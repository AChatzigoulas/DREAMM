"""Run a standalone workflow with its installed runtime and local coordinates."""
import argparse
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

from dreamm_m1.capture import _json, _parents, _read
from dreamm_m1.identity import canonical, digest, require, sha
from dreamm_m1.local_budget import UNLIMITED, source_bytes

PDB_ID = re.compile(r'[1-9][A-Za-z0-9]{3}', flags=re.ASCII)
RCSB = 'files.rcsb.org'
REQUIRED = ('application', 'runtime_root', 'runtime_manifest', 'runtime_sha256',
            'database', 'database_manifest')
ENSEMBLE = ('ensemble_runtime', 'ensemble_sha256', 'extensions', 'extensions_sha256')
GENERATION = ('generation_runtime', 'generation_sha256')


def official_url(url):
    parts = urlsplit(url)
    return (parts.scheme == 'https' and parts.netloc == RCSB
            and re.fullmatch(r'/download/[1-9][A-Za-z0-9]{3}\.cif', parts.path)
            and not parts.query and not parts.fragment)


class PdbRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(official_url(newurl) and newurl.upper() == req.full_url.upper(),
                'SOURCE_ACQUISITION_FAILED', 'unexpected PDB download redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def acquire(pdb_id):
    require(isinstance(pdb_id, str) and PDB_ID.fullmatch(pdb_id),
            'SOURCE_INVALID', 'four-character PDB ID required')
    url = f'https://{RCSB}/download/{pdb_id.upper()}.cif'
    request = Request(url, headers={'Accept': 'text/plain,application/octet-stream',
                                    'Accept-Encoding': 'identity', 'User-Agent': 'DREAMM/1.1'})
    with build_opener(PdbRedirect()).open(request, timeout=60) as response:
        require(response.status == 200 and official_url(response.url)
                and response.url.upper() == url.upper(),
                'SOURCE_ACQUISITION_FAILED', 'unexpected PDB response')
        limit = source_bytes()
        raw = response.read() if limit == UNLIMITED else response.read(limit + 1)
        require(0 < len(raw) <= source_bytes() and raw.lstrip().startswith(b'data_'),
                'SOURCE_ACQUISITION_FAILED', 'download is empty, oversized or not an mmCIF entry')
        return raw, {'url': url, 'etag': response.headers.get('ETag'),
                     'last_modified': response.headers.get('Last-Modified')}


def get_input(value, mode):
    from .cli import read_input
    path = Path(value)
    if not path.exists() and PDB_ID.fullmatch(value) and mode in ('M1', 'M3', 'M5'):
        raw, origin = acquire(value)
        return raw, 'mmcif', value.upper(), origin
    fmt = {'.cif':'mmcif', '.mmcif':'mmcif', '.pdb':'pdb', '.ent':'pdb'}.get(path.suffix.lower())
    require(fmt, 'SOURCE_INVALID', 'supply a local .pdb/.ent/.cif/.mmcif file'
            + (' or a four-character PDB accession' if mode in ('M1', 'M3', 'M5') else ''))
    raw = read_input(path, source_bytes())
    return raw, fmt, None, {'local_name': path.name}


def config_path():
    if os.environ.get('DREAMM_CONFIG'):
        return Path(os.environ['DREAMM_CONFIG'])
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'dreamm'/'config.json'


def settings(path, mode):
    path = Path(path or config_path()).absolute()
    require(path.is_file(),'INSTALL_REQUIRED',
            f'No DREAMM configuration found at {path}. Run ./install.sh in the DREAMM source directory '
            'or select an existing configuration with dreamm run --config FILE.')
    _parents(path.parent)
    info=path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077,
            'INSTALL_INVALID','configuration must be a private file owned by the Linux user')
    cfg = _json(_read(path, 32768))
    require(cfg.get('schema') == 'dreamm-standalone-install/1',
            'INSTALL_INVALID', 'unsupported configuration')
    needed = REQUIRED + (ENSEMBLE if mode in ('M3','M4','M5') else ()) + (GENERATION if mode == 'M5' else ())
    result = {}
    for key in needed:
        value = cfg.get(key)
        require(isinstance(value,str) and value, 'INSTALL_REQUIRED',
                f'{mode} needs the full installation. Run ./install.sh --profile full in the DREAMM source directory.'
                if key in ENSEMBLE+GENERATION else 'The basic installation is incomplete. Rerun ./install.sh in the DREAMM source directory.')
        result[key] = sha(value) if key.endswith('_sha256') else str(Path(value).absolute())
        require(key.endswith('_sha256') or Path(value).is_absolute(),
                'INSTALL_INVALID', 'configuration contains a relative runtime path')
    return result


def run(args):
    from .cli import _run_worker, check_selection, write_private, sync_directory
    from .process import requested_budget
    from .exports import export as export_results
    from .reference_export import export as export_reference
    require(args.execution == 'trusted-local' and args.runtime_profile == 'modern'
            and not any(getattr(args,name,None) for name in
                ('input','sha256','pdb_id','runtime_root','runtime_manifest','runtime_sha256',
                 'database','database_manifest','application','source_packet','cgroup_root',
                 'installation','installation_sha256','launcher','launcher_sha256',*ENSEMBLE,*GENERATION)),
            'REQUEST_INVALID', 'use -i with the installed standalone configuration, or use explicit expert runtime options without -i')
    chains = [chain for value in (args.chains or ()) for chain in value.split(',')] or None
    args.chains = chains
    require(args.seed is None or (args.mode=='M5' and 0<=args.seed<2**64),
            'REQUEST_INVALID','a UInt64 seed is available only for M5')
    require(not any(getattr(args,name,None) for name in ('reference_checkpoint','reference_sha256',
                'generation_checkpoint','generation_checkpoint_sha256')) or args.mode in ('M3','M4','M5'),
            'REQUEST_INVALID','these checkpoints belong to an ensemble mode')
    from .modern_pipeline import SECONDS
    from .cli import stage_timeouts
    ensemble=args.mode in ('M3','M4','M5')
    stage_timeouts(args,{**SECONDS, 'inference':180, **({'generation':1200,'essa':600,'p2rank':1800} if ensemble else {})})
    with requested_budget(args):
        raw, fmt, pdb_id, origin = get_input(args.source,args.mode)
        check_selection(args.mode,fmt,pdb_id,chains,args.additional,trusted_input=True)
        cfg=settings(args.config,args.mode)
        values = {**vars(args), **cfg, 'source_format':fmt, 'sha256':digest(raw),'pdb_id':pdb_id}
        output=Path(args.output or f'DREAMM-{pdb_id or args.mode}-{uuid.uuid4().hex[:8]}').absolute()
        _parents(output.parent)
        output.mkdir(mode=0o700)
        write_private(output/'input-provenance.json',canonical({'mode':args.mode,'source_sha256':values['sha256'],
            'format':fmt,'bytes':len(raw),'pdb_id':pdb_id,'chains':chains,'acquisition':origin,
            'runtime_sha256':cfg['runtime_sha256']}))
        with tempfile.TemporaryDirectory(prefix='dreamm-source-') as scratch:
            source=Path(scratch)/('source.cif' if fmt=='mmcif' else 'source.pdb')
            write_private(source,raw)
            values.update(input=str(source),output=str(output/'attempt'))
            if args.mode in ('M1','M2'):
                summary=_run_worker(argparse.Namespace(**values))
                result=export_results(argparse.Namespace(attempt=values['output'],output=output/'exports'))
            else:
                require(bool(args.reference_checkpoint)==bool(args.reference_sha256),'REQUEST_INVALID',
                        'a supplied expert reference needs its exact checkpoint checksum')
                if not args.reference_checkpoint:
                    reference=output/'reference-attempt'
                    start=time.monotonic()
                    _run_worker(argparse.Namespace(**{**values,'mode':'M1' if fmt=='mmcif' else 'M2',
                        'additional':None,'seed':None,'chimera':False,'output':str(reference),
                        'stage_timeout':[item for item in args.stage_timeout
                                         if item.partition('=')[0] not in ('generation','essa','p2rank')]}))
                    receipt=export_reference(argparse.Namespace(attempt=reference,output=output/'reference'))
                    require(int(time.monotonic()-start)<args.timeout,'RESOURCE_LIMIT','job deadline reached during reference')
                    values.update(reference_checkpoint=receipt['output'],reference_sha256=receipt['sha256'],
                                  timeout=args.timeout-int(time.monotonic()-start))
                values['stage_timeout']=[item for item in args.stage_timeout
                                          if item.partition('=')[0] in ('generation','essa','p2rank')]
                summary=_run_worker(argparse.Namespace(**values))
                result={'archive':str(output/'attempt'/'results'/'DREAMM-ensemble.zip')}
        final={'completed':True,'output':str(output),'prediction':summary,'exports':result}
        write_private(output/'.complete.pending',canonical(final))
        os.rename(output/'.complete.pending',output/'complete.json')
        sync_directory(output)
        return final
