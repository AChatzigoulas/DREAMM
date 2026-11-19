"""Local M1/M2 execution and publication, for Linux researcher-owned inputs."""
import argparse
import os
from pathlib import Path
import sys
import uuid

from dreamm_m1.capture import _json, _read, _parents
from dreamm_m1.identity import canonical, digest, require
from dreamm_m1.local_budget import source_bytes
from dreamm_m1.rocky_database import PREFIX

from .cli import check_selection, prepare, read_input, stage_timeouts, write_private, sync_directory
from .runtime import runtime_bundle


def validate_results(work,config):
    from dreamm_m1.inference import residue_rows,verify_membrane_reference
    from dreamm_m1.mmcif_input import CIF_1RLW_SHA256
    from dreamm_m1.pdb_input import PDB_1RLW_SHA256
    base=work/'scientific';extraction=base/'extraction';prediction=base/'prediction'
    for name in ('adapter_map.json','unscaled.json','join.json','prepared.pdb','charges.pqr','python-features.csv',
            'output.hhm','query.json','conservation.json','descriptors/Outputs/reference/complete_AA.txt','checkpoint.json'):
        require((extraction/name).is_file(),'LOCAL_OUTPUT_INVALID','missing scientific artifact: '+name)
    for name in ('membrane.csv','scaling.json','labels.json','bridge.json','complete.json'):
        require((prediction/name).is_file(),'LOCAL_OUTPUT_INVALID','missing inference artifact: '+name)
    residues=residue_rows({'adapter_map':_read(extraction/'adapter_map.json')})
    membrane=_read(prediction/'membrane.csv')
    calls,canonical_calls=verify_membrane_reference(membrane,residues)
    labels=_json(_read(prediction/'labels.json'))
    eligible=[r['residue_uid'] for r in _json(_read(extraction/'unscaled.json'))['rows']]
    require(0<len(residues) and len(eligible)==len(labels) and set(eligible)==set(labels)
            and set(labels)<=residues.keys() and all(type(v) is int and v in (0,1) for v in labels.values())
            and canonical_calls==membrane and all(labels.get(uid)==1 for uid in calls),
            'LOCAL_OUTPUT_INVALID','reference residue/eligible coverage differs')
    if not config.get('chains') and config['source_sha256'] in (CIF_1RLW_SHA256,PDB_1RLW_SHA256):
        require(len(residues)==126 and len(labels)==95,'LOCAL_OUTPUT_INVALID','1RLW control coverage differs')
    summary={'schema':'dreamm-local-prediction/1','native_complete':True,'attempt':config['attempt'],
        'mode':config.get('mode','M1'),
        'source_sha256':config['source_sha256'],'chain_selection':config.get('chains'),
        'runtime_profile':'modern','execution_boundary':'trusted-local',
        'fresh_feature_extraction':True,'residues':len(residues),'eligible_residues':len(labels),
        'membrane_calls':len(calls),'outcome':'complete_positive' if calls else 'complete_scientific_empty'}
    write_private(work/'prediction-summary.json', canonical(summary))
    manifest={'schema':'dreamm-trusted-artifacts/1','attempt':config['attempt'],
        'source_sha256':config['source_sha256'],'runtime_sha256':config['runtime_sha256'],
        'application_sha256':config['application_sha256'],'files':{}}
    for path in sorted(work.rglob('*')):
        if path.is_dir():continue
        raw=_read(path);manifest['files'][path.relative_to(work).as_posix()]={'bytes':len(raw),'sha256':digest(raw)}
    return summary,manifest


def run(args):
    from .database_receipt import verify
    from .modern_pipeline import SECONDS, admit_reference
    from .process import launch, python_command

    fmt = getattr(args, 'source_format', None) or ('mmcif' if args.mode == 'M1' else 'pdb')
    chains = getattr(args, 'chains', None)
    check_selection(args.mode, fmt, args.pdb_id, chains, None, trusted_input=True)
    raw = read_input(args.input, source_bytes())
    require(digest(raw) == args.sha256, 'SOURCE_INVALID', 'source SHA-256 differs')
    if fmt == 'pdb':
        admit_reference(raw)
    runtime = Path(args.runtime_root).absolute()
    manifest = Path(args.runtime_manifest).absolute()
    bundle = runtime_bundle(_read(manifest), expected_sha256=args.runtime_sha256)
    require(bundle.schema == 'dreamm-modern-runtime/1', 'RUNTIME_INVALID', 'modern runtime required')
    bundle.verify(runtime)
    database = Path(args.database).absolute()
    verified = verify(database, _json(_read(Path(args.database_manifest))),
        runtime_sha256=bundle.sha256, installation=manifest.parent/'installation.json',
        state=manifest.parent/'database-verification.json', force=args.full_database_verify)
    verified = {key: value for key, value in verified.items() if key != 'mount'}
    verified['path'] = str(database)
    output = Path(args.output).absolute()
    _parents(output.parent)
    output.mkdir(mode=0o700)
    prepare(argparse.Namespace(mode=args.mode, format=fmt,
        pdb_id=args.pdb_id, chains=chains, additional=None, input=args.input,
        sha256=args.sha256, output=output/'request', trusted_input=True), raw=raw)
    work = output/'failed-artifacts'
    work.mkdir(mode=0o700)
    app = Path(args.application or sys.argv[0]).absolute()
    config = {'attempt': uuid.uuid4().hex, 'mode': args.mode, 'chains': args.chains, 'source_sha256': args.sha256,
              'runtime_sha256': bundle.sha256, 'runtime': str(runtime), 'runtime_manifest': str(manifest),
              'database_prefix': str(database/PREFIX), 'database_verification': verified,
              'application_sha256': digest(_read(app)), 'stage_deadlines': stage_timeouts(args, {**SECONDS,'inference':180})}
    receipt = launch(args, output, work, python_command(runtime/bundle.data['roles']['science_python'],
                    app, 'dreamm.trusted', 'scientific_main'), config, threads=16)
    summary, artifacts = validate_results(work, config)
    write_private(output/'artifacts.json', canonical(artifacts))
    os.rename(work, output/'results')
    write_private(output/'.complete.pending', canonical({'summary':summary,'supervision':receipt,
                  'artifact_manifest_sha256':digest(canonical(artifacts))}))
    os.rename(output/'.complete.pending', output/'complete.json')
    sync_directory(output)
    return {'completed':True,'output':str(output),'prediction':summary,'supervision':receipt}


def scientific_main():
    from .cli import compatibility
    from .modern_pipeline import run_reference
    from .modern_runtime import enable_imports
    from .process import read_run

    with read_run() as config:
        runtime = config['runtime']
        bundle = runtime_bundle(_read(Path(config['runtime_manifest'])), expected_sha256=config['runtime_sha256'])
        enable_imports(runtime)
        run_reference(Path(config['work']).parent/'request', bundle=bundle, runtime_root=runtime,
            profiles=None, database_prefix=config['database_prefix'],
            database_verification=config['database_verification'], work=Path(config['work'])/'scientific',
            profile_bytes=compatibility(), execution='trusted-local', stage_deadlines=config['stage_deadlines'])
