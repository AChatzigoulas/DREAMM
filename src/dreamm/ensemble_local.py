"""Standalone downstream execution from complete, explicitly frozen scientific inputs.

This command reuses M1 and P2Rank checkpoints; it never presents reused inputs
as newly executed preparation, model inference or pocket prediction.
"""
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from dreamm_m1.capture import _json,_read,_parents
from dreamm_m1.identity import AA,MODEL_SHA256,canonical,digest,require
from dreamm_m1.local_budget import artifact_count, local_budget, model_count, scratch_bytes, trusted_local
from dreamm_m1.inference import verify_membrane_reference
from dreamm_m1.pdb_input import read_pdb_source
from .cli import write_private,sync_directory
from .runtime import relative_path,runtime_bundle


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
    from .process import launch, python_command

    fresh = getattr(args, 'fresh_ensemble', False)
    if fresh:
        from .ensemble_run import native_input, verify_p2rank
        data, payload = native_input(args)
        verify_p2rank(args.ensemble_runtime, args.ensemble_sha256)
        if args.mode == 'M5':
            from .generation import verify_runtime
            generation = verify_runtime(args.generation_runtime, args.generation_sha256)
            require(not args.chimera or generation.get('chimera'), 'GENERATION_INVALID',
                    '--chimera needs an explicitly installed Chimera fallback')
    else:
        data = checkpoint(args.checkpoint, args.sha256)
        payload = {name:_read(Path(args.checkpoint)/name) for name in data['files']}
    runtime = Path(args.runtime_root).absolute()
    bundle = runtime_bundle(_read(Path(args.runtime_manifest)), expected_sha256=args.runtime_sha256)
    require(bundle.schema == 'dreamm-modern-runtime/1', 'RUNTIME_INVALID', 'modern runtime required')
    bundle.verify(runtime)
    extra = Path(args.extensions).absolute()
    meta = _read(extra/'checkpoint.json')
    require(digest(meta) == args.extensions_sha256, 'RUNTIME_INVALID', 'extension receipt checksum differs')
    inventory(extra, _json(meta), limit=256*1024**2)
    output = Path(args.output).absolute()
    _parents(output.parent)
    output.mkdir(mode=0o700)
    work, staged = output/'failed-artifacts', output/'inputs'
    work.mkdir(mode=0o700)
    staged.mkdir(mode=0o700)
    for name,pin in data['files'].items():
        raw = payload[name]
        require(pin == {'bytes':len(raw),'sha256':digest(raw)}, 'ENSEMBLE_CHECKPOINT_INVALID', 'input changed during staging')
        path = staged/name
        path.parent.mkdir(parents=True,exist_ok=True)
        write_private(path, raw)
    write_private(staged/'checkpoint.json', canonical(data))
    app = Path(args.application or sys.argv[0]).absolute()
    config = {'runtime':str(runtime), 'runtime_sha256':bundle.sha256, 'roles':bundle.data['roles'],
              'application':str(app), 'extensions':str(extra), 'inputs':str(staged),
              'checkpoint_sha256':digest(canonical(data)), 'fresh':fresh,
              'stage_deadlines':stage_timeouts(args, {'generation':1200,'essa':600,'p2rank':1800})}
    if fresh:
        config.update(p2rank=str(Path(args.ensemble_runtime).absolute()), p2rank_sha256=args.ensemble_sha256)
        if args.mode == 'M5':
            config.update(generation=str(Path(args.generation_runtime).absolute()),
                          generation_sha256=args.generation_sha256, chimera=args.chimera)
    receipt = launch(args, output, work, python_command(runtime/bundle.data['roles']['science_python'],
                     app, 'dreamm.ensemble_local', 'main'), config, threads=1)
    require((work/'scientific-complete.json').is_file(), 'ENSEMBLE_INCOMPLETE', 'mandatory stages did not finish')
    result = _json(_read(work/'scientific-complete.json'))
    os.rename(work, output/'results')
    write_private(output/'.complete.pending', canonical({'scientific':result, 'supervision':receipt}))
    os.rename(output/'.complete.pending', output/'complete.json')
    sync_directory(output)
    return _json(_read(output/'complete.json'))


def essa_child(config):
    from .modern_runtime import enable_imports,verify_python_versions
    enable_imports(config['runtime'],stage='dynamics');verify_python_versions('dynamics')
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
    from .modern_runtime import enable_imports, verify_python_versions
    from .process import read_run
    import importlib.metadata

    if sys.argv[1:2] == ['essa']:
        with local_budget(*map(int, os.environ['DREAMM_LOCAL_BUDGET'].split(':'))):
            return essa_child(_json(_read(Path(sys.argv[2]))))
    with read_run() as config:
        enable_imports(config['runtime'])
        verify_python_versions('science')
        sys.path.append(str(Path(config['extensions'])/'xlsx'))
        require(importlib.metadata.version('openpyxl') == '3.1.5'
                and importlib.metadata.version('et-xmlfile') == '2.0.0',
                'RUNTIME_INVALID', 'XLSX extension versions differ')
        if config['fresh']:
            from .ensemble_run import worker
            worker(config)
        else:
            root = Path(config['inputs'])
            analyze(checkpoint(root, config['checkpoint_sha256']), root, config)


def analyze(data,root,config,*,reused_p2rank=True,spans=None):
    from . import ensemble_analysis as analysis
    from .process import python_command
    work = Path(config['work'])
    if spans is None:spans=[]
    frames=data['frames'];profile=data['profile'];ref=reference(root);analysis.INPUT=root
    (work/'tmp').mkdir(exist_ok=True);analysis.EXPECTED_POCKETS={profile:data['pocket_count']}
    geometries=[analysis.frame_geometry(f,ref) for f in frames]
    def essa(frame,geo):
        request=work/('essa-'+frame['name']+'.json');output=request.with_suffix('.scores.json')
        write_private(request,canonical({'input':str(geo['file']),'residues':geo['ca'],'output':str(output),'runtime':config['runtime']}))
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
            argv = python_command(Path(config['runtime'])/config['roles']['dynamics_python'],
                                  config['application'], 'dreamm.ensemble_local', 'main')
            subprocess.run([*argv,'essa',str(request)], stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                           check=True, timeout=config['stage_deadlines']['essa'])
        return _json(_read(output))
    analysis.essa=essa
    target=work/'exports';target.mkdir()
    try:
        result=analysis.evaluate(profile,frames,geometries,ref,{},spans,target)
    finally:
        write_private((work/'analysis-timings.json'),canonical(spans))
    for name in ('reference-membrane.csv','reference-membrane-and-conservation.json','source-model-0.pdb','checkpoint.json'):
        shutil.copyfile(root/name,target/name)
    for name in ('original-source.cif','original-source.pdb','source-manifest.json','generation-checkpoint.json'):
        if (root/name).is_file():shutil.copyfile(root/name,target/name)
    if not reused_p2rank:
        for name in ('source-frames','prepared','aligned'):
            shutil.copytree(work/name,target/'structures'/name)
    if data['mode']=='M5' and (work/'generation/generation-checkpoint.json').is_file():
        shutil.copytree(work/'generation/output',target/'generation/native')
        for name in ('reference.pdb','polar.pdb','polar.dssp','run.jl','exprose.invocation.json'):
            shutil.copyfile((work/'generation')/name,target/'generation'/name)
        shutil.copyfile(work/'generation/generation-checkpoint.json',target/'generation/checkpoint.json')
        for name in ('polar-preparation.json', 'chimera-preparation.invocation.json'):
            path = work/'generation'/name
            if path.is_file():
                shutil.copyfile(path,target/'generation'/name)
    write_private(target/'timings.json',canonical(spans));archive=analysis.bundle(target)
    index={p.relative_to(work).as_posix():{'sha256':digest(_read(p)),'bytes':p.stat().st_size}
           for p in work.rglob('*') if p.is_file() and p.relative_to(work).parts[0]!='ccd-cache'}
    write_private((work/'scientific-complete.json'),canonical({'schema':'dreamm-standalone-ensemble/1',
        'mode':data['mode'],'profile':profile,'p2rank':'2.5.1','input_checkpoint_sha256':config['checkpoint_sha256'],
        'M1_reference_reused':True,'P2Rank_outputs_reused':reused_p2rank,'fresh_downstream_analysis':True,
        'frames':len(frames),'membrane_calls':sum(r['membrane'] for r in ref.values()),'sites':len(result['sites']),
        'outcome':result['status'],'zip':archive,'files':index,'ensemble_method':'modern-downstream/1'}))


if __name__=='__main__':main()
