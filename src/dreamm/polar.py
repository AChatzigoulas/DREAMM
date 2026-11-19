"""Remove carbon-bound hydrogens using the installed chemical bond templates."""
from collections import defaultdict
import gzip
from pathlib import Path
import re
from xml.etree import ElementTree

from dreamm_m1.identity import canonical, digest, require
from dreamm_m1.pdb_input import read_pdb_source

# Preparation-generated protonation names share these parent-residue
# heavy-atom graphs. A hydrogen still needs a matching named bond in the CCD.
PROTONATION = {'HID':'HIS','HIE':'HIS','HIP':'HIS','HSD':'HIS','HSE':'HIS','HSP':'HIS',
               'ASH':'ASP','GLH':'GLU','CYM':'CYS','CYX':'CYS','LYN':'LYS'}


def terminal_bonds(path):
    result = defaultdict(set)
    for patch in ElementTree.parse(path).getroot().findall('patch'):
        if patch.findtext('name') not in ('NTERM', 'NEUTRAL-NTERM'):
            continue
        for atom in patch.findall('add/atom'):
            if [bond.text for bond in atom.findall('bond')] == ['N']:
                for name in [atom.findtext('name'), *(alias.text for alias in atom.findall('altname'))]:
                    result[name].add(('N', 'N'))
    return result


def component_bonds(path):
    from gemmi import cif
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        block = cif.read_string(stream.read()).sole_block()
    elements = {name: element for name, element in block.find('_chem_comp_atom.', ['atom_id', 'type_symbol'])}
    parents = defaultdict(set)
    for a, b in block.find('_chem_comp_bond.', ['atom_id_1','atom_id_2']):
        for hydrogen, heavy in ((a,b), (b,a)):
            if elements[hydrogen] == 'H' and elements[heavy] != 'H':
                parents[hydrogen].add((heavy, elements[heavy]))
    return parents


def prepare(raw, ccd, terminal_patches):
    parsed = read_pdb_source(raw, expected_sha256=digest(raw))
    require(len(parsed.model_ids) == 1, 'GENERATION_INVALID', 'polar preparation needs one reference model')
    lines = raw.splitlines(keepends=True)
    by_line = {atom.line: atom for atom in parsed.atoms}
    by_serial = {atom.serial_raw.encode(): atom for atom in parsed.atoms}
    require(len(by_serial) == len(by_line), 'POLAR_TOPOLOGY_UNVERIFIED', 'duplicate PDB atom serial')
    by_fields = defaultdict(list)
    residues = defaultdict(lambda: defaultdict(list))
    starts = set()
    previous_chain = None
    for number, line in enumerate(lines, 1):
        if line.startswith(b'TER'):
            previous_chain = None
        if number in by_line:
            atom = by_line[number]
            by_fields[line[12:27]].append(atom)
            if atom.locator.chain != previous_chain:
                starts.add(atom.residue_key)
                previous_chain = atom.locator.chain
            residues[atom.residue_key][atom.atom_name_raw.strip()].append(atom)

    links = {}
    link_parents = defaultdict(set)
    for number, line in enumerate(lines, 1):
        if line.startswith(b'LINK  '):
            left, right = by_fields[line[12:27]], by_fields[line[42:57]]
            require(len(left) == len(right) == 1, 'POLAR_TOPOLOGY_UNVERIFIED', 'unresolved or ambiguous LINK endpoint')
            a, b = left[0], right[0]
            links[number] = (a.uid, b.uid)
            if (a.element_raw.strip() == 'H') != (b.element_raw.strip() == 'H'):
                require(all(part.strip() in (b'',b'1555') for part in (line[59:65],line[66:72])),
                        'POLAR_TOPOLOGY_UNVERIFIED', 'symmetry-related hydrogen LINK needs review')
                hydrogen, heavy = (a,b) if a.element_raw.strip() == 'H' else (b,a)
                link_parents[hydrogen.uid].add(heavy)

    ccd = Path(ccd)
    terminals = terminal_bonds(terminal_patches)
    templates = {}
    removed = {}
    for atom in parsed.atoms:
        if atom.element_raw.strip() != 'H':
            require(atom.element_raw.strip(), 'POLAR_TOPOLOGY_UNVERIFIED', 'missing element in prepared PDB')
            continue
        resname = atom.residue.source_resname
        component = PROTONATION.get(resname, resname)
        name = atom.atom_name_raw.strip()
        if component not in templates:
            require(re.fullmatch(r'[A-Z0-9]{1,3}', component), 'POLAR_TOPOLOGY_UNVERIFIED', 'invalid CCD component')
            path = ccd/(component+'.cif.gz')
            templates[component] = component_bonds(path) if path.is_file() else {}
        template = templates[component].get(name, set())
        if atom.residue_key in starts:
            template = template | terminals.get(name, set())
        candidates = {parent for parent_name, element in template
                      for parent in residues[atom.residue_key][parent_name]
                      if parent.element_raw.strip() == element and
                      (parent.altloc_raw == atom.altloc_raw or ' ' in (atom.altloc_raw, parent.altloc_raw))}
        explicit = link_parents[atom.uid]
        require(not (explicit and candidates) or explicit == candidates,
                'POLAR_TOPOLOGY_UNVERIFIED', f'conflicting CCD and LINK parents for {resname} {name}')
        candidates |= explicit
        require(len(candidates) == 1, 'POLAR_TOPOLOGY_UNVERIFIED',
                f'no unique bonded parent for {atom.locator.chain}:{atom.locator.residue_label} {resname} {name}')
        parent = next(iter(candidates))
        if parent.element_raw.strip() == 'C':
            removed[atom.uid] = parent.uid

    selected = []
    for number, line in enumerate(lines, 1):
        if number in by_line and by_line[number].uid in removed:
            continue
        if line.startswith((b'ANISOU', b'SIGATM', b'SIGUIJ', b'CONECT')):
            owner = by_serial.get(line[6:11])
            require(owner is not None, 'POLAR_TOPOLOGY_UNVERIFIED', 'orphan atom serial annotation')
            if owner.uid in removed:
                continue
            if line.startswith(b'CONECT'):
                fields = line.rstrip(b'\r\n')[11:]
                serials = [fields[i:i+5] for i in range(0,len(fields),5) if fields[i:i+5].strip()]
                require(all(serial in by_serial for serial in serials), 'POLAR_TOPOLOGY_UNVERIFIED',
                        'unresolved CONECT endpoint')
                kept = [serial for serial in serials if by_serial[serial].uid not in removed]
                if not kept:
                    continue
                line = line[:11]+b''.join(kept)+b'\n'
        if number in links and any(uid in removed for uid in links[number]):
            continue
        # The legacy writer emits a single unwrapped model. Serial numbers
        # and annotations for retained atoms remain as deposited in the prepared input.
        if not line.startswith((b'MODEL ', b'ENDMDL')):
            selected.append(line)
    polar = b''.join(selected)
    return polar, canonical({'schema':'dreamm-polar-ccd/1','input_sha256':digest(raw),
                             'output_sha256':digest(polar),'removed_carbon_hydrogens':removed,
                             'retained_atoms':len(by_line)-len(removed)})
