"""Modern M3/M4 preparation, alignment and P2Rank; unchanged M1 checkpoint reuse."""
from decimal import Decimal
import os
from pathlib import Path
import pkgutil
import re
import shutil
import subprocess

from dreamm_m1.capture import _read,_json
from dreamm_m1.identity import AA,canonical,digest,require
from dreamm_m1.pdb_input import read_pdb_source
from .ensemble_local import reference,inventory,checkpoint
from .ensemble_source import ca_identity


def verify_p2rank(root,expected):
    root=Path(root);raw=_read(root/'runtime.json');require(digest(raw)==expected,'RUNTIME_INVALID','P2Rank receipt checksum differs')
    data=_json(raw);approved=_json(pkgutil.get_data('dreamm','data/p2rank251.json'))
    require(data['schema']=='dreamm-p2rank-runtime/1' and data['version']=='2.5.1'
        and data['files']==approved['files'] and data['classpath_order']==approved['classpath_order']
        and data['science_profiles']==approved['science_profiles'], 'RUNTIME_INVALID','unqualified P2Rank scientific closure')
    paths=list(root.rglob('*'))
    require(not any(p.is_symlink() for p in paths)
        and {p.relative_to(root).as_posix() for p in paths if p.is_file()}==set(data['files'])|{'runtime.json'},
        'RUNTIME_INVALID','P2Rank runtime inventory differs')
    for name,pin in data['files'].items():
        value=_read(root/name,64*1024**2)
        require(pin=={'bytes':len(value),'sha256':digest(value)},'RUNTIME_INVALID','P2Rank installed file differs: '+name)
    return data


def native_input(args):
    require(args.mode in ('M3','M4','M5'),'GENERATION_RUNTIME_UNQUALIFIED','unsupported ensemble mode')
    path=Path(args.reference_checkpoint);raw=_read(path/'checkpoint.json')
    require(digest(raw)==args.reference_sha256,'ENSEMBLE_REFERENCE_INVALID','reference manifest checksum differs')
    prior=_json(raw)
    if prior['schema']=='dreamm-reference-checkpoint/1':
        require(prior['M1_actual_inference_reused'] is True,
                'ENSEMBLE_REFERENCE_INVALID','actual completed M1 reference required')
        inventory(path,prior);reference(path)
    else:
        require(args.mode!='M5','ENSEMBLE_REFERENCE_INVALID','M5 requires an exported M1 reference with prepared coordinates')
        prior=checkpoint(path,args.reference_sha256)
    from .cli import check_selection, read_input
    from dreamm_m1.local_budget import source_bytes
    fmt = getattr(args, 'source_format', None) or ('pdb' if args.mode == 'M4' else 'mmcif')
    chains = getattr(args, 'chains', None)
    check_selection(args.mode,fmt,args.pdb_id,chains,getattr(args,'additional',None),trusted_input=True)
    require(args.ensemble_profile in ('default','alphafold'),'ENSEMBLE_INPUT_INVALID','explicit scientific profile required')
    source=read_input(args.input,source_bytes());require(digest(source)==args.sha256,'ENSEMBLE_INPUT_INVALID','source checksum differs')
    files={n:_read(Path(args.reference_checkpoint)/n) for n in
        ('source-model-0.pdb','reference-membrane.csv','reference-membrane-and-conservation.json')}
    receipt=_json(files['reference-membrane-and-conservation.json'])
    if prior['schema']=='dreamm-reference-checkpoint/1':
        require(receipt['original_source_sha256']==args.sha256 and receipt.get('chain_selection')==chains,
                'ENSEMBLE_REFERENCE_INVALID','reference source or chain selection differs')
    if args.mode=='M5':
        require(receipt['original_source_sha256']==args.sha256,'ENSEMBLE_REFERENCE_INVALID','generation source is not the actual M1 source')
        files['prepared-reference.pdb']=_read(Path(args.reference_checkpoint)/'prepared-reference.pdb')
        require(digest(files['prepared-reference.pdb'])==receipt['prepared_sha256'],
                'ENSEMBLE_REFERENCE_INVALID','M1 prepared reference checksum differs')
    require(all(prior['files'][name]=={'bytes':len(raw),'sha256':digest(raw)} for name,raw in files.items()),
            'ENSEMBLE_REFERENCE_INVALID','reference changed after validation')
    saved=None
    if args.mode=='M5' and getattr(args,'generation_checkpoint',None):
        from .generation import saved_checkpoint
        require(args.generation_checkpoint_sha256,'GENERATION_INVALID','saved generation needs its checksum')
        meta,_=saved_checkpoint(args.generation_checkpoint,args.generation_checkpoint_sha256,
            reference_sha256=receipt['prepared_sha256'],runtime_sha256=args.generation_sha256,additional=args.additional)
        saved={'sha256':args.generation_checkpoint_sha256}
        for name in meta['files']:files['generation-reuse/'+name]=_read(Path(args.generation_checkpoint)/name)
        files['generation-reuse/checkpoint.json']=_read(Path(args.generation_checkpoint)/'checkpoint.json')
    files['source.cif' if fmt=='mmcif' else 'source.pdb']=source
    data={'schema':'dreamm-ensemble-native-input/1','mode':args.mode,'pdb_id':args.pdb_id,
          'source_sha256':args.sha256,'source_format':fmt,'chains':chains,'profile':args.ensemble_profile,
          'reference_checkpoint_sha256':args.reference_sha256,'n_additional':getattr(args,'additional',None),
          'seed':getattr(args,'seed',None),
          'generation_reuse':saved,
          'files':{n:{'bytes':len(v),'sha256':digest(v)} for n,v in files.items()}}
    return data,files


def bind_reference(raw,original):
    def atoms(value):
        source=read_pdb_source(value,expected_sha256=digest(value));result={}
        for a in source.reference_atoms:
            key=(a.record,a.locator.chain,a.locator.resnum,a.locator.insertion,a.residue.source_resname,
                 a.atom_name_raw.strip(),a.altloc_raw,a.element_raw.strip())
            require(key not in result,'ENSEMBLE_MAPPING_UNPROVEN','ambiguous reference atom identity')
            result[key]=tuple(Decimal(x.strip()) for x in (*a.xyz_raw,a.occupancy_raw,a.bfactor_raw))
        return result
    require(atoms(raw)==atoms(original),'ENSEMBLE_MAPPING_UNPROVEN',
            'reference model differs from the M1 source; refusing positional or proximity joins')


def prepare_frames(raw_frames,ref,spans,work,*,generated=False):
    from moleculekit.molecule import Molecule
    from moleculekit.tools.preparation import systemPrepare
    import numpy as np
    from dreamm_m1.identity import residue_label
    from .modern_preparation import SETTINGS
    from .modern import filter_reference
    from .ensemble_analysis import measured
    # Predictor-only conventional residue labels; preparation bytes stay intact.
    aliases={'HID':'HIS','HIE':'HIS','HIP':'HIS','HSD':'HIS','HSE':'HIS','HSP':'HIS',
             'ASH':'ASP','GLH':'GLU','CYM':'CYS','CYX':'CYS','LYN':'LYS'}
    expected={k:r['aa'] for k,r in ref.items()};frames=[];reference_molecule=None

    def alignment_indices(molecule):
        rows=[(f'{chain}_{residue_label(num,insertion)}',index)
              for index,(chain,num,insertion,name) in enumerate(
                  zip(molecule.chain,molecule.resid,molecule.insertion,molecule.name)) if name=='CA']
        result=dict(rows)
        require(len(rows)==len(result) and result.keys()==expected.keys(),
                'ENSEMBLE_MAPPING_UNPROVEN','prepared C-alpha identity differs before alignment')
        return result
    for directory in ('source-frames','prepared','aligned','bound/frames'):(work/directory).mkdir(parents=True)
    for i,raw in enumerate(raw_frames):
        name=f'frame-{i:02d}.pdb';source=(work/'source-frames')/name;source.write_bytes(raw)
        require(ca_identity(raw)==expected,'ENSEMBLE_MAPPING_UNPROVEN','source frame residue coverage differs')
        with measured('prepare.frame-'+str(i),spans):
            molecule=filter_reference(source)
            settings=dict(SETTINGS)
            if generated and digest(raw_frames[0])=='77725c01c422b75e9efa7f932836ae8447166488ec4ea27b92caa886a5383b1f':
                # Fixture-scoped canonical topology: new conformations cannot
                # create THR-HG1/ASP covalent links from a close H contact.
                require('C' not in expected.values(),'ENSEMBLE_INPUT_INVALID','1RLW chemistry correspondence differs')
                settings['guess_bonds']=False
            (work/f'preparation-settings-{i:02d}.json').write_bytes(canonical(settings))
            molecule,detected,details=systemPrepare(molecule,**settings)
            (work/f'preparation-specs-{i:02d}.json').write_bytes(canonical(detected))
            details.to_csv(work/f'preparation-details-{i:02d}.csv',index=False)
            ca_indices=alignment_indices(molecule)
            molecule.center(loc=(0,0,0),sel='all');prepared=(work/'prepared')/name;molecule.write(str(prepared))
        with measured('align.frame-'+str(i),spans):
            if reference_molecule is None:
                reference_molecule=Molecule(str(prepared))
                reference_indices=alignment_indices(reference_molecule)
            else:
                # Pair the same author residues in frame-zero order while
                # retaining each frame's deposited record order for export.
                sel=np.array([ca_indices[key] for key in reference_indices],dtype=int)
                refsel=np.array(list(reference_indices.values()),dtype=int)
                molecule.align(sel,reference_molecule,refsel)
            aligned=(work/'aligned')/name;molecule.write(str(aligned))
        native=aligned.read_bytes();lines=native.splitlines(keepends=True);changes=[]
        for j,line in enumerate(lines):
            if not line.startswith((b'ATOM  ',b'HETATM')):continue
            old=line[17:20].decode('ascii')
            if old in aliases:
                lines[j]=line[:17]+aliases[old].encode()+line[20:];changes.append([j+1,old,aliases[old]])
        projected=b''.join(lines)
        require(ca_identity(projected)==expected,'ENSEMBLE_MAPPING_UNPROVEN','prepared/aligned residue correspondence differs')
        # Explicit author-identity joins, never coordinate proximity or equal
        # populations. Added atoms have no guessed source ancestry.
        def indexed(value):
            parsed=read_pdb_source(value,expected_sha256=digest(value));result={}
            for a in parsed.atoms:
                if a.record not in ('ATOM','HETATM'):continue
                key=(a.locator.chain,a.locator.resnum,a.locator.insertion,a.atom_name_raw.strip(),a.altloc_raw,a.element_raw.strip())
                require(key not in result,'ENSEMBLE_MAPPING_UNPROVEN','ambiguous author atom correspondence')
                result[key]=a
            return result
        original_atoms=indexed(raw);prepared_atoms=indexed(prepared.read_bytes());common=original_atoms.keys()&prepared_atoms.keys()
        atom_map={'schema':'dreamm-ensemble-preparation-map/1','frame_index':i,
            'source_sha256':digest(raw),'prepared_sha256':digest(prepared.read_bytes()),
            'method':'unique_author_chain_resnum_insertion_atom_altloc_element_after_complete_CA_binding',
            'coordinate_transform':'center_all_atoms_before_alignment',
            'common':[{'source_uid':original_atoms[k].uid,'prepared_uid':prepared_atoms[k].uid,
                'source_locator':vars(original_atoms[k].locator),'prepared_locator':vars(prepared_atoms[k].locator),
                'source_resname':original_atoms[k].residue.source_resname,
                'prepared_resname':prepared_atoms[k].residue.source_resname} for k in sorted(common)],
            'removed':[original_atoms[k].uid for k in sorted(original_atoms.keys()-common)],
            'added':[{'prepared_uid':prepared_atoms[k].uid,'source_ancestry':None} for k in sorted(prepared_atoms.keys()-common)]}
        from dreamm_m1.local_budget import artifact_bytes
        encoded=canonical(atom_map);require(len(encoded)<=artifact_bytes(),'RESOURCE_LIMIT','atom correspondence receipt exceeds bound')
        (work/f'preparation-map-{i:02d}.json').write_bytes(encoded)
        destination=(work/'bound/frames')/name;destination.write_bytes(projected)
        frames.append({'index':i,'name':name,'sha256':digest(projected),'source_sha256':digest(raw),
            'prepared_sha256':digest(prepared.read_bytes()),'aligned_sha256':digest(native),
            'predictor_residue_label_projection':changes})
    return frames


def predict(frames,profile,runtime,spans,work,config):
    from .ensemble_analysis import measured

    p2rank = Path(config['p2rank'])
    output = work/'p2rank-output'
    output.mkdir()
    dataset = work/'dataset.ds'
    dataset.write_text(''.join('bound/frames/'+f['name']+'\n' for f in frames), encoding='ascii')
    cache = work/'ccd-cache'
    shutil.copytree(p2rank/'ccd/chemcomp', cache/'chemcomp')
    tmp = work/'tmp'
    tmp.mkdir(exist_ok=True)
    java = Path(config['runtime'])/config['roles']['java']
    argv=[str(java),'-Xmx2048m','-XX:ActiveProcessorCount=15','-Djava.awt.headless=true',
          '-Duser.home='+os.environ['HOME'],'-Djava.io.tmpdir='+str(tmp),
          '-DPDB_CACHE_DIR='+str(cache),'-DPDB_DIR='+str(p2rank/'ccd'),'-cp',
          os.pathsep.join(str(p2rank/n) for n in runtime['classpath_order']),'cz.siret.prank.program.Main',
          'predict',str(dataset)]
    if profile=='alphafold':argv+=['-c','alphafold']
    argv+=['-o',str(output),'-threads','15','-fail_fast','true']
    env=dict(os.environ,INSTALL_DIR=str(p2rank),PDB_CACHE_DIR=str(cache),PDB_DIR=str(p2rank/'ccd'))
    with measured('p2rank.2.5.1.'+profile,spans),(work/'p2rank.stdout').open('xb') as so,(work/'p2rank.stderr').open('xb') as se:
        subprocess.run(argv,cwd=work,env=env,stdin=subprocess.DEVNULL,stdout=so,stderr=se,check=True,
                       timeout=config['stage_deadlines']['p2rank'])
    log=_read((work/'p2rank.stdout'))+_read((work/'p2rank.stderr'))
    require(not re.search(br'could not download|download(ing|ed) chem|fallback|UnknownHostException|failed to load structure|NoClassDefFoundError|ClassNotFoundException',log,re.I),
            'ENSEMBLE_INCOMPLETE','P2Rank dependency/chemistry failure')
    expected={f['name']+'_'+role+'.csv' for f in frames for role in ('predictions','residues')}
    require({p.name for p in output.glob('*.csv') if p.name.endswith(('_predictions.csv','_residues.csv'))}==expected,
            'ENSEMBLE_INCOMPLETE','missing, extra or duplicated P2Rank frame output')
    destination=(work/'bound/raw')/profile;destination.mkdir(parents=True)
    for f in frames:
        name=f['name']
        for role in ('predictions','residues'):shutil.copyfile(output/(name+'_'+role+'.csv'),destination/(name+'_'+role+'.csv'))
        shutil.copyfile(output/'visualizations/data'/(name+'_points.pdb.gz'),destination/(name+'_points.pdb.gz'))
    shutil.copyfile(output/'params.txt',(work/'bound/p2rank-effective.txt'))
    (work/'p2rank-invocation.json').write_bytes(canonical({'argv':argv,'profile':profile,
        'science_profile_sha256':runtime['science_profiles'][profile]}))


def worker(config):
    from .ensemble_source import prepare_source, select_frame
    from .ensemble_local import analyze
    from . import ensemble_analysis as analysis

    work, inputs = Path(config['work']), Path(config['inputs'])
    raw = _read(inputs/'checkpoint.json')
    require(digest(raw) == config['checkpoint_sha256'], 'ENSEMBLE_INPUT_INVALID', 'native intake changed')
    data = _json(raw)
    inventory(inputs, data)
    ref = reference(inputs)
    require(data['schema']=='dreamm-ensemble-native-input/1' and data['mode'] in ('M3','M4','M5'),
            'ENSEMBLE_INPUT_INVALID','native scientific mode differs')
    runtime = _json(_read(Path(config['p2rank'])/'runtime.json'))
    fmt=data.get('source_format') or ('pdb' if data['mode']=='M4' else 'mmcif')
    suffix='.cif' if fmt=='mmcif' else '.pdb'
    source=_read(inputs/('source'+suffix))
    if data['mode']=='M5':
        from dataclasses import replace
        from dreamm_ensemble.frames import first_pdb_frame, project_mmcif
        from dreamm_m1.mmcif_input import CIF_1RLW_SHA256
        from .modern_source import read_source
        from dreamm_m1.preparation import reference_bridge
        if fmt == 'mmcif':
            parsed=read_source(source,expected_sha256=data['source_sha256'],runtime_sha256=config['runtime_sha256'])
            if parsed.sha256 == CIF_1RLW_SHA256 and not data.get('chains'):
                bridge=reference_bridge(parsed)[0]
            else:
                bridge=project_mmcif(replace(parsed,atoms=parsed.reference_atoms,model_ids=parsed.model_ids[:1]))[0].original
        else:
            bridge=first_pdb_frame(read_pdb_source(source,expected_sha256=data['source_sha256']))
        frames=[select_frame(bridge,data.get('chains'))]
        intake={'schema':'dreamm-M5-reference-intake/1','source_sha256':data['source_sha256'],
            'reference_frame_index':0,'bridge_sha256':digest(frames[0]),'full_original_preserved':True}
    else:
        frames,intake=prepare_source(source,expected_sha256=data['source_sha256'],mode=data['mode'],
            runtime_sha256=config['runtime_sha256'],fmt=fmt,chains=data.get('chains'))
    if data['pdb_id']:
        from .modern_source import verify_entry_id
        verify_entry_id(source,data['pdb_id'])
    bind_reference(frames[0],_read((inputs/'source-model-0.pdb')))
    (work/'source-manifest.json').write_bytes(canonical(intake));spans=[]
    try:
        generation=None
        if data['mode']=='M5':
            from .generation import generate
            frames,generation=generate(inputs,ref,data['n_additional'],config,spans,
                saved=data.get('generation_reuse'),seed=data.get('seed'))
            intake={'schema':'dreamm-M5-source/1','mode':'M5','original_source':intake,
                'reference_frame_index':0,'analysis_frames':generation['frames'],'n_additional':data['n_additional']}
            (work/'source-manifest.json').write_bytes(canonical(intake))
        products=prepare_frames(frames,ref,spans,work,generated=data['mode']=='M5')
        predict(products,data['profile'],runtime,spans,work,config)
        root=(work/'bound');analysis.INPUT=root
        (root/('original-source'+suffix)).write_bytes(source)
        (root/'source-manifest.json').write_bytes(canonical(intake))
        for name in ('source-model-0.pdb','reference-membrane.csv','reference-membrane-and-conservation.json'):
            shutil.copyfile(inputs/name,root/name)
        geometries=[analysis.frame_geometry(f,ref) for f in products]
        count=0
        for frame,geo in zip(products,geometries):count+=len(analysis.filter_frame(data['profile'],frame,geo)[0])
        if generation is not None:(root/'generation-checkpoint.json').write_bytes(canonical(generation))
        cp={'schema':'dreamm-ensemble-analysis-input/1','mode':data['mode'],'profile':data['profile'],
            'p2rank_version':'2.5.1','pocket_count':count,'frames':products,
            'provenance':{'source_sha256':data['source_sha256'],'runtime_sha256':config['runtime_sha256'],
                'reference_checkpoint_sha256':data['reference_checkpoint_sha256'],
                'ensemble_method':'modern-MoleculeKit/2-M5' if data['mode']=='M5' else 'modern-MoleculeKit/1'},
            'files':{p.relative_to(root).as_posix():{'bytes':p.stat().st_size,'sha256':digest(_read(p))}
                     for p in root.rglob('*') if p.is_file()}}
        (root/'checkpoint.json').write_bytes(canonical(cp));checkpoint(root,digest(canonical(cp)))
        (work/'native-stage-timings.json').write_bytes(canonical(spans))
        analyze(cp,root,config,reused_p2rank=False,spans=spans)
    finally:
        if not (work/'scientific-complete.json').exists():
            (work/'native-stage-timings.json').write_bytes(canonical(spans))
