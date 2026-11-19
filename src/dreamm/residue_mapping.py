"""Source-to-preparation correspondence for selected and converted residues."""
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

from dreamm_m1.identity import AA, Locator, require
from dreamm_m1.mmcif_evidence import _cif_key
from dreamm_m1.pdb_input import PdbSource
from .legacy_residues import LEGACY_RESIDUES

BACKBONE = frozenset(('N', 'CA', 'C', 'O'))


def source_residue_groups(source):
    groups = defaultdict(list)
    for atom in source.reference_atoms:
        if isinstance(source, PdbSource):
            loc, name, alt, xyz = atom.locator, atom.atom_name_raw.strip(), atom.altloc_raw, atom.xyz_raw
        else:
            _, loc, _, name, alt, _ = _cif_key(atom)
            xyz = tuple(atom.field('Cartn_'+axis).value for axis in 'xyz')
        key = loc.chain, int(loc.resnum), loc.insertion
        groups[key].append((atom, name, alt, tuple(map(Decimal, xyz))))
    return groups


def match_preparation(source, before, prepared, chains=None):
    """Bind only exact identities. Never use atom-order or proximity to join."""
    groups = source_residue_groups(source)
    selected_chains = set(chains or ())
    index = {}
    retained, rebuilt, moved = [], [], []
    used = set()
    bindings = {}
    for atom in before.reference_atoms:
        loc = atom.locator
        key = loc.chain, int(loc.resnum), loc.insertion
        require(not selected_chains or loc.chain in selected_chains,
                'PREPARATION_MAPPING_UNPROVEN', 'preparation retained an unselected chain')
        name = atom.atom_name_raw.strip()
        hits = [(a, xyz) for a, n, alt, xyz in groups.get(key, [])
                if n == name and alt == atom.altloc_raw and
                LEGACY_RESIDUES.get(a.residue.source_resname, a.residue.source_resname)
                == atom.residue.source_resname]
        require(len(hits) == 1 and hits[0][1] == tuple(map(Decimal, atom.xyz_raw)),
                'PREPARATION_MAPPING_UNPROVEN', 'filtered atom identity or coordinates differ')
        original = hits[0][0]
        before_key = key, name, atom.altloc_raw
        require(before_key not in index, 'PREPARATION_MAPPING_UNPROVEN', 'ambiguous filtered atom')
        index[before_key] = original, atom
        used.add(original.uid)
        residue = original.residue
        if residue.source_resname != atom.residue.source_resname:
            require(name in BACKBONE, 'PREPARATION_MAPPING_UNPROVEN', 'converted side chain survived mutation')
            residue = replace(residue, converted_resname=atom.residue.source_resname)
        require(bindings.setdefault(key, residue) == residue,
                'PREPARATION_MAPPING_UNPROVEN', 'conflicting source residue at one locator')
    for key, group in groups.items():
        if selected_chains and key[0] not in selected_chains:
            continue
        names = {a.residue.source_resname for a, _, _, _ in group}
        atom_names = {n for _, n, _, _ in group}
        expected_polymer = (
            any(a.record == 'ATOM' and a.residue.source_resname != 'UNK' for a, _, _, _ in group)
            and bool(atom_names & BACKBONE)
        ) or (any(n in LEGACY_RESIDUES for n in names) and BACKBONE <= atom_names)
        if expected_polymer:
            require(key in bindings, 'PREPARATION_MAPPING_UNPROVEN', 'selected polymer residue was dropped')
            bound = bindings[key]
            for atom, name, alt, _ in group:
                if atom.residue.uid != bound.uid or alt not in (' ', 'A'):
                    continue
                if bound.converted_resname is None or name in BACKBONE:
                    require(atom.uid in used, 'PREPARATION_MAPPING_UNPROVEN',
                            'selected polymer atom was dropped before preparation')
    final = {}
    seen_before = set()
    for atom in prepared.reference_atoms:
        loc = atom.locator
        key = loc.chain, int(loc.resnum), loc.insertion
        name = atom.atom_name_raw.strip()
        res = bindings.get(key)
        require(res is not None and res.scientific_resname == atom.residue.source_resname
                and atom.record == 'ATOM',
                'PREPARATION_MAPPING_UNPROVEN', 'prepared residue is foreign or differently named')
        final[loc] = res
        pair = index.get((key, name, atom.altloc_raw))
        detail = {'prepared': atom.uid, 'residue': res.uid, 'name': name, 'locator': vars(loc)}
        if pair is None:
            rebuilt.append(detail)
        else:
            original, prior = pair
            seen_before.add(prior.uid)
            retained.append({**detail, 'source_atom': original.uid, 'filtered_atom': prior.uid})
            if tuple(map(Decimal, atom.xyz_raw)) != tuple(map(Decimal, prior.xyz_raw)):
                moved.append(detail)
    ca = [atom.locator for atom in prepared.reference_atoms if atom.atom_name_raw.strip() == 'CA']
    require(len(ca) == len(set(ca)) == len(bindings) and set(ca) == final.keys(),
            'PREPARATION_MAPPING_UNPROVEN', 'prepared C-alpha or residue coverage differs')
    report = {'schema': 'dreamm-legacy-preparation-correspondence/1',
              'source_sha256': source.sha256, 'before_sha256': before.sha256,
              'prepared_sha256': prepared.sha256,
              'residues': [{'source_uid': res.uid, 'source': vars(res), 'prepared_locator': vars(loc),
                            'prepared_name': res.scientific_resname} for loc, res in final.items()],
              'retained_atoms': retained, 'prepared_only_atoms': rebuilt,
              'moved_retained_atoms': moved,
              'removed_before_preparation': [
                  {'source_atom': a.uid, 'author_locator': list(key), 'record': a.record, 'name': name,
                   'altloc': alt, 'source_residue': a.residue.uid}
                  for key, group in groups.items() for a, name, alt, _ in group if a.uid not in used
              ],
              'removed_during_preparation': [
                  {'source_atom': original.uid, 'filtered_atom': atom.uid}
                  for original, atom in index.values() if atom.uid not in seen_before
              ]}
    return final, report
