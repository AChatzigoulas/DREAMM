"""Qualified modern ExProSE generation; separate from the protected M1 branch."""
from decimal import Decimal
import hashlib
import math
import os
from pathlib import Path,PurePosixPath
import pkgutil
import shutil
import subprocess

from dreamm_m1.capture import _read,_json,_parents
from dreamm_m1.identity import canonical,digest,require
from dreamm_m1.pdb_input import read_pdb_source


def verify_runtime(root,expected):
    root=Path(root).absolute();_parents(root);raw=_read(root/'runtime.json')
    require(digest(raw)==expected,'RUNTIME_INVALID','generation runtime receipt checksum differs')
    data=_json(raw)
    require(data['schema']=='dreamm-generation-runtime/1' and data['julia']=='1.10.13'
        and data.get('chimera') in (None,'1.19_b42556') and data['ProteinEnsembles']=='0.3.1'
        and data['original_exprose_tree']=='0c88d70cf1a9170ed684c337c5117d41ae9f4b8f'
        and data['plotting_only_correction'] is True and data['M1_scientific_changes'] is False
        and data['weight']=='0.4','RUNTIME_INVALID','qualified generation profile required')
    files,links=data['files'],data['links']
    require(0<len(files)+len(links)<=60000,'RUNTIME_INVALID','generation inventory bound')
    paths=list(root.rglob('*'))
    require({p.relative_to(root).as_posix() for p in paths if not p.is_dir() or p.is_symlink()}
        ==set(files)|set(links)|{'runtime.json'},'RUNTIME_INVALID','generation runtime inventory differs')
    for name,pin in files.items():
        # Julia's official test/package data includes Unicode/punctuation in
        # filenames. Keep this POSIX-only rule local to this pinned closure;
        # do not loosen the shared M1 runtime-path contract.
        require(isinstance(name,str) and 0<len(name)<=1024 and not PurePosixPath(name).is_absolute()
            and '\\' not in name and all(ord(c)>=32 and ord(c)!=127 for c in name)
            and all(p not in ('','.','..') for p in name.split('/')),
            'RUNTIME_INVALID','unsafe generation runtime path')
        path=root/name;_parents(path.parent);info=path.lstat()
        require(path.is_file() and not path.is_symlink() and info.st_size==pin['bytes'] and info.st_size<=256*1024**2,
                'RUNTIME_INVALID','generation file type/size differs: '+name)
        checksum = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024**2), b''):
                checksum.update(block)
        require(checksum.hexdigest()==pin['sha256'],'RUNTIME_INVALID','generation file checksum differs: '+name)
    for name,target in links.items():
        path=root/name
        require(path.is_symlink() and os.readlink(path)==target and path.resolve().is_relative_to(root),
                'RUNTIME_INVALID','generation alias differs')
    return data


def atoms(raw):
    source=read_pdb_source(raw,expected_sha256=digest(raw));result={}
    require(len(source.model_ids)==1,'GENERATION_INVALID','one model per generated file required')
    for atom in source.atoms:
        key=(atom.locator.chain,atom.locator.resnum,atom.locator.insertion,atom.residue.source_resname,
             atom.atom_name_raw.strip(),atom.altloc_raw)
        require(key not in result,'GENERATION_INVALID','ambiguous generated atom identity')
        result[key]=atom
    return result


def saved_checkpoint(root,expected,*,reference_sha256,runtime_sha256,additional):
    from .ensemble_local import inventory
    root=Path(root);raw=_read(root/'checkpoint.json')
    require(digest(raw)==expected,'GENERATION_INVALID','saved generation manifest checksum differs')
    meta=_json(raw);require(meta['schema']=='dreamm-saved-generation/1','GENERATION_INVALID','unsupported saved generation')
    inventory(root,meta)
    result=_json(_read(root/'generation-checkpoint.json'))
    require(result['complete'] is True and result['n_additional']==additional and result['analysis_frames']==additional+1
        and result['w']=='0.4' and result['reference_sha256']==reference_sha256
        and result['runtime_sha256']==runtime_sha256 and len(result['frames'])==additional+1
        and [r['index'] for r in result['frames']]==list(range(additional+1)),
        'GENERATION_INVALID','saved scientific inputs/configuration differ')
    require(digest(_read(root/'reference.pdb'))==reference_sha256,'GENERATION_INVALID','saved reference differs')
    for name,pin in result['scientific_outputs'].items():
        raw=_read(root/'output'/name)
        require(pin=={'bytes':len(raw),'sha256':digest(raw)},'GENERATION_INVALID','incomplete saved scientific output')
    paths=[root/'output/pdbs'/('out.pdb.'+str(i)) for i in range(1,additional+1)]
    require(set((root/'output/pdbs').iterdir())==set(paths),'GENERATION_INVALID','saved conformation set differs')
    for row,path in zip(result['frames'][1:],paths):
        require(digest(_read(path))==row['sha256'],'GENERATION_INVALID','saved generated coordinates differ')
    for name,count in result['arrays'].items():
        values=tuple(map(float,_read(root/'output'/name).split()))
        require(len(values)==count and values and all(map(math.isfinite,values)),
                'GENERATION_INVALID','saved scientific array is incomplete')
    return meta,result


def export_checkpoint(args):
    """Freeze a completed generator stage from a closed attempt, not partial coordinates."""
    from .ensemble_local import inventory
    from .cli import write_private,sync_directory
    attempt=Path(args.attempt).absolute();supervision=_json(_read(attempt/'supervision.json'))
    require((supervision.get('process_group_cleanup_verified') or supervision.get('namespace_cleanup_verified'))
            and not supervision['process_tree_remaining'],
            'GENERATION_INVALID','attempt must be closed before checkpoint export')
    candidates=[attempt/n/'generation' for n in ('results','failed-artifacts') if (attempt/n/'generation/generation-checkpoint.json').is_file()]
    require(len(candidates)==1,'GENERATION_INVALID','one complete generator stage required')
    source=candidates[0];result=_json(_read(source/'generation-checkpoint.json'))
    require(result['complete'] is True,'GENERATION_INVALID','incomplete generator stage')
    root=Path(args.output).absolute();_parents(root.parent);root.mkdir(mode=0o700);files={}
    for path in source.rglob('*'):
        require(not path.is_symlink(),'GENERATION_INVALID','linked generator artifact')
        if path.is_dir():continue
        name=path.relative_to(source).as_posix();raw=_read(path);files[name]={'bytes':len(raw),'sha256':digest(raw)}
        dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);write_private(dest,raw)
    config = attempt/'run.json' if (attempt/'run.json').is_file() else attempt/'admission.json'
    meta={'schema':'dreamm-saved-generation/1','producer_admission_sha256':digest(_read(config)),'files':files}
    write_private(root/'.checkpoint.pending',canonical(meta));os.rename(root/'.checkpoint.pending',root/'checkpoint.json');sync_directory(root)
    checksum=digest(canonical(meta));saved_checkpoint(root,checksum,reference_sha256=result['reference_sha256'],
        runtime_sha256=result['runtime_sha256'],additional=result['n_additional'])
    return {'output':str(root),'sha256':checksum,'n_additional':result['n_additional'],'scientific_reexecution':False}


def generate(root,ref,additional,config,spans,saved=None,seed=None):
    from .ensemble_run import ca_identity
    from .ensemble_analysis import measured
    require(type(additional) is int and 1<=additional<=50,'GENERATION_INVALID','n must be 1..50')
    require(seed is None or type(seed) is int and 0<=seed<2**64,'GENERATION_INVALID','invalid ExProSE seed')
    work = Path(config['work'])/'generation'
    work.mkdir()
    reference=_read(root/'prepared-reference.pdb');receipt=_json(_read(root/'reference-membrane-and-conservation.json'))
    require(digest(reference)==receipt['prepared_sha256'] and ca_identity(reference)=={k:r['aa'] for k,r in ref.items()},
            'GENERATION_INVALID','prepared M1 reference provenance or residues differ')
    if saved is not None:
        source=root/'generation-reuse'
        meta,result=saved_checkpoint(source,saved['sha256'],reference_sha256=digest(reference),
            runtime_sha256=config['generation_sha256'],additional=additional)
        require(seed is None or result.get('seed')==seed,'GENERATION_INVALID','saved ExProSE seed differs')
        for name in meta['files']:
            destination=work/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(_read(source/name))
        (work.parent/'generation-reuse.json').write_bytes(canonical({'source_checkpoint_sha256':saved['sha256'],
            'exact_generated_coordinates_reused':True,'generator_rerun':False}))
        paths=[work/'output/pdbs'/('out.pdb.'+str(i)) for i in range(1,additional+1)]
        frames=[reference]+[_read(p) for p in paths]
        require(all(ca_identity(f)==ca_identity(reference) for f in frames),'GENERATION_INVALID','saved residue mapping differs')
        return frames,result
    (work/'reference.pdb').write_bytes(reference)
    runtime = Path(config['runtime'])
    generation = Path(config['generation'])
    env=dict(os.environ,JULIA_DEPOT_PATH=str(generation/'depot'),
        JULIA_LOAD_PATH=str(generation/'patched-project')+':@stdlib',JULIA_NUM_THREADS='1',
        OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',JULIA_CPU_TARGET='generic',LANG='C.UTF-8')
    def run(stage,argv,timeout):
        with measured(stage,spans),(work/(stage+'.stdout')).open('xb') as so,(work/(stage+'.stderr')).open('xb') as se:
            subprocess.run(argv,cwd=work,env=env,stdin=subprocess.DEVNULL,stdout=so,stderr=se,check=True,timeout=timeout)
        (work/(stage+'.invocation.json')).write_bytes(canonical({'argv':argv,'timeout_seconds':timeout}))
    if config.get('chimera'):
        (work/'prepare.com').write_bytes(b'open reference.pdb\ndelete HC\nwrite #0 polar.pdb\nclose #0\n')
        run('chimera-preparation',[str(generation/'chimera/bin/chimera'),'--nogui','prepare.com'],120)
    else:
        from .polar import prepare
        with measured('polar-preparation',spans):
            patches = runtime/'charge/lib/python3.14/site-packages/pdb2pqr/dat/PATCHES.xml'
            polar, evidence = prepare(reference, Path(config['p2rank'])/'ccd/chemcomp', patches)
            (work/'polar.pdb').write_bytes(polar)
            (work/'polar-preparation.json').write_bytes(evidence)
    original=atoms(reference);polar=_read(work/'polar.pdb');kept=atoms(polar)
    require(kept.keys()<=original.keys() and ca_identity(polar)==ca_identity(reference),
            'GENERATION_INVALID','polar preparation atom/residue correspondence differs')
    require(all(original[k].element_raw.strip()=='H' for k in original.keys()-kept.keys())
        and all(tuple(map(Decimal,kept[k].xyz_raw))==tuple(map(Decimal,original[k].xyz_raw)) for k in kept),
        'GENERATION_INVALID','polar preparation changed retained atoms')
    env['LD_LIBRARY_PATH']=str(runtime/'native/lib')
    run('dssp',[str(runtime/config['roles']['dssp']),'-i','polar.pdb','-o','polar.dssp'],120)
    env.pop('LD_LIBRARY_PATH',None)
    require(b'#  RESIDUE AA STRUCTURE' in _read(work/'polar.dssp'),'GENERATION_INVALID','DSSP output incomplete')
    launcher=pkgutil.get_data('dreamm','data/exprose_entry.jl')
    (work/'run.jl').write_bytes(launcher)
    if seed is not None:env['DREAMM_EXPROSE_SEED']=str(seed)
    run('exprose',[str(generation/'julia/bin/julia'),'--startup-file=no','--history-file=no','--compiled-modules=no',
        '--project='+str(generation/'patched-project'),'run.jl','--i1','polar.pdb','--d1','polar.dssp',
        '-n',str(additional),'-o','output','-w','0.4'],config.get('stage_deadlines',{}).get('generation',1200))
    output=work/'output';paths=[output/'pdbs'/('out.pdb.'+str(i)) for i in range(1,additional+1)]
    require(set((output/'pdbs').iterdir())==set(paths),'GENERATION_INVALID','missing/extra conformations')
    frames=[reference];records=[{'index':0,'role':'reference','sha256':digest(reference)}];topology=None
    heavy={k for k,a in kept.items() if a.element_raw.strip()!='H'}
    for i,path in enumerate(paths,1):
        raw=_read(path);mapped=atoms(raw)
        require(ca_identity(raw)==ca_identity(reference) and mapped.keys()<=kept.keys() and heavy<=mapped.keys(),
                'GENERATION_INVALID','generated atom/residue identity or heavy-atom coverage differs')
        if topology is None:topology=mapped.keys()
        require(mapped.keys()==topology,'GENERATION_INVALID','generated topology differs between frames')
        coordinates=digest(canonical([a.xyz_raw for a in mapped.values()]))
        frames.append(raw);records.append({'index':i,'role':'generated','sha256':digest(raw),'atoms':len(mapped),
            'coordinates_sha256':coordinates})
    require(len({r['coordinates_sha256'] for r in records[1:]})==additional,'GENERATION_INVALID','duplicate generated conformations')
    arrays={}
    names=('spe_scores.tsv','rmsds.tsv')
    if additional>1:names+=('pcs/pcs.tsv','pcs/pcs_input.tsv','pcs/evals_spread.tsv','rmsfs.tsv')
    for name in names:
        values=tuple(map(float,_read(output/name).split()))
        require(values and all(map(math.isfinite,values)),'GENERATION_INVALID','missing/nonfinite scientific array: '+name)
        arrays[name]=len(values)
    require(arrays['spe_scores.tsv']==additional and arrays['rmsds.tsv']==additional,
            'GENERATION_INVALID','incomplete conformation scores')
    if additional==2:
        require(arrays['pcs/pcs.tsv']==2 and arrays['pcs/evals_spread.tsv']==1
            and not list((output/'pcs').glob('*.png')),'GENERATION_INVALID','invalid singleton-PC analysis')
    if additional>1:
        require(_read(output/'rmsfs.png').startswith(b'\x89PNG\r\n\x1a\n'),'GENERATION_INVALID','missing RMSF plot')
    pca_status={1:'unavailable_single_generated_conformation',2:'unavailable_fewer_than_two_components'}
    result={'schema':'dreamm-modern-generation/1','complete':True,'n_additional':additional,'analysis_frames':len(frames),
        'w':'0.4','reference_sha256':digest(reference),'frames':records,'arrays':arrays,
        'polar_preparation':'chimera-1.19' if config.get('chimera') else 'ccd-bonds/1','polar_sha256':digest(polar),'dssp_sha256':digest(_read(work/'polar.dssp')),
        'pca_2d_plots':pca_status.get(additional,'recorded_native_outputs'),
        'ensemble_statistics':'unavailable_single_generated_conformation' if additional==1 else 'native',
        'runtime_sha256':config['generation_sha256'],'seed_supplied':seed is not None,'seed':seed,
        'launcher_sha256':digest(launcher),'julia':'1.10.13','RNG':'TaskLocalRNG',
        'M1_recomputed':False,'scientific_outputs':{p.relative_to(output).as_posix():{'bytes':p.stat().st_size,
          'sha256':digest(_read(p))} for p in output.rglob('*') if p.is_file()}}
    (work/'generation-checkpoint.json').write_bytes(canonical(result))
    return frames,result
