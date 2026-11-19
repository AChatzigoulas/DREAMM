"""Explicit prepared-PDB -> Java row -> Python row correspondence.

Source -> prepared bindings must come from reviewed preparation instrumentation.
This module never guesses that mapping from counts, sequences or coordinates.
"""
from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import io
import json
import math
import re
from types import MappingProxyType

MODEL_SHA256 = "b68b031bce885a2b7f572205a42ff0986947b1e62a788dbde409a5e0b379b90d"
AA = dict(zip("ALA ARG ASN ASP CYS GLU GLN GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL".split(),
              "ARNDCEQGHILKMFPSTWYV"))
MAX_BYTES, MAX_ROWS, MAX_COLUMNS = 8 * 1024**2, 20000, 4096
PDB_8YOQ_SHA256 = "a6abe2548c3fd9d3a1608f75254893808f0eb1bc3312148793371498678340a2"
PDB_2KS4_REFERENCE_SHA256 = "bc4702c824f8347d064ec61f16316aa5f34f6275d14a6059e9e8f4064f1a4760"
O95425_SHA256 = "341c084f3cd5232d8bc3f66a0713009a4e48184bfc2b03813f440450fd23b959"
CIF_3RUK_SHA256 = "f22902309efbbf1c36a3a6530eb19d22dd614baccc8ba4f0575d45b71649102b"
PREPARED_3RUK_SHA256 = "ea73c9d921aed00a53074fb6440dcf2619cdbe3ae3bb370e39169a7d9d4eca90"


class M1Error(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(f"{code}: {detail}")


def require(condition, code, detail):
    if not condition:
        raise M1Error(code, detail)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def sha(value):
    require(isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value),
            "SOURCE_INVALID", "expected SHA256")
    return value


@dataclass(frozen=True, order=True)
class Locator:
    chain: str
    resnum: str
    insertion: str = " "
    segment: str = ""

    @property
    def residue_label(self):
        return residue_label(self.resnum, self.insertion)


def residue_label(resnum, insertion=" "):
    """Preserve numeric labels for standard PDB rows and explicit suffixes otherwise."""
    require(not insertion.strip() or re.fullmatch(r"[A-Za-z]", insertion),
            "IDENTITY_UNREPRESENTABLE", "PDB insertion code is not representable in residue outputs")
    number = int(resnum)
    return str(number) + insertion if insertion.strip() else number


def residue_label_parts(label):
    match = re.fullmatch(r"(-?[0-9]+)([A-Za-z]?)", str(label))
    require(match, "IDENTITY_UNREPRESENTABLE", "invalid author residue label")
    return int(match[1]), match[2] or " "


@dataclass(frozen=True)
class SourceResidue:
    source_sha256: str
    model_index: int
    model_id: str | None
    auth_chain: str
    auth_seq: str
    insertion_raw: str | None
    label_chain: str | None
    label_seq: str | None
    segment_raw: str | None
    source_resname: str
    converted_resname: str | None = None

    def __post_init__(self):
        if self.converted_resname is None:
            object.__delattr__(self, "converted_resname")
        else:
            require(self.converted_resname in AA and self.source_resname not in AA,
                    "PREPARATION_MAPPING_UNPROVEN", "invalid documented residue conversion")
        sha(self.source_sha256)
        require(type(self.model_index) is int and self.model_index >= 0,
                "SOURCE_INVALID", "model index")
        for name, value in vars(self).items():
            if name not in ("model_index", "source_sha256"):
                require(value is None or (isinstance(value, str) and len(value) <= 256
                        and not any(ord(c) < 32 for c in value)), "SOURCE_INVALID", "identity string")
        require(bool(self.auth_seq) and bool(self.source_resname), "SOURCE_INVALID", "missing identity")

    @property
    def uid(self):
        source_fields = {name: value for name, value in vars(self).items() if name != "converted_resname"}
        return "r_" + digest(canonical(source_fields))

    @property
    def scientific_resname(self):
        return self.converted_resname or self.source_resname


def pdb_atoms(data):
    require(isinstance(data, bytes) and 0 < len(data) <= MAX_BYTES, "SOURCE_INVALID", "PDB size")
    from .local_budget import trusted_local
    retained_3ruk_altlocs = digest(data) == PREPARED_3RUK_SHA256
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise M1Error("SOURCE_INVALID", "PDB must be ASCII") from exc
    atoms, model_count, in_model, ended_model = [], 0, False, False
    seen = set()
    residue_blocks, previous_key = set(), None
    for n, line in enumerate(lines, 1):
        require(len(line) <= 160 and "\x00" not in line, "SOURCE_INVALID", "PDB line")
        if line.startswith("MODEL "):
            model_count += 1
            require(model_count == 1 and not atoms, "FRAME_SET_INVALID", "prepared reference must be one model")
            in_model = True
        elif line.startswith("ENDMDL"):
            require(in_model, "FRAME_SET_INVALID", "ENDMDL without MODEL")
            in_model, ended_model = False, True
        elif line.startswith("HETATM"):
            raise M1Error("SOURCE_INVALID", "preparation must explicitly resolve HETATM; no implicit filtering")
        elif line.startswith("ATOM  "):
            require(not ended_model and (not model_count or in_model), "FRAME_SET_INVALID", "atom outside reference")
            require(len(line) >= 54, "SOURCE_INVALID", "short ATOM")
            require(line[16:17] == " " or ((retained_3ruk_altlocs or trusted_local()) and line[16:17] == "A"),
                    "IDENTITY_AMBIGUOUS", "altloc must be resolved by recorded preparation")
            key = Locator(line[21:22], line[22:26].strip(), line[26:27], line[72:76].strip())
            require(re.fullmatch(r"-?[0-9]+", key.resnum), "SOURCE_INVALID", "PDB residue number")
            residue = line[17:20]
            require(residue in AA, "SOURCE_INVALID", "processed residue must have explicit standard identity")
            xyz = [line[a:b].strip() for a, b in ((30, 38), (38, 46), (46, 54))]
            require(all(re.fullmatch(r"-?[0-9]+\.[0-9]{3}", x) and math.isfinite(float(x)) for x in xyz),
                    "SOURCE_INVALID", "PDB coordinates/precision")
            atom_id = (key, line[12:16])
            if key != previous_key:
                require(key not in residue_blocks, "IDENTITY_AMBIGUOUS", "noncontiguous residue block")
                residue_blocks.add(key)
                previous_key = key
            require(atom_id not in seen, "IDENTITY_AMBIGUOUS", "duplicate atom/segment locator")
            seen.add(atom_id)
            atoms.append((n, key, residue, line.ljust(80)))
    require(atoms and len(atoms) <= 100000 and not in_model, "SOURCE_INVALID", "empty/unclosed/oversized reference")
    return atoms


@dataclass(frozen=True)
class Link:
    locator: Locator
    residue: SourceResidue
    processed_resname: str
    tool_chain: str
    tool_resnum: str
    aa_id: str
    atom_lines: tuple


@dataclass(frozen=True)
class Adapter:
    prepared_sha256: str
    data: bytes
    links: tuple
    source_mapping_evidence_ref: str
    stem: str

    @property
    def receipt(self):
        return {"schema": "dreamm-m1-adapter/1", "prepared_sha256": self.prepared_sha256,
                "adapter_sha256": digest(self.data), "source_mapping_evidence_ref": self.source_mapping_evidence_ref,
                "stem": self.stem,
                "mapping_method": "construction_time_atom_line_map_no_coordinate_guess",
                "residues": [{"residue_uid": l.residue.uid, "source": vars(l.residue),
                              "python_locator": vars(l.locator), "tool_chain": l.tool_chain,
                              "tool_resnum": l.tool_resnum, "tool_insertion": " ",
                              "processed_resname": l.processed_resname, "AA": l.aa_id,
                              "prepared_atom_lines": list(l.atom_lines)} for l in self.links]}


def descriptor_adapter(prepared, bindings, *, source_mapping_evidence_ref, stem="reference_Prot.pdb", chain_map=None):
    """Build a separate dense-0-based PDB plus inverse atom/residue receipt.

    Retains ATOM order, names, coordinates, occupancy and other atom fields.
    Only chain, residue number and insertion fields change. Never overwrites a
    structure. Segment->chain decisions must be supplied explicitly, not guessed.
    This adapter policy must be compared to legacy MoleculeKit on real 1RLW.
    """
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.pdb", stem), "SOURCE_INVALID", "internal PDB stem")
    require(isinstance(source_mapping_evidence_ref, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", source_mapping_evidence_ref),
            "DESCRIPTOR_MAPPING_UNPROVEN", "preparation evidence reference required")
    atoms = pdb_atoms(prepared)
    keys = list(dict.fromkeys(a[1] for a in atoms))
    require(len(keys) <= 9999 and set(keys) == set(bindings), "DESCRIPTOR_MAPPING_UNPROVEN", "exact prepared binding set required")
    require(all(isinstance(x, SourceResidue) for x in bindings.values()), "DESCRIPTOR_MAPPING_UNPROVEN", "typed source bindings")
    if digest(prepared) == PREPARED_3RUK_SHA256:
        require(len(keys) == 464 and all(x.source_sha256 == CIF_3RUK_SHA256 for x in bindings.values()),
                "DESCRIPTOR_MAPPING_UNPROVEN", "3RUK alternate locations require their archived source bindings")
    require(len({x.uid for x in bindings.values()}) == len(keys), "IDENTITY_AMBIGUOUS", "source binding is not bijective")
    require(len({(x.source_sha256, x.model_index, x.model_id) for x in bindings.values()}) == 1,
            "FRAME_SET_INVALID", "mixed source references")
    require(all(x.model_index == 0 for x in bindings.values()), "FRAME_SET_INVALID", "M1 reference is first model")
    if chain_map is None:
        chain_map = {key: key.chain for key in keys}
        require(all(len({k.segment for k in keys if k.chain == key.chain}) == 1 for key in keys),
                "IDENTITY_AMBIGUOUS", "multiple segments need explicit chain policy")
    require(set(chain_map) == set(keys), "DESCRIPTOR_MAPPING_UNPROVEN", "chain map coverage")
    require(all(re.fullmatch(r"[A-Za-z0-9]", c) for c in chain_map.values()), "IDENTITY_AMBIGUOUS", "Java chain must be explicit ASCII token")
    # Distinct prepared chain/segment groups must not silently collapse.
    groups = {}
    for key in keys:
        groups.setdefault(chain_map[key], set()).add((key.chain, key.segment))
    require(all(len(g) == 1 for g in groups.values()), "IDENTITY_AMBIGUOUS", "chain/segment collapse")
    inverse_groups = {}
    for key in keys:
        inverse_groups.setdefault((key.chain, key.segment), set()).add(chain_map[key])
    require(all(len(g) == 1 for g in inverse_groups.values()), "IDENTITY_AMBIGUOUS", "one segment split across Java chains")
    atom_groups = {}
    for atom in atoms:
        atom_groups.setdefault(atom[1], []).append(atom)
    links = []
    for index, key in enumerate(keys):
        rows = atom_groups[key]
        require(len({a[2] for a in rows}) == 1, "IDENTITY_AMBIGUOUS", "mixed residue names")
        require(sum(a[3][12:16].strip() == "CA" for a in rows) == 1,
                "IDENTITY_AMBIGUOUS", "each prepared residue needs exactly one CA")
        name = rows[0][2]
        links.append(Link(key, bindings[key], name, chain_map[key], str(index),
                          f"{stem}_{chain_map[key]}_{name}_{index}", tuple(a[0] for a in rows)))
    bykey = {l.locator: l for l in links}
    replacements = {}
    for line_number, key, _, line in atoms:
        link = bykey[key]
        replacements[line_number] = line[:21] + link.tool_chain + f"{int(link.tool_resnum):4d}" + " " + line[27:]
    lines = []
    prior_atom = None
    for line_number, line in enumerate(prepared.decode("ascii").splitlines(), 1):
        if line_number in replacements:
            prior_atom = line, replacements[line_number]
            line = prior_atom[1]
        elif line.startswith("TER"):
            require(prior_atom is not None and line[17:27] == prior_atom[0][17:27],
                    "DESCRIPTOR_MAPPING_UNPROVEN", "TER does not identify its preceding residue")
            line = line[:17] + prior_atom[1][17:27] + line[27:]
            prior_atom = None
        lines.append(line)
    data = ("\n".join(lines) + ("\n" if prepared.endswith(b"\n") else "")).encode("ascii")
    return Adapter(digest(prepared), data, tuple(links), source_mapping_evidence_ref, stem)


@dataclass(frozen=True)
class PythonRow:
    locator: Locator
    aa: str
    values: object
    row_id: str

    def __post_init__(self):
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


@dataclass(frozen=True)
class DescriptorRow:
    aa_id: str
    values: tuple
    line: int
    row_sha256: str


def read_descriptors(data):
    from .local_budget import artifact_bytes
    require(isinstance(data, bytes) and len(data) <= artifact_bytes(), "ARTIFACT_MISSING_OR_MALFORMED", "descriptor size")
    result = []
    try:
        records = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), delimiter="\t", strict=True)
        header = tuple(next(records, ()))
        require(1 < len(header) <= MAX_COLUMNS and header[0] == "AA" and len(set(header)) == len(header)
                and all(header) and all(len(x) <= 256 for x in header),
                "FEATURE_SCHEMA_MISMATCH", "unique descriptor columns with AA first")
        for line, row in enumerate(records, 2):
            require(line <= MAX_ROWS + 1, "RESOURCE_LIMIT", "descriptor row bound")
            require(len(row) == len(header) and all(len(x) <= 256 for x in row), "FEATURE_SCHEMA_MISMATCH", "descriptor width/token")
            result.append(DescriptorRow(row[0], tuple(row[1:]), line, digest(canonical(row))))
    except (UnicodeError, csv.Error) as exc:
        raise M1Error("ARTIFACT_MISSING_OR_MALFORMED", "descriptor TSV") from exc
    return header[1:], tuple(result)


def join_features(adapter, python_rows, descriptor_table):
    """Bijective UID join, preserving Python row order; no positional fallback."""
    require(isinstance(adapter, Adapter), "DESCRIPTOR_MAPPING_UNPROVEN", "construction receipt required")
    columns, descriptors = descriptor_table
    require(0 < len(columns) < MAX_COLUMNS and len(set(columns)) == len(columns),
            "FEATURE_SCHEMA_MISMATCH", "descriptor columns")
    rows = tuple(python_rows)
    require(0 < len(rows) <= MAX_ROWS, "DESCRIPTOR_MAPPING_UNPROVEN", "Python rows")
    require(len({r.row_id for r in rows}) == len(rows) and all(r.row_id for r in rows), "IDENTITY_AMBIGUOUS", "Python row IDs")
    by_locator = {l.locator: l for l in adapter.links}
    require(len({r.locator for r in rows}) == len(rows) and set(r.locator for r in rows) == set(by_locator),
            "DESCRIPTOR_MAPPING_UNPROVEN", "Python row locator coverage/uniqueness")
    expected = {l.aa_id: l for l in adapter.links}
    by_id, duplicates = {}, []
    for row in descriptors:
        require(row.aa_id in expected, "DESCRIPTOR_MAPPING_UNPROVEN", "foreign Java AA identity")
        require(len(row.values) == len(columns), "FEATURE_SCHEMA_MISMATCH", "descriptor width")
        if row.aa_id in by_id:
            previous = by_id[row.aa_id]
            require(previous.values == row.values, "DESCRIPTOR_CONFLICT", "nonidentical rows under same AA key")
            duplicates.append({"kept_line": previous.line, "duplicate_line": row.line, "row_sha256": row.row_sha256})
        else:
            by_id[row.aa_id] = row
    require(set(by_id) == set(expected), "DESCRIPTOR_MAPPING_UNPROVEN", "missing descriptor identities")
    joined = []
    for row in rows:
        link = by_locator[row.locator]
        require(row.aa == AA[link.processed_resname], "DESCRIPTOR_MAPPING_UNPROVEN", "processed residue-name mismatch")
        require(not set(row.values).intersection(columns), "FEATURE_SCHEMA_MISMATCH", "colliding Python/Java features")
        descriptor = by_id[link.aa_id]
        joined.append({"residue_uid": link.residue.uid, "python_row_id": row.row_id,
                       "descriptor_line": descriptor.line, "descriptor_row_sha256": descriptor.row_sha256,
                       "aa": row.aa, "source": vars(link.residue),
                       "values": {**row.values, **dict(zip(columns, descriptor.values))}})
    return {"rows": joined, "duplicates": duplicates, "adapter_receipt": adapter.receipt,
            "ordering": "explicit_python_row_order_each_output_row_bound_to_residue_uid"}
