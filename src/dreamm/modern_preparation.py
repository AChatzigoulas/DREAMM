"""Versioned two-pass preparation candidate; never substitutes silently for M1.

The external PARSE charge pass and structural systemPrepare pass both consume
the original filtered structure. Residue naming follows the unchanged M1 map.
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import time

from dreamm_m1.identity import AA, O95425_SHA256, PDB_8YOQ_SHA256, canonical
from dreamm_m1.local_budget import source_bytes
from dreamm_m1.pdb_input import read_pdb_source
from dreamm_m1.preparation import PDB2PQR_OPTIONS, POST_NAMES, decimal_serial_adapter
from .modern import filter_reference

SETTINGS = {'titration': True, 'pH': 7.0, 'force_protonation': None, 'no_opt': None,
            'no_prot': None, 'no_titr': None, 'hold_nonpeptidic_bonds': True,
            'verbose': False, 'return_details': True, 'hydrophobic_thickness': None,
            'plot_pka': None, 'titrate': None, 'detect_specs': None, 'guess_bonds': True}


def reference_segments(raw, source_sha256):
    """Produce the exact protected tool PDB, retaining the native file separately.

    MoleculeKit 1.17.19 retains CRYST1 from uploaded PDBs; 1.3.4's writer
    omitted it. Only these two pinned fixture adapters remove that header,
    then require the immutable core's complete original output checksum.
    """
    if source_sha256 in (PDB_8YOQ_SHA256, O95425_SHA256):
        lines = raw.splitlines(keepends=True)
        if sum(line.startswith(b'CRYST1') for line in lines) > 1:
            raise ValueError('ambiguous modern crystal header')
        raw = b''.join(line for line in lines if not line.startswith(b'CRYST1'))
    if source_sha256 == PDB_8YOQ_SHA256:
        from dreamm_m1.native_m1 import _8yoq_legacy_prepared
        return _8yoq_legacy_prepared(raw)
    if source_sha256 == O95425_SHA256:
        from dreamm_m1.scaling_fixtures import exact_prepared_projection
        return exact_prepared_projection(raw, 'O95425-2')
    return raw


def identities(raw):
    parsed = read_pdb_source(raw, expected_sha256=hashlib.sha256(raw).hexdigest())
    atoms = {}
    for atom in parsed.atoms:
        key = (atom.locator.chain, atom.locator.resnum, atom.locator.insertion,
               atom.residue.source_resname, atom.atom_name_raw.strip(), atom.altloc_raw)
        if key in atoms:
            raise ValueError('ambiguous preparation identity; no segment/coordinate guess')
        atoms[key] = atom
    return atoms


def comparison(source, target):
    before, after = identities(source), identities(target)
    common = before.keys() & after.keys()
    return {'source_atoms': len(before), 'prepared_atoms': len(after),
        'identity_matches': len(common), 'source_only': [list(k) for k in sorted(before.keys()-after.keys())],
        'prepared_only': [list(k) for k in sorted(after.keys()-before.keys())],
        'changed_coordinates': [{'identity': list(k), 'source': before[k].xyz_raw,
                                  'prepared': after[k].xyz_raw} for k in sorted(common)
                                 if before[k].xyz_raw != after[k].xyz_raw],
        'mapping': 'exact chain/residue/insertion/resname/atom/altloc; new atom ancestry unproven'}


def prepare_reference(input_path, *, expected_sha256, charge_python, work, chain=None, timeout=180,
                      project_blank_pdb_segment=False, general=False):
    """Execute only with verified runtime paths supplied by an admitted caller."""
    raw = Path(input_path).read_bytes()
    if len(raw) > source_bytes() or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('frozen preparation input differs')
    work = Path(work)
    work.mkdir(mode=0o700)
    molecule = filter_reference(input_path, chain=chain)
    before = work/'before.pdb'
    molecule.write(str(before))
    before.write_bytes(decimal_serial_adapter(before.read_bytes())[0])
    from .writer_identity import project,blank_writer_segment,project_ter_segments
    def project_output(data,phase):
        if general and project_blank_pdb_segment:
            return project_ter_segments(data)
        return blank_writer_segment(data) if project_blank_pdb_segment else project(data,expected_sha256,phase)
    original_before=before.read_bytes()
    canonical_before,before_projection=project_output(original_before,'before')
    if canonical_before!=original_before:
        (work/'before-native.pdb').write_bytes(original_before)
        before.write_bytes(canonical_before)
    input_atoms = identities(before.read_bytes())
    if any(key[3] not in AA for key in input_atoms):
        raise ValueError('nonstandard residues require a separately validated preparation case')
    # Capture the original float32 CA coordinates before preparation. They are
    # the legacy neighbourhood inputs, not coordinates from protonated atoms.
    ca = [{'chain': str(molecule.chain[i]), 'resnum': str(molecule.resid[i]),
           'insertion': str(molecule.insertion[i]).strip() or ' ',
           'xyz': [float(v) for v in molecule.coords[i, :, 0]]}
          for i, name in enumerate(molecule.name) if name == 'CA']
    if len({(r['chain'], r['resnum'], r['insertion']) for r in ca}) != len(ca):
        raise ValueError('ambiguous original CA identity')
    (work/'pre-ca.json').write_bytes(canonical(ca))
    start = time.monotonic()
    argv = [str(charge_python), '-I', '-B', '-m', 'pdb2pqr', *PDB2PQR_OPTIONS, str(before), str(work/'charges.pqr')]
    if general and len(set(molecule.chain))>1:
        argv = [str(charge_python),'-I','-S','-B','-c',
                "import sys;sys.path.insert(0,sys.argv.pop(1));from dreamm.charge_stage import main;main()",
                str(Path(__file__).parents[1]),str(before),str(work/'charges.pqr'),str(work/'charge-serializer.json')]
    with (work/'charge.stdout').open('xb') as out, (work/'charge.stderr').open('xb') as err:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=work, timeout=timeout)
    if result.returncode:
        raise ValueError('external charge pass failed; inspect charge.stderr')
    charge_seconds = time.monotonic()-start
    import moleculekit.tools.preparation as preparation
    original = preparation._pdb2pqr
    invocations = []
    def capture(path, *args, **kwargs):
        internal = Path(path).read_bytes()
        if invocations:
            raise ValueError('unexpected repeated internal preparation charge pass')
        (work/'internal-input.pdb').write_bytes(internal)
        invocations.append({'input_sha256': hashlib.sha256(internal).hexdigest(),
                            'options': {key: value for key, value in kwargs.items()
                                        if value is None or isinstance(value,(str,int,float,bool))}})
        return original(path,*args,**kwargs)
    preparation._pdb2pqr = capture
    start = time.monotonic()
    try:
        # The original filtered molecule is deliberately independent from PQR.
        prepared, detected_specs, details = preparation.systemPrepare(molecule, **SETTINGS)
    finally:
        preparation._pdb2pqr = original
    if len(invocations) != 1:
        raise ValueError('one internal preparation pass required')
    structure_seconds = time.monotonic()-start
    substitutions = []
    for name in set(prepared.resname):
        if name not in AA:
            if name not in POST_NAMES:
                raise ValueError('unapproved prepared residue name: '+name)
            substitutions.append({'from':name,'to':POST_NAMES[name]})
            prepared.resname[prepared.resname==name] = POST_NAMES[name]
    prepared.write(str(work/'prepared.pdb'))
    prepared.write(str(work/'prepared.cif'))
    native = decimal_serial_adapter((work/'prepared.pdb').read_bytes())[0]
    final = reference_segments(native, expected_sha256)
    final,prepared_projection=project_output(final,'prepared')
    if final != native:
        (work/'prepared-native.pdb').write_bytes(native)
    (work/'prepared.pdb').write_bytes(final)
    details.to_csv(work/'protonation.csv',index=False)
    after = (work/'prepared.pdb').read_bytes()
    residues = lambda atoms: {key[:4] for key in atoms}
    if residues(identities(after)) != residues(input_atoms):
        raise ValueError('preparation changed residue identity/coverage')
    receipt = {'schema':'dreamm-modern-preparation/1','profile':'moleculekit11719-two-pass-candidate1',
        'input_sha256':expected_sha256,'chain_selection':chain,'settings':SETTINGS,'external_options':list(PDB2PQR_OPTIONS),
        'external_charge_seconds':charge_seconds,'structure_seconds':structure_seconds,
        'internal_invocations':invocations,'post_names':substitutions,
        'writer_identity':{'before':before_projection,'prepared':prepared_projection,
            'original_filtered_molecule_preserved_for_internal_preparation':True},
        'segment_projection':{'native_sha256':hashlib.sha256(native).hexdigest(),
            'final_sha256':hashlib.sha256(final).hexdigest(),
            'policy':'pinned legacy-tool PDB writer compatibility (CRYST1 and synthetic segment); native retained'},
        'detected_specs':detected_specs,
        'versions':{name:importlib.metadata.version(name) for name in
                    ('moleculekit','acellera-pdb2pqr','acellera-propka')},
        'source_to_prepared':comparison(before.read_bytes(),after),
        'artifacts':{path.name:{'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}
                     for path in work.iterdir() if path.is_file()},
        'scientific_equivalence_accepted':False}
    (work/'preparation.json').write_bytes(canonical(receipt))
    return receipt
