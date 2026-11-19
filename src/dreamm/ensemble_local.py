"""Standalone downstream execution from complete, explicitly frozen scientific inputs.

This command reuses M1 and P2Rank checkpoints; it never presents reused inputs
as newly executed preparation, model inference or pocket prediction.
"""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

from dreamm_m1.capture import _json,_read,_parents
from dreamm_m1.identity import AA,MODEL_SHA256,canonical,digest,require
from dreamm_m1.local_budget import artifact_bytes, artifact_count, local_budget, model_count, scratch_bytes, source_bytes, trusted_local
from dreamm_m1.inference import verify_membrane_reference
from dreamm_m1.pdb_input import read_pdb_source
from .cli import write_private,sync_directory
from .runtime import relative_path,runtime_bundle


def retain_failure(work,target):
    """Keep bounded regular diagnostics without following transient tool links."""
    target.mkdir(mode=0o700);skipped=[];total=0;count=0
    for directory,folders,names in os.walk(work,followlinks=False):
        linked=[n for n in folders if (Path(directory)/n).is_symlink()]
        folders[:]=[n for n in folders if n not in linked]
        skipped.extend({'path':str((Path(directory)/n).relative_to(work)),'reason':'linked directory'} for n in linked)
        for name in names:
            source=Path(directory)/name;relative=source.relative_to(work)
            try:
                info=source.lstat();count+=1
                if (not stat.S_ISREG(info.st_mode) or info.st_size>artifact_bytes()
                        or total+info.st_size>scratch_bytes() or count>artifact_count()):
                    skipped.append({'path':str(relative),'mode':oct(info.st_mode),'bytes':info.st_size});continue
                raw=_read(source);total+=len(raw);destination=target/relative
                destination.parent.mkdir(parents=True,exist_ok=True);write_private(destination,raw)
            except (OSError,ValueError) as exc:skipped.append({'path':str(relative),'reason':str(exc)})
        if count>artifact_count():break
    write_private(target/'failure-capture.json',canonical({'regular_bytes_retained':total,'excluded':skipped,
        'incomplete_scientific_attempt':True,'links_followed':False}))


def inventory(root,manifest,*,limit=None):
    if limit is None:
        limit = scratch_bytes() if trusted_local() else 128*1024**2
    root=Path(root);_parents(root)
    rows=manifest['files'];require(0<len(rows)<=artifact_count(),'ENSEMBLE_CHECKPOINT_INVALID','file inventory bound')
    paths=list(root.rglob('*'))
    require(not any(p.is_symlink() for p in paths),'ENSEMBLE_CHECKPOINT_INVALID','linked checkpoint member')
    require({p.relative_to(root).as_posix() for p in paths if not p.is_dir()}==set(rows)|{'checkpoint.json'},
            'ENSEMBLE_CHECKPOINT_INVALID','unexpected or missing checkpoint member')
    total=0
    for name,pin in rows.items():
        relative_path(name);raw=_read(root/name);total+=len(raw)
        require(total<=limit and pin=={'bytes':len(raw),'sha256':digest(raw)},
                'ENSEMBLE_CHECKPOINT_INVALID','checkpoint content differs: '+name)
    return rows


def reference(root):
    root=Path(root);receipt=_json(_read(root/'reference-membrane-and-conservation.json'))
    raw=_read(root/'source-model-0.pdb')
    require(receipt['model_sha256']==MODEL_SHA256 and receipt['source_sha256']==digest(raw),
            'ENSEMBLE_MAPPING_UNPROVEN','reference source/model lineage differs')
    source=read_pdb_source(raw,expected_sha256=digest(raw))
    from .ensemble_source import ca_atoms
    atoms=list(ca_atoms(source))
    expected={a.residue.uid:(a.locator.chain,a.locator.residue_label,aa) for a, aa in atoms}
    require(len(expected)==len(atoms),
            'ENSEMBLE_MAPPING_UNPROVEN','ambiguous or unsupported reference residue identity')
    rows={r['uid']:r for r in receipt['predictions']}
    require(len(rows)==len(receipt['predictions']) and {u:tuple(r['identity']) for u,r in rows.items()}==expected
            and all(math.isfinite(r['conservation']) and r['raw_label'] in (0,1,None) for r in rows.values()),
            'ENSEMBLE_MAPPING_UNPROVEN','incomplete reference labels or Conservation')
    membrane=_read(root/'reference-membrane.csv');flags,encoded=verify_membrane_reference(membrane,expected)
    require(encoded==membrane and len(flags)==receipt['membrane_calls']
            and sum(r['raw_label'] is not None for r in rows.values())==receipt['eligible_residues']
            and all(r['broken_chain']==flags.get(u) and (u not in flags or r['raw_label']==1) for u,r in rows.items()),
            'ENSEMBLE_MAPPING_UNPROVEN','reference eligibility, calls or terminal flags differ')
    result={f'{chain}_{number}':{'uid':uid,'aa':aa,'conservation':rows[uid]['conservation'],
             'membrane':uid in flags,'broken_chain':flags.get(uid)} for uid,(chain,number,aa) in expected.items()}
    require(len(result)==len(expected),'ENSEMBLE_MAPPING_UNPROVEN','colliding residue locators')
    return result


def checkpoint(root,expected):
    root=Path(root);raw=_read(root/'checkpoint.json');require(digest(raw)==expected,
        'ENSEMBLE_CHECKPOINT_INVALID','manifest checksum differs')
    data=_json(raw);require(data['schema']=='dreamm-ensemble-analysis-input/1' and data['mode'] in ('M3','M4','M5')
        and data['p2rank_version']=='2.5.1' and data['profile'] in ('default','alphafold'),
        'ENSEMBLE_CHECKPOINT_INVALID','unsupported scientific checkpoint')
    inventory(root,data);reference(root)
    frames=data['frames']
    require(1<=len(frames)<=(51 if data['mode']=='M5' else max(model_count(),51))
        and [f['index'] for f in frames]==list(range(len(frames)))
        and len({f['name'] for f in frames})==len(frames), 'ENSEMBLE_CHECKPOINT_INVALID','missing/duplicate frame')
    for frame in frames:
        name=frame['name'];require(name==f"frame-{frame['index']:02d}.pdb",
            'ENSEMBLE_CHECKPOINT_INVALID','unexpected frame filename')
        require(data['files']['frames/'+name]['sha256']==frame['sha256'],
            'ENSEMBLE_CHECKPOINT_INVALID','frame identity differs')
        for suffix in ('_predictions.csv','_residues.csv','_points.pdb.gz'):
            require('raw/'+data['profile']+'/'+name+suffix in data['files'],
                    'ENSEMBLE_CHECKPOINT_INVALID','incomplete pocket output')
    return data


def run(args):
    from .cli import stage_timeouts
    from .trusted import MEMORY,RESERVE,NAMESPACES,available_memory,namespace_command,supervise
    require(sys.platform=='linux' and os.geteuid()>0 and sys.flags.isolated,
            'LOCAL_UNAVAILABLE','isolated non-root Linux execution required')
    fresh=getattr(args,'fresh_ensemble',False)
    if fresh:
        from .ensemble_run import native_input,verify_p2rank
        data,payload=native_input(args)
        verify_p2rank(args.ensemble_runtime,args.ensemble_sha256)
        if args.mode=='M5':
            from .generation import verify_runtime
            verify_runtime(args.generation_runtime,args.generation_sha256)
    else:
        data=checkpoint(args.checkpoint,args.sha256)
        payload={name:_read(Path(args.checkpoint)/name) for name in data['files']}
    bundle=runtime_bundle(_read(Path(args.runtime_manifest)),expected_sha256=args.runtime_sha256)
    require(bundle.schema=='dreamm-modern-runtime/1','RUNTIME_INVALID','modern runtime required')
    bundle.verify(args.runtime_root)
    extra=Path(args.extensions).absolute();meta=_read(extra/'checkpoint.json')
    require(digest(meta)==args.extensions_sha256,'RUNTIME_INVALID','extension receipt checksum differs')
    inventory(extra,_json(meta),limit=256*1024**2)
    require(1<=args.timeout<=(86400 if fresh else 7200),'RESOURCE_LIMIT','bounded scientific deadline required')
    deadlines=stage_timeouts(args,{'generation':1200,'essa':600})
    output=Path(args.output).absolute();_parents(output.parent);output.mkdir(mode=0o700)
    fd=os.open('/tmp/dreamm-trusted-'+str(os.getuid())+'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        require(os.fstat(fd).st_uid==os.getuid(),'LOCAL_ADMISSION_FAILED','worker lock owner differs')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(available_memory()>=MEMORY+RESERVE,'RESOURCE_LIMIT','14 GiB available RAM required')
        app=Path(args.application or sys.argv[0]).absolute()
        require(app.suffix=='.pyz','LOCAL_ADMISSION_FAILED','built zipapp required')
        with tempfile.TemporaryDirectory(prefix='dreamm-ensemble-') as temp:
            temp=Path(temp);inputs=temp/'inputs';inputs.mkdir(mode=0o700);work=temp/'work';work.mkdir(mode=0o700)
            require(shutil.disk_usage(temp).free>=2*scratch_bytes() and shutil.disk_usage(output).free>=scratch_bytes(),
                    'RESOURCE_LIMIT','free space must cover scratch and retained output')
            staged=inputs/'data';staged.mkdir(mode=0o700)
            for name,pin in data['files'].items():
                raw=payload[name]
                require(pin=={'bytes':len(raw),'sha256':digest(raw)},'ENSEMBLE_CHECKPOINT_INVALID','input changed during staging')
                path=staged/name;path.parent.mkdir(parents=True,exist_ok=True);write_private(path,raw)
            write_private(staged/'checkpoint.json',canonical(data))
            config={'schema':'dreamm-trusted-admission/1','attempt':uuid.uuid4().hex,'trusted_input_only':True,
                'memory':MEMORY,'scratch':scratch_bytes(),'runtime_sha256':bundle.sha256,'stage_deadlines':deadlines,
                'application_sha256':digest(_read(app)),'checkpoint_sha256':digest(canonical(data)),
                'extensions_sha256':args.extensions_sha256,'host_namespaces':{n:os.readlink('/proc/self/ns/'+n) for n in NAMESPACES},
                'source_bytes':source_bytes(),'artifact_bytes':artifact_bytes()}
            write_private(inputs/'admission.json',canonical(config));write_private(inputs/'runtime.json',canonical(bundle.data))
            write_private(output/'admission.json',canonical(config))
            if fresh:
                config['p2rank_sha256']=args.ensemble_sha256
                if args.mode=='M5':config['generation_sha256']=args.generation_sha256
                # Rewrite only this not-yet-launched private admission.
                (inputs/'admission.json').write_bytes(canonical(config));(output/'admission.json').write_bytes(canonical(config))
            launcher=Path(getattr(args,'launcher',None) or '/usr/bin/bwrap').absolute()
            require(launcher.is_file() and not launcher.stat().st_mode&0o022,'LOCAL_ADMISSION_FAILED',
                    'trusted non-writable bubblewrap required')
            pin=getattr(args,'launcher_sha256',None)
            if pin:require(digest(_read(launcher,1024**2))==pin,'LOCAL_ADMISSION_FAILED','launcher differs')
            argv=namespace_command(launcher,Path(args.runtime_root).absolute(),app,inputs,work,extra,
                                   entry='dreamm.ensemble_run' if fresh else 'dreamm.ensemble_local')
            if fresh:
                (work/'ccd-cache').mkdir(mode=0o700)
                write_private(inputs/'hosts',b'127.0.0.1 localhost dreamm-local\n::1 localhost dreamm-local\n')
                write_private(inputs/'nsswitch.conf',b'hosts: files\n')
                pos=argv.index('--remount-ro');argv[pos:pos]=['--ro-bind',str(Path(args.ensemble_runtime).absolute()),'/p2rank',
                    '--ro-bind',str(Path(args.ensemble_runtime).absolute()/'ccd/chemcomp'),'/work/ccd-cache/chemcomp',
                    '--hostname','dreamm-local','--ro-bind',str(inputs/'hosts'),'/etc/hosts',
                    '--ro-bind',str(inputs/'nsswitch.conf'),'/etc/nsswitch.conf']
                if args.mode=='M5':
                    pos=argv.index('--remount-ro')
                    argv[pos:pos]=['--ro-bind',str(Path(args.generation_runtime).absolute()),'/generation',
                        '--ro-bind','/usr/bin','/usr/bin','--symlink','usr/bin','/bin']
                    for directory in ('/usr/share/fonts','/etc/fonts'):
                        if Path(directory).exists():argv[pos:pos]=['--ro-bind',directory,directory]
            for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS'):argv[argv.index(key)+1]='1'
            try:
                cpus=getattr(args,'processes',None)
                require(cpus is None or 1<=cpus<=15,'RESOURCE_LIMIT','CPU affinity bound must be 1..15')
                receipt=supervise(argv,work,output,args.timeout,cpu_processes=cpus or 15)
                require((work/'scientific-complete.json').is_file(),'ENSEMBLE_INCOMPLETE','mandatory stages did not finish')
                shutil.copytree(work,output/'results')
                write_private(output/'complete.json',canonical({'scientific':_json(_read(work/'scientific-complete.json')),
                              'supervision':receipt}))
                sync_directory(output)
            except BaseException:
                retain_failure(work,output/'failed-artifacts');raise
        return _json(_read(output/'complete.json'))
    finally:os.close(fd)


def essa_child(config):
    from .modern_runtime import enable_imports,verify_python_versions
    enable_imports('/runtime',stage='dynamics');verify_python_versions('dynamics')
    from prody import ESSA,parsePDB
    model=ESSA();model.setSystem(parsePDB(config['input'],low_memory=False),lowmem=True)
    from dreamm_m1.identity import residue_label
    ca=model._ca
    keys=[f'{c}_{residue_label(n, i)}' for c,n,i in zip(ca.getChids(),ca.getResnums(),ca.getIcodes())]
    require(len(keys)==len(set(keys)) and dict(zip(keys,map(str,ca.getResnames())))==config['residues'],
            'ENSEMBLE_MAPPING_UNPROVEN','ESSA residue map differs')
    model.scanResidues();values=list(map(float,model.getESSAZscores()))
    require(len(values)==len(keys) and all(map(math.isfinite,values)),'ENSEMBLE_INCOMPLETE','invalid ESSA result')
    write_private(Path(config['output']),canonical(dict(zip(keys,values))))


def main():
    if sys.argv[1:2]==['essa']:
        require(len(sys.argv)==3,'ENSEMBLE_INPUT_INVALID','fixed ESSA request required')
        essa_child(_json(_read(Path(sys.argv[2]))));return
    config=_json(_read(Path('/inputs/admission.json')))
    if config.get('artifact_bytes') != 8*1024**2:
        with local_budget(config['source_bytes'],config['artifact_bytes'],config['scratch']):
            return replay_worker(config)
    return replay_worker(config)


def replay_worker(config):
    from .trusted_entry import environment
    from .modern_runtime import enable_imports,verify_python_versions
    environment(config)
    data=checkpoint('/inputs/data',config['checkpoint_sha256'])
    require(digest(_read(Path('/database/checkpoint.json')))==config['extensions_sha256'],
            'RUNTIME_INVALID','extension manifest changed')
    inventory('/database',_json(_read(Path('/database/checkpoint.json'))),limit=256*1024**2)
    enable_imports('/runtime');verify_python_versions('science');sys.path.append('/database/xlsx')
    import importlib.metadata
    require(importlib.metadata.version('openpyxl')=='3.1.5' and importlib.metadata.version('et-xmlfile')=='2.0.0',
            'RUNTIME_INVALID','XLSX extension versions differ')
    analyze(data,Path('/inputs/data'),config)


def analyze(data,root,config,*,reused_p2rank=True,spans=None):
    from . import ensemble_analysis as analysis
    if spans is None:spans=[]
    frames=data['frames'];profile=data['profile'];ref=reference(root);analysis.INPUT=root
    Path('/work/tmp').mkdir(exist_ok=True);analysis.EXPECTED_POCKETS={profile:data['pocket_count']}
    geometries=[analysis.frame_geometry(f,ref) for f in frames]
    def essa(frame,geo):
        request=Path('/work')/('essa-'+frame['name']+'.json');output=request.with_suffix('.scores.json')
        write_private(request,canonical({'input':str(geo['file']),'residues':geo['ca'],'output':str(output)}))
        saved=data.get('essa_checkpoints',{}).get(str(frame['index']))
        if saved is not None:
            require(saved['frame_sha256']==frame['sha256'] and saved['runtime_sha256']==config['runtime_sha256']
                and saved['method']=='prody-2.6.1-ESSA-lowmem-scanResidues-default-one-thread/1'
                and saved['path'] in data['files'],'ENSEMBLE_CHECKPOINT_INVALID','ESSA checkpoint inputs differ')
            raw=_read(root/saved['path']);values=_json(raw)
            require(set(values)==set(geo['ca']) and all(math.isfinite(v) for v in values.values()),
                    'ENSEMBLE_CHECKPOINT_INVALID','ESSA score coverage differs')
            write_private(output,raw);return values
        with request.with_suffix('.stdout').open('xb') as out,request.with_suffix('.stderr').open('xb') as err:
            subprocess.run(['/runtime/dynamics/bin/python3.14','-I','-S','-B','-c',
                "import sys;sys.path.insert(0,'/code/dreamm.pyz');from dreamm.ensemble_local import main;main()",
                'essa',str(request)],stdin=subprocess.DEVNULL,stdout=out,stderr=err,check=True,
                timeout=config.get('stage_deadlines',{}).get('essa',600))
        return _json(_read(output))
    analysis.essa=essa
    target=Path('/work/exports');target.mkdir()
    try:
        result=analysis.evaluate(profile,frames,geometries,ref,{},spans,target)
    finally:
        write_private(Path('/work/analysis-timings.json'),canonical(spans))
    for name in ('reference-membrane.csv','reference-membrane-and-conservation.json','source-model-0.pdb','checkpoint.json'):
        shutil.copyfile(root/name,target/name)
    for name in ('original-source.cif','original-source.pdb','source-manifest.json','generation-checkpoint.json'):
        if (root/name).is_file():shutil.copyfile(root/name,target/name)
    if not reused_p2rank:
        for name in ('source-frames','prepared','aligned'):
            shutil.copytree(Path('/work')/name,target/'structures'/name)
    if data['mode']=='M5' and Path('/work/generation/generation-checkpoint.json').is_file():
        shutil.copytree('/work/generation/output',target/'generation/native')
        for name in ('reference.pdb','polar.pdb','polar.dssp','run.jl','exprose.invocation.json'):
            shutil.copyfile(Path('/work/generation')/name,target/'generation'/name)
        shutil.copyfile('/work/generation/generation-checkpoint.json',target/'generation/checkpoint.json')
    write_private(target/'timings.json',canonical(spans));archive=analysis.bundle(target)
    index={p.relative_to('/work').as_posix():{'sha256':digest(_read(p)),'bytes':p.stat().st_size}
           for p in Path('/work').rglob('*') if p.is_file() and p.relative_to('/work').parts[0]!='ccd-cache'}
    write_private(Path('/work/scientific-complete.json'),canonical({'schema':'dreamm-standalone-ensemble/1',
        'mode':data['mode'],'profile':profile,'p2rank':'2.5.1','input_checkpoint_sha256':config['checkpoint_sha256'],
        'M1_reference_reused':True,'P2Rank_outputs_reused':reused_p2rank,'fresh_downstream_analysis':True,
        'frames':len(frames),'membrane_calls':sum(r['membrane'] for r in ref.values()),'sites':len(result['sites']),
        'outcome':result['status'],'zip':archive,'files':index,'ensemble_method':'modern-downstream/1'}))


if __name__=='__main__':main()
