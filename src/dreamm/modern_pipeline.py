"""Modern M1 orchestration of the immutable scientific functions, 1RLW first.

This module owns neither scheduling nor process containment. The existing local
supervisor admits one bounded worker; any failed stage fails the whole attempt.
"""
import os
from pathlib import Path
import pkgutil
import re
import subprocess
import sys

from dreamm_m1 import native_m1 as native
from dreamm_m1.capture import _json, _parents, _read
from dreamm_m1.execution_profiles import ExecutionProfile
from dreamm_m1.identity import AA, Locator, canonical, descriptor_adapter, digest, require
from dreamm_m1.local_budget import source_bytes, trusted_local
from dreamm_m1.measurement import StageRecorder, measured
from dreamm_m1.mmcif_evidence import compare_mmcif_to_pdb
from dreamm_m1.mmcif_input import CIF_1RLW_SHA256
from dreamm_m1.msms_mesh import validate_mesh
from dreamm_m1.pdb_evidence import read_python_features
from dreamm_m1.pdb_input import read_pdb_source, PdbSource, PDB_1RLW_SHA256, source_prepared_correspondence
from dreamm_m1.pipeline import prepare_m1
from dreamm_m1.preparation import charge_correspondence, read_pqr, reference_bridge
from dreamm_m1.reference import verify_reference_assets
from dreamm_m1.rocky_database import FINGERPRINT, PREFIX
from dreamm_m1.source_packet import load_1rlw_source_packet, _HASHES
from .modern_runtime import ModernRuntimeBundle, verify_python_versions
from .worker import bind_tools, reference_fixture

SECONDS = {'preparation': 360, 'dssp': 180, 'freesasa': 180, 'msms': 180,
           'hhblits': 14400, 'dynamics': 180, 'protdcal': 7200}


def recipe_sha256():
    return digest(canonical({name: digest(pkgutil.get_data('dreamm', name)) for name in
        ('modern_pipeline.py', 'modern_stage.py', 'modern_preparation.py', 'modern_inference.py', 'model_compat.py',
         'writer_identity.py', 'residue_mapping.py', 'legacy_residues.py', 'charge_stage.py')}))


def stage_argv(python, kind, config):
    require(kind in ('preparation', 'dynamics', 'inference'), 'LOCAL_INVALID', 'unknown modern scientific stage')
    return (str(python), '-I', '-S', '-B', '-c',
            "import sys;sys.path.insert(0,'/code/dreamm.pyz');from dreamm.modern_stage import main;main()",
            kind, str(config))


def _pdb(raw):
    return read_pdb_source(raw, expected_sha256=digest(raw))


def preparation_outputs(source, extraction, expected_residues=126, *, general=False, chains=None):
    """Bind actual preparation output to original source UIDs by identifiers."""
    before, prepared = (_read(extraction/name) for name in ('before.pdb', 'prepared.pdb'))
    if general:
        from .residue_mapping import match_preparation
        before_pdb = _pdb(before)
        bindings, report = match_preparation(source, before_pdb, _pdb(prepared), chains)
        (extraction/'preparation-map.json').write_bytes(canonical(report))
        rows = _json(_read(extraction/'pre-ca.json'))
        by_position = {(loc.chain, int(loc.resnum), loc.insertion): loc for loc in bindings}
        ca = {by_position[r['chain'], int(r['resnum']), r['insertion']]: r['xyz'] for r in rows}
        require(len(rows) == len(ca) and ca.keys() == bindings.keys(),
                'PREPARATION_MAPPING_UNPROVEN', 'source C-alpha coverage differs')
        serialization=extraction/'charge-serializer.json'
        charges = charge_correspondence(before_pdb, read_pqr(
            _read(extraction/'charges.pqr'), legacy_zero_hydrogen_radius=True,
            serialization=_json(_read(serialization)) if serialization.is_file() else None), bindings)
        require(not charges['missing_input_atom_uids'], 'CHARGE_MAPPING_UNPROVEN',
                'charge pass lost PDB input atoms')
        (extraction/'charge-map.json').write_bytes(canonical(charges))
        return prepared, bindings, ca, charges
    if isinstance(source,PdbSource):
        before_map,pre_bindings=source_prepared_correspondence(source,_pdb(before))
        require(before_map['all_prepared_residues_bound'] and len(pre_bindings)==expected_residues
                and before_map['counts']['coordinate_changes']==0 and before_map['counts']['prepared_only_atoms']==0
                and all(a['record']=='HETATM' for a in before_map['source_only_atoms']),
                'PREPARATION_MAPPING_UNPROVEN','filtered PDB reference identity/coordinates changed')
        report,bindings=source_prepared_correspondence(source,_pdb(prepared))
        require(report['all_prepared_residues_bound'] and bindings==pre_bindings,
                'PREPARATION_MAPPING_UNPROVEN','prepared PDB reference correspondence changed')
        (extraction/'preparation-map.json').write_bytes(canonical(report))
        rows=_json(_read(extraction/'pre-ca.json'))
        ca={Locator(r['chain'],r['resnum'],r['insertion']):r['xyz'] for r in rows}
        require(len(rows)==len(ca) and ca.keys()==bindings.keys(),'PREPARATION_MAPPING_UNPROVEN','PDB CA coverage changed')
        charges=charge_correspondence(_pdb(before),read_pqr(_read(extraction/'charges.pqr'),legacy_zero_hydrogen_radius=True),bindings)
        require(not charges['missing_input_atom_uids'],'CHARGE_MAPPING_UNPROVEN','charge pass lost PDB input atoms')
        (extraction/'charge-map.json').write_bytes(canonical(charges))
        return prepared,bindings,ca,charges
    report, pre_bindings = compare_mmcif_to_pdb(source, _pdb(before))
    require(report['all_pdb_protein_residues_bound'] and report['counts']['coordinate_changes'] == 0
            and report['counts']['pdb_only_atoms'] == 0 and len(pre_bindings) == expected_residues
            and all(row['record'] == 'HETATM' for row in report['source_only_atoms']),
            'PREPARATION_MAPPING_UNPROVEN', 'filtered source atom identities/coordinates changed')
    report, bindings = compare_mmcif_to_pdb(source, _pdb(prepared))
    require(report['all_pdb_protein_residues_bound'] and bindings == pre_bindings,
            'PREPARATION_MAPPING_UNPROVEN', 'prepared reference residue correspondence changed')
    (extraction/'preparation-map.json').write_bytes(canonical(report))
    rows = _json(_read(extraction/'pre-ca.json'))
    ca = {Locator(r['chain'], r['resnum'], r['insertion']): r['xyz'] for r in rows}
    require(len(rows) == len(ca) and ca.keys() == bindings.keys(),
            'PREPARATION_MAPPING_UNPROVEN', 'original CA coverage changed')
    charges = charge_correspondence(_pdb(before), read_pqr(_read(extraction/'charges.pqr'),
                                      legacy_zero_hydrogen_radius=True), bindings)
    require(not charges['missing_input_atom_uids'], 'CHARGE_MAPPING_UNPROVEN', 'charge pass lost input atoms')
    (extraction/'charge-map.json').write_bytes(canonical(charges))
    return prepared, bindings, ca, charges


def admit_reference(bridge):
    """Bound the classifier to its established single-chain identity contract."""
    atoms=[a for a in _pdb(bridge).reference_atoms if a.record=='ATOM']
    chains={a.locator.chain for a in atoms}
    if trusted_local():
        require(chains and all(re.fullmatch(r'[A-Za-z0-9]', chain) for chain in chains),
                'SOURCE_INVALID', 'PDB tool projection requires representable protein chains')
        return None
    require(len(chains)==1 and re.fullmatch(r'[A-Za-z0-9]',next(iter(chains))),
            'SOURCE_INVALID','exactly one protein chain with a PDB chain identifier is required')
    require(all(a.residue.source_resname in AA and a.altloc_raw==' ' and a.locator.insertion==' ' for a in atoms),
            'SOURCE_INVALID','alternate sites, insertion codes and nonstandard residues require explicit qualification')
    residues={a.locator:a.residue.source_resname for a in atoms}
    require(all(a.residue.source_resname==residues[a.locator] for a in atoms),
            'SOURCE_INVALID','conflicting residue names at the same locator')
    ca=[a.locator for a in atoms if a.atom_name_raw.strip()=='CA']
    require(0<len(residues)<=524 and len(ca)==len(residues) and set(ca)==residues.keys()
        and len({(loc.chain,int(loc.resnum)) for loc in ca})==len(ca),
        'SOURCE_INVALID','require 1..524 unambiguous protein residues with one CA each')
    return len(residues)


def run_reference(request_directory, *, bundle, runtime_root, profiles, database_prefix,
                  database_verification, work, profile_bytes, source_packet=None, execution='bounded-worker',
                  stage_deadlines=None):
    """Called after effective local isolation, runtime and database verification."""
    from .cli import verify_request
    import argparse
    require(isinstance(bundle, ModernRuntimeBundle), 'SCIENTIFIC_RUNTIME_MISMATCH', 'modern runtime profile required')
    verify_request(argparse.Namespace(directory=request_directory, trusted_input=trusted_local()))
    request = _json(_read(Path(request_directory)/'request.json', 16384))
    require(execution in ('bounded-worker','trusted-local'), 'LOCAL_ADMISSION_FAILED', 'unknown execution boundary')
    deadlines={**SECONDS,'inference':180}
    if stage_deadlines is not None:
        require(trusted_local() and execution=='trusted-local' and stage_deadlines.keys()==deadlines.keys()
                and all(type(value) is int and 1<=value<=86400 for value in stage_deadlines.values()),
                'LOCAL_ADMISSION_FAILED','unexpected stage deadlines')
        deadlines=stage_deadlines
    if execution=='bounded-worker':
        require(request['mode']=='M1' and reference_fixture(request)=='1RLW',
                'LOCAL_MODE_UNQUALIFIED','bounded worker requires its original pinned input')
    else:
        require(request['mode'] in ('M1','M2'),
                'LOCAL_MODE_UNQUALIFIED','trusted-local M1/M2 reference required')
    require(sys.version_info[:3] == (3,14,10) and os.geteuid() != 0
            and (execution == 'trusted-local' or os.cpu_count() == 16),
            'SCIENTIFIC_RUNTIME_MISMATCH', 'qualified modern worker and unchanged 15-thread HHblits policy required')
    verify_python_versions('science')
    runtime_root = Path(runtime_root)
    require(os.statvfs(runtime_root).f_flag & os.ST_RDONLY, 'LOCAL_ADMISSION_FAILED', 'runtime must be immutable')
    bundle.verify(runtime_root)
    require(database_verification.get('manifest_sha256') == FINGERPRINT and database_verification.get('mount') == '/database'
            and Path(database_prefix) == Path('/database')/PREFIX,
            'DATABASE_UNVERIFIED', 'verified original read-only local database required')
    scopes = {'preparation_charge','preparation_structure','features','descriptors','classifier_compatibility'}
    require(set(profiles) == scopes, 'SCIENTIFIC_EXECUTION_UNAPPROVED', 'five admitted modern scopes required')
    for scope, profile in profiles.items():
        require(isinstance(profile,ExecutionProfile),'SCIENTIFIC_EXECUTION_UNAPPROVED','typed stage profile required')
        profile.require_scientific_admission()
        require(profile.data['scope'] == scope and profile.data['parameters']['recipe_sha256'] == recipe_sha256()
                and profile.data['inputs']['fixture']['sha256'] == request['source']['sha256'],
                'SCIENTIFIC_EXECUTION_UNAPPROVED', 'modern stage/source profile mismatch')
    require(profiles['classifier_compatibility'].data['parameters'].get('model_deserialization_authorized') is True,
            'SCIENTIFIC_EXECUTION_UNAPPROVED', 'original model execution admission missing')
    if execution == 'trusted-local':
        raw_source=_read(Path(request_directory)/request['source']['path'])
        if request['source']['format']=='pdb':
            source=read_pdb_source(raw_source,expected_sha256=request['source']['sha256'])
        else:
            from .modern_source import read_source
            source = read_source(raw_source,expected_sha256=request['source']['sha256'],runtime_sha256=bundle.sha256)
            from .modern_source import verify_entry_id
            if request['pdb_id']:
                verify_entry_id(raw_source,request['pdb_id'])
    else:
        source = load_1rlw_source_packet(source_packet)
    require(source.original == _read(Path(request_directory)/request['source']['path']),
            'SOURCE_INVALID', 'source packet/request bytes differ')
    chains = request['chain_selection']['ids'] if request['chain_selection'] else None
    generic = source.sha256 not in (CIF_1RLW_SHA256, PDB_1RLW_SHA256) or chains is not None
    if isinstance(source, PdbSource):
        if len(source.model_ids)>1 and trusted_local():
            from dreamm_ensemble.frames import first_pdb_frame
            bridge=first_pdb_frame(source)
        else:
            bridge=source.original
    elif generic and trusted_local():
        from dreamm_m1.identity import CIF_3RUK_SHA256
        if source.sha256==CIF_3RUK_SHA256 and chains==['A']:
            from dreamm_m1.scaling_fixtures import legacy_3ruk_bridge
            bridge=legacy_3ruk_bridge(source)
        else:
            from dreamm_ensemble.frames import project_mmcif
            from dataclasses import replace
            reference = replace(source, atoms=source.reference_atoms, model_ids=source.model_ids[:1])
            bridge = project_mmcif(reference)[0].original
    else:
        bridge = reference_bridge(source)[0]
    general = generic and trusted_local()
    expected_residues = admit_reference(bridge) if generic else 126
    roles = {key:runtime_root/value for key,value in bundle.data['roles'].items()}
    tools = {'preparation_charge':'charge_python','preparation_structure':'science_python',
             'features':'hhblits','descriptors':'protdcal_jar','classifier_compatibility':'science_python'}
    for scope,role in tools.items():
        require(profiles[scope].data['tool']['executable_sha256'] == bundle.data['files'][bundle.data['roles'][role]]['sha256']
                and profiles[scope].data['python']['interpreter_sha256'] == bundle.data['files'][bundle.data['roles']['science_python']]['sha256']
                and profiles[scope].data['dependencies']['lock_sha256'] == bundle.data['files'][bundle.data['roles']['dependency_lock']]['sha256'],
                'SCIENTIFIC_EXECUTION_UNAPPROVED','tool/interpreter/dependency-lock profile mismatch')
    model, reference, zeroed = (_read(roles[key], limit) for key,limit in
        (('model',1024**2),('reference',16*1024**2),('zeroed',32768)))
    verify_reference_assets(reference,zeroed,profile_bytes)
    root = Path(work).absolute();_parents(root.parent);root.mkdir(mode=0o700)
    packet_copy = root/'source-packet';packet_copy.mkdir(mode=0o700)
    if execution == 'trusted-local':
        if len(source.model_ids) > 1:
            from dataclasses import replace
            receipts = replace(source, atoms=source.reference_atoms).receipts()
        else:
            receipts = source.receipts()
        (packet_copy/('source.pdb' if isinstance(source,PdbSource) else 'source.cif')).write_bytes(source.original)
        for key in ('source_models','source_atoms','source_residues'):
            value=receipts[key]
            # For large model collections keep full original bytes and model
            # inventory, with detailed atom/residue rows for the selected model.
            if generic and len(source.model_ids)>1:
                if key=='source_atoms':value=[row for row in value if row['model_index']==0]
                if key=='source_residues':value={uid:row for uid,row in value.items() if row['model_index']==0}
            (packet_copy/(key+'.json')).write_bytes(canonical(value))
        (packet_copy/'parser_runtime.json').write_bytes(canonical(receipts.get('parser_runtime',
            {'schema':'dreamm-pdb-parser/1','implementation':'unchanged_fixed_column_PDB_reader','source_sha256':source.sha256})))
    else:
        for name in _HASHES:
            (packet_copy/name).write_bytes(_read(Path(source_packet)/name))
    (root/'scientific-profile.json').write_bytes(canonical({'schema':'dreamm-modern-m1-profile/1',
        'runtime_manifest_sha256':bundle.sha256,'recipe_sha256':recipe_sha256(),'source_sha256':source.sha256,
        'reference_frame_index':0,'chain_selection':chains,
        'fresh_feature_extraction':True,'frozen_feature_or_label_fallback':False,
        'execution_boundary':execution,
        'stage_deadlines_seconds':deadlines,
        'versions':bundle.data['versions'],'stages':{scope:profile.data for scope,profile in profiles.items()}}))
    extraction = root/'extraction'
    (root/'bridge.pdb').write_bytes(bridge)
    config = root/'preparation-request.json'
    config.write_bytes(canonical({'input':str(root/'bridge.pdb'),'input_sha256':digest(bridge),'work':str(extraction),
        'chains':chains,'charge_timeout':min(180,deadlines['preparation']) if stage_deadlines is None else deadlines['preparation'],
        'general':general,
        'project_blank_pdb_segment':generic and (not isinstance(source,PdbSource)
            or all(not a.locator.segment for a in source.reference_atoms))}))
    descriptor_runtime = {key:str(roles[role]) for key,role in
        (('java','java'),('python','descriptor_python'),('jar','protdcal_jar'),('helper','helper'),
         ('kernel','kernel'),('tables','weights'),('schema_json','schema_json'),('schema_tsv','schema_tsv'))}
    descriptor_runtime.update({key:bundle.data['files'][bundle.data['roles'][role]]['sha256']
                              for key,role in (('jar_sha256','protdcal_jar'),('helper_sha256','helper'))})
    recorder = StageRecorder(evidence_class='scientific_unvalidated')
    try:
        with recorder.activate(), bind_tools(native,{**roles,'pdb2pqr':roles['charge_python']},database_prefix):
            measured('preparation.modern_two_pass')(native._tool)(stage_argv(roles['science_python'],'preparation',config),
                root,'preparation',deadlines['preparation'])
            prepared,bindings,ca,charges = preparation_outputs(source,extraction,expected_residues,
                                                               general=general,chains=chains)
            structure,dssp = native._dssp(extraction,bindings,deadlines['dssp'])
            sasa = native._sasa(extraction,bindings,deadlines['freesasa'])
            depth = native._depth(structure,extraction,bindings,deadlines['msms'])
            if not trusted_local():
                faces,vertices = validate_mesh(_read(extraction/'msms-surface.face'),_read(extraction/'msms-surface.vert'),
                    xyzr=extraction/'surface.xyzr',atom_count=len(_pdb(prepared).atoms))
                (extraction/'mesh.json').write_bytes(canonical({'faces':faces,'vertices':vertices}))
            if execution == 'trusted-local':
                from .trusted_science import conservation as fixed_conservation
                conservation = fixed_conservation(extraction,dssp,bindings,deadlines['hhblits'])
            else:
                conservation = native._conservation(extraction,dssp,bindings,deadlines['hhblits'],15)
            config = root/'dynamics-request.json'
            config.write_bytes(canonical({'work':str(extraction),'bindings':[
                {'locator':vars(loc),'source':vars(res)} for loc,res in bindings.items()]}))
            measured('features.modern_dynamics_process')(native._tool)(stage_argv(roles['dynamics_python'],'dynamics',config),
                root,'dynamics',deadlines['dynamics'])
            rows = _json(_read(extraction/'dynamics.json'))
            dynamics = {Locator(**r['locator']):r['values'] for r in rows}
            require(len(rows) == len(dynamics) and dynamics.keys() == bindings.keys(),
                    'FEATURE_MAPPING_UNPROVEN','complete unique dynamics coverage required')
            charge_features = native._charge_features(extraction,bindings,charges['rows'])
            sequence = {loc:{'Conservation score':value} for loc,value in conservation.items()}
            python_csv = native._assemble(dssp,ca,sasa,depth,sequence,dynamics,charge_features)
            (extraction/'python-features.csv').write_bytes(python_csv)
            stem='reference_Prot.pdb' if generic else '1rlw_Prot.pdb'
            adapter = descriptor_adapter(prepared,bindings,source_mapping_evidence_ref='preparation_map',stem=stem)
            descriptors = native._descriptors(extraction,adapter,_read(roles['project']),descriptor_runtime,
                                              deadlines['protdcal'],whole_stage=True)
            (extraction/'descriptor-stage.json').write_bytes(descriptors.receipt_bytes)
            result = prepare_m1(source=source.original,expected_source_sha256=source.sha256,prepared=prepared,
                bindings=bindings,python_rows=read_python_features(python_csv,adapter),descriptors=descriptors,
                profile_bytes=profile_bytes,pdb_id=None if generic else '1RLW',adapter_stem=adapter.stem,
                admitted_single_chain=generic and execution=='trusted-local',
                admitted_local=general)
            for name in ('adapter_map','join','unscaled'):
                (extraction/(name+'.json')).write_bytes(result.artifacts[name])
    finally:
        if extraction.is_dir():(extraction/'timings.json').write_bytes(canonical(recorder.receipt()))
    from .modern_inference import ROLES
    checkpoint = canonical({'schema':'dreamm-modern-feature-checkpoint/1','source_sha256':source.sha256,
        'files':{role:{'name':name,'bytes':len(result.artifacts[role]),'sha256':digest(result.artifacts[role])}
                 for role,name in ROLES.items()}})
    (extraction/'checkpoint.json').write_bytes(checkpoint)
    prediction_dir = root/'prediction'
    config = root/'inference-request.json'
    config.write_bytes(canonical({'checkpoint':str(extraction),'sha256':digest(checkpoint),'model':str(roles['model']),
        'reference':str(roles['reference']),'zeroed':str(roles['zeroed']),'output':str(prediction_dir)}))
    native._tool(stage_argv(roles['science_python'],'inference',config),root,'inference',deadlines['inference'])
    prediction = {name:_read(prediction_dir/name) for name in ('scaling.json','labels.json','membrane.csv')}
    require((prediction_dir/'complete.json').is_file(),'LOCAL_WORKER_FAILED','inference completion receipt missing')
    return result,prediction
