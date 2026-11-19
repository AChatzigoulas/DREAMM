"""Export actual completed M1 inference for ensemble reuse, without recalculation."""
from pathlib import Path
import os

from dreamm_m1.identity import AA,MODEL_SHA256,canonical,digest,require
from dreamm_m1.inference import residue_rows,verify_membrane_reference
from dreamm_m1.pdb_input import read_pdb_source
from dreamm_m1.capture import _parents
from .exports import verified_files
from .cli import write_private,sync_directory


def export(args):
    manifest,files=verified_files(args.attempt,load={
        'scientific/extraction/adapter_map.json','scientific/prediction/labels.json',
        'scientific/extraction/conservation.json','scientific/prediction/membrane.csv',
        'scientific/bridge.pdb','scientific/scientific-profile.json','scientific/extraction/prepared.pdb'})
    import json
    adapter=json.loads(files['scientific/extraction/adapter_map.json'])
    labels=json.loads(files['scientific/prediction/labels.json'])
    conservation=json.loads(files['scientific/extraction/conservation.json'])
    conserved={r['source_residue_uid']:r['value'] for r in conservation}
    old_identities=residue_rows({'adapter_map':files['scientific/extraction/adapter_map.json']})
    membrane=files['scientific/prediction/membrane.csv'];old_flags,_=verify_membrane_reference(membrane,old_identities)
    bridge=files['scientific/bridge.pdb'];source=read_pdb_source(bridge,expected_sha256=digest(bridge))
    from dreamm_ensemble.frames import first_pdb_frame
    from .ensemble_source import select_frame
    from .ensemble_source import ca_atoms
    profile = json.loads(files['scientific/scientific-profile.json'])
    chains = profile.get('chain_selection')
    if chains:
        bridge=select_frame(first_pdb_frame(source),chains)
        source=read_pdb_source(bridge,expected_sha256=digest(bridge))
    old={tuple(old_identities[r['residue_uid']]):r['residue_uid'] for r in adapter['residues']}
    require(len(old)==len(adapter['residues']) and set(conserved)==set(old_identities)
            and len(conserved)==len(conservation),'ENSEMBLE_REFERENCE_INVALID','incomplete or ambiguous M1 reference')
    rows=[];mapped=set()
    for a, aa in ca_atoms(source):
        identity=(a.locator.chain,a.locator.residue_label,aa)
        require(identity in old and identity not in mapped,
                'ENSEMBLE_REFERENCE_INVALID','unproven source-to-M1 identity')
        mapped.add(identity);uid=old[identity]
        rows.append({'uid':a.residue.uid,'M1_source_uid':uid,'identity':list(identity),
            'conservation':conserved[uid],'raw_label':labels.get(uid),'broken_chain':old_flags.get(uid)})
    require(mapped==set(old),'ENSEMBLE_REFERENCE_INVALID','source bridge coverage differs')
    payload={'source-model-0.pdb':bridge,'prepared-reference.pdb':files['scientific/extraction/prepared.pdb'],
             'reference-membrane.csv':membrane}
    receipt={'source_sha256':digest(bridge),'original_source_sha256':manifest['source_sha256'],
        'chain_selection':chains,
        'model_sha256':MODEL_SHA256,'predictions':rows,'membrane_calls':len(old_flags),'eligible_residues':len(labels),
        'actual_M1_attempt':manifest['attempt'],'actual_M1_artifact_manifest_sha256':digest(canonical(manifest)),
        'prepared_sha256':digest(payload['prepared-reference.pdb']),'scientific_calculation_performed':False}
    payload['reference-membrane-and-conservation.json']=canonical(receipt)
    data={'schema':'dreamm-reference-checkpoint/1','M1_actual_inference_reused':True,
        'files':{n:{'bytes':len(v),'sha256':digest(v)} for n,v in payload.items()}}
    root=Path(args.output).absolute();_parents(root.parent);root.mkdir(mode=0o700)
    for name,raw in payload.items():write_private(root/name,raw)
    write_private(root/'.checkpoint.pending',canonical(data));os.rename(root/'.checkpoint.pending',root/'checkpoint.json');sync_directory(root)
    return {'output':str(root),'sha256':digest(canonical(data)),'residues':len(rows),'membrane_calls':len(old_flags),
            'scientific_calculation_performed':False}
