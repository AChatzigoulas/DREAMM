"""Lossless M3/M4 intake, sharing the reviewed complete-model readers."""
from dreamm_m1.identity import AA, digest, require
from dreamm_m1.pdb_input import read_pdb_source
from dreamm_m1.local_budget import model_count, trusted_local
from dreamm_ensemble.frames import groups, pdb_manifest, project_mmcif


def residue_key(atom):
    return (atom.locator.chain, atom.locator.resnum, atom.locator.insertion,
            atom.residue.source_resname)


def ca_atoms(source):
    from collections import defaultdict
    from .legacy_residues import LEGACY_RESIDUES
    groups = defaultdict(list)
    for atom in source.reference_atoms:
        groups[atom.residue_key].append(atom)
    for atoms in groups.values():
        name = atoms[0].residue.source_resname
        if name == 'UNK':
            continue
        if name not in AA and not (name in LEGACY_RESIDUES and
                {'N','CA','C','O'} <= {a.atom_name_raw.strip() for a in atoms}):
            require(not any(a.record=='ATOM' and a.atom_name_raw.strip()=='CA' for a in atoms),
                    'ENSEMBLE_MAPPING_UNPROVEN','no validated conversion for '+name)
            continue
        candidates = [a for a in atoms if a.atom_name_raw.strip()=='CA' and a.altloc_raw in (' ','A')]
        if name in AA:
            candidates = [a for a in candidates if a.record=='ATOM']
        if candidates:
            require(len(candidates)==1,'ENSEMBLE_MAPPING_UNPROVEN','ambiguous reference C-alpha selection')
            yield candidates[0], AA[LEGACY_RESIDUES.get(name,name)]


def ca_identity(raw):
    source=read_pdb_source(raw,expected_sha256=digest(raw));result={}
    for atom, aa in ca_atoms(source):
        key=f'{atom.locator.chain}_{atom.locator.residue_label}'
        require(key not in result,
                'ENSEMBLE_MAPPING_UNPROVEN','ambiguous or unsupported residue identity')
        result[key]=aa
    require(result,'ENSEMBLE_MAPPING_UNPROVEN','no protein residues');return result

def select_frame(raw, chains):
    if not chains:
        return raw
    source = read_pdb_source(raw, expected_sha256=digest(raw))
    require(len(source.model_ids) == 1, 'ENSEMBLE_INPUT_INVALID', 'one frame required for chain selection')
    selected = {a.line for a in source.reference_atoms if a.locator.chain in chains}
    require(selected and {a.locator.chain for a in source.reference_atoms if a.line in selected} == set(chains),
            'ENSEMBLE_INPUT_INVALID', 'requested chain is missing from the frame')
    lines = raw.splitlines(keepends=True)
    return b''.join(line for index, line in enumerate(lines, 1) if index in selected
                    or (line.startswith(b'TER ') and index - 1 in selected)) + b'END\n'


def prepare_source(raw, *, expected_sha256, mode, runtime_sha256=None, fmt=None, chains=None):
    require(mode in ('M3', 'M4') and digest(raw)==expected_sha256,
            'ENSEMBLE_INPUT_INVALID', 'mode or input checksum differs')
    if (fmt or ('mmcif' if mode=='M3' else 'pdb')) == 'mmcif':
        from .modern_source import read_source
        original=read_source(raw,expected_sha256=expected_sha256,runtime_sha256=runtime_sha256)
        source,crosswalk=project_mmcif(original)
    else:
        source=read_pdb_source(raw,expected_sha256=expected_sha256)
        crosswalk=None
    require(1<=len(source.model_ids)<=model_count(), 'ENSEMBLE_INPUT_INVALID', 'source model capacity exceeded')
    frames=[];selected_ca=[];reference=None;lines=source.original.splitlines(keepends=True)
    for i,atoms in enumerate(groups(source)):
        # Keep exact source record bytes, including all altloc/nonprotein atoms.
        # A later preparation stage must explicitly account for any removal.
        end=atoms[-1].line
        if end<len(lines) and lines[end].startswith(b'TER '):end+=1
        body=b''.join(lines[atoms[0].line-1:end])
        # Only pad TER whitespace in the tool projection. All atom-site bytes
        # remain exact and the entire unmodified source is retained separately.
        body=b''.join(line.rstrip(b' \r\n').ljust(80,b' ')+b'\n' if line.startswith(b'TER ')
                      else line for line in body.splitlines(keepends=True))
        raw_frame=body+b'END\n'
        parsed=read_pdb_source(raw_frame,expected_sha256=digest(raw_frame))
        identity=lambda a:(a.record,residue_key(a),a.atom_name_raw,a.altloc_raw,a.xyz_raw,
                           a.occupancy_raw,a.bfactor_raw,a.element_raw)
        require(list(map(identity,parsed.atoms))==list(map(identity,atoms)),
                'ENSEMBLE_MAPPING_UNPROVEN','frame projection lost an atom or changed its identity')
        frame=select_frame(raw_frame, chains)
        ca=ca_identity(frame)
        if trusted_local():
            selected = read_pdb_source(frame,expected_sha256=digest(frame))
            selected_ca.append([residue_key(atom) for atom, _ in ca_atoms(selected)])
        if reference is None:reference=ca
        require(ca==reference,'ENSEMBLE_MAPPING_UNPROVEN','residue identities differ between frames')
        frames.append(frame)
    receipt={'schema':'dreamm-ensemble-source/1','mode':mode,'source_sha256':expected_sha256,
        'reference_frame_index':0,'frame_count':len(frames),'models':pdb_manifest(source,selected_ca=selected_ca or None),
        'frame_sha256':[digest(f) for f in frames], 'mmcif_atom_crosswalk_sha256':crosswalk,
        'chain_selection':chains,
        'scientific_calculation_performed':False}
    return frames,receipt
