"""Pinned inputs and preparation identity checks for the two serial benchmarks."""
from dataclasses import dataclass, replace
from decimal import Decimal
from io import StringIO

from .identity import AA, CIF_3RUK_SHA256, O95425_SHA256, PREPARED_3RUK_SHA256, SourceResidue, canonical, digest, require
from .pdb_input import read_pdb_source


JAVA_3RUK_SHA256 = "3c6dc4b8e23ce8bfc055646d8075123e11355b2edd5db3d9d599c299f10a9f65"
STEM_3RUK = "3RUK_20261003033543_chainA_fixed.pdb_Prot.pdb"
STEM_O95425 = "AFO954252F1model_v6_20261020130308_Prot.pdb"
PREPARED_O95425_SHA256 = "c2640584aa2699f8acc0652c4245c3136bde6cb91b1d6c6e5a7813699ebcfa80"
ALT_3RUK = {(num, "ASP", name) for num in ("137", "470") for name in ("CA", "CB", "CG", "OD1", "OD2")}


@dataclass(frozen=True)
class Frozen3Ruk:
    original: bytes

    def __post_init__(self):
        require(isinstance(self.original, bytes) and len(self.original) == 1574110
                and self.sha256 == CIF_3RUK_SHA256, "SOURCE_INVALID", "frozen 3RUK mmCIF bytes required")

    @property
    def sha256(self):
        return digest(self.original)


def frozen_o95425(raw):
    source = read_pdb_source(raw, expected_sha256=O95425_SHA256)
    atoms = source.atoms
    ca = {a.locator for a in atoms if a.atom_name_raw.strip() == "CA"}
    require(len(source.model_ids) == 1 and len(atoms) == 14108 and len(ca) == 1788
            and {a.locator for a in atoms} == ca and all(
                a.record == "ATOM" and a.locator.chain == "A" and a.locator.insertion == " "
                and not a.locator.segment and a.altloc_raw == " " and a.residue.source_resname in AA for a in atoms),
            "PREPARATION_INPUT_INVALID", "frozen O95425-2 protein identity")
    return source


def legacy_3ruk_bridge(source):
    """Repeat the reviewed model-0 Bio.PDB bridge. No serial can exceed 99999."""
    from Bio.PDB import MMCIFParser, PDBIO
    structure = MMCIFParser(QUIET=True).get_structure("MKBRIDGE", StringIO(source.original.decode("ascii")))
    require(len(structure) == 1 and all(len(r.resname.strip()) <= 3 for r in structure.get_residues()),
            "PREPARATION_INPUT_INVALID", "unexpected 3RUK model or unrepresentable residue")
    io = PDBIO()
    io.set_structure(structure[0])
    buffer = StringIO()
    io.save(buffer)
    return "".join(line for line in buffer.getvalue().splitlines(keepends=True)
                   if not line.startswith(("TER", "CONECT"))).encode("ascii")


def _key(atom):
    return atom.locator.resnum, atom.residue.source_resname, atom.atom_name_raw.strip()


def _xyz(atom):
    return tuple(map(Decimal, atom.xyz_raw))


def identity_segment(pdb, expected_atoms, *, altlocs=frozenset()):
    """Normalize the writer's synthetic segment in the identity view only."""
    require(len(pdb.model_ids) == 1 and len(pdb.atoms) == expected_atoms and
            {a.locator.segment for a in pdb.atoms} in ({"0"}, {""}) and all(
                a.record == "ATOM" and a.locator.chain == "A" and a.locator.insertion == " "
                and a.residue.source_resname in AA and a.altloc_raw in (" ", "A") for a in pdb.atoms),
            "PREPARATION_MAPPING_UNPROVEN", "unexpected pinned-fixture pre-charge layout")
    atoms = tuple(replace(a, locator=replace(a.locator, segment="")) for a in pdb.atoms)
    require(len({_key(a) for a in atoms}) == len(atoms) and {_key(a) for a in atoms if a.altloc_raw == "A"} == altlocs,
            "IDENTITY_AMBIGUOUS", "unexpected alternate locations or projected atom collision")
    return replace(pdb, atoms=atoms)


def selected_3ruk_source(source, bridge, before):
    """Require source, bridge and actual filtered molecule to select the same exact atoms."""
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    table = MMCIF2Dict(StringIO(source.original.decode("ascii")))
    source_sha = source.sha256
    names = ("group_PDB id auth_asym_id auth_seq_id auth_comp_id auth_atom_id label_alt_id label_asym_id "
             "label_seq_id pdbx_PDB_ins_code pdbx_PDB_model_num type_symbol Cartn_x Cartn_y Cartn_z").split()
    columns = [table["_atom_site." + name] for name in names]
    require(len({len(col) for col in columns}) == 1, "SOURCE_INVALID", "ragged frozen atom-site loop")
    selected, alternate_b, residues = {}, {}, {}
    for group, atom_id, chain, num, name, atom, alt, label_chain, label_num, insertion, model, element, x, y, z in zip(*columns):
        if group != "ATOM" or chain != "A":
            continue
        key = num, name, atom
        require(model == "1" and insertion in (".", "?") and name in AA and alt in (".", "?", "A", "B"),
                "SOURCE_INVALID", "unexpected 3RUK source identity")
        residue = SourceResidue(source_sha, 0, model, chain, num, insertion, label_chain, label_num, None, name)
        require(residues.setdefault(num, residue) == residue, "IDENTITY_AMBIGUOUS", "3RUK author/label residue collision")
        target = alternate_b if alt == "B" else selected
        require(key not in target, "IDENTITY_AMBIGUOUS", "duplicate 3RUK source atom")
        target[key] = (atom_id, alt if alt in ("A", "B") else " ", element, (x, y, z))
    require(len(residues) == 464 and len(selected) == 3700 and alternate_b.keys() == ALT_3RUK
            and {k for k, v in selected.items() if v[1] == "A"} == ALT_3RUK,
            "PREPARATION_MAPPING_UNPROVEN", "3RUK frozen source counts or alternate identities differ")

    bridge_map = {(_key(a), a.altloc_raw): a for a in bridge.atoms if a.record == "ATOM" and a.locator.chain == "A"}
    require(len(bridge_map) == 3710, "PREPARATION_MAPPING_UNPROVEN", "3RUK bridge must retain both alternates")
    for group in (selected, alternate_b):
        for key, (_, alt, element, xyz) in group.items():
            a = bridge_map.get((key, alt))
            require(a is not None and a.element_raw.strip() == element and _xyz(a) == tuple(map(Decimal, xyz)),
                    "PREPARATION_MAPPING_UNPROVEN", "3RUK Bio.PDB bridge changed source atom identity or coordinates")

    pre = identity_segment(before, 3700, altlocs=ALT_3RUK)
    projected = []
    for a in pre.atoms:
        row = selected.get(_key(a))
        require(row is not None and a.altloc_raw == row[1] and a.element_raw.strip() == row[2]
                and _xyz(a) == tuple(map(Decimal, row[3])),
                "PREPARATION_MAPPING_UNPROVEN", "3RUK actual filter differs from recorded A-and-blank selection")
        projected.append(replace(a, residue=residues[a.locator.resnum],
                                 uid="a_" + digest(canonical([source_sha, 0, row[0]])), xyz_raw=row[3]))
    receipt = {"source_sha256": source_sha, "bridge_sha256": bridge.sha256, "before_sha256": before.sha256,
               "author_chain": "A", "source_chain_A_atoms": 3710, "retained_atoms": 3700,
               "alternate_pairs": [{"author_key": list(key), "retained_source": selected[key],
                                    "excluded_source": alternate_b[key]} for key in sorted(ALT_3RUK)]}
    return replace(pre, original=source.original, atoms=tuple(projected)), receipt


def exact_prepared_projection(raw, fixture):
    """Drop only the synthetic writer segment, and require the archived PDB hash."""
    if fixture == "3RUK":
        atoms, altlocs, expected = 7483, ALT_3RUK, PREPARED_3RUK_SHA256
    else:
        require(fixture == "O95425-2", "SOURCE_INVALID", "unknown fixture")
        atoms, altlocs, expected = 28036, frozenset(), PREPARED_O95425_SHA256
    identity_segment(read_pdb_source(raw, expected_sha256=digest(raw)), atoms, altlocs=altlocs)
    projected = b"".join(line[:72] + b"    " + line[76:] if line.startswith(b"ATOM  ") else line
                         for line in raw.splitlines(keepends=True))
    require(digest(projected) == expected, "PREPARATION_MAPPING_UNPROVEN",
            fixture + " prepared PDB differs from the pinned archived tool input")
    return projected


def verify_3ruk_charge_altlocs(before, rows):
    """PQR lacks altlocs. Prove the ten coordinates against uniquely selected input atoms."""
    selected = {_key(a): a for a in before.atoms if a.altloc_raw == "A"}
    verified = {}
    for row in rows:
        key = row["resnum_raw"], {"ASH": "ASP"}.get(row["resname"], row["resname"]), row["atom_name"]
        if key in selected:
            require(_xyz(selected[key]) == tuple(map(Decimal, row["xyz_raw"])),
                    "CHARGE_MAPPING_UNPROVEN", "3RUK PQR changed a retained alternate atom coordinate")
            verified[key] = row["pqr_atom_uid"]
    require(verified.keys() == ALT_3RUK, "CHARGE_MAPPING_UNPROVEN", "3RUK PQR alternate atom coverage")
    return [{"author_key": list(key), "pqr_atom_uid": verified[key], "retained_altloc": "A"} for key in sorted(verified)]
