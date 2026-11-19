"""PDB component intake only. Not a replacement for blocked M1 mmCIF intake."""
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from .identity import Locator, SourceResidue, canonical, digest, require, sha
from .measurement import measured

PDB_1RLW_SHA256 = "8389c419c7e5d586ddcbb2e86e805033bc1baf58069c875aaa9c33b1c9ed71bd"


@dataclass(frozen=True)
class PdbAtom:
    uid: str
    line: int
    model_index: int
    record: str
    serial_raw: str
    atom_name_raw: str
    altloc_raw: str
    element_raw: str
    residue: SourceResidue
    locator: Locator
    xyz_raw: tuple
    occupancy_raw: str
    bfactor_raw: str

    @property
    def identity_key(self):
        # Complete identifiers first; coordinates are not used to choose a match.
        return (self.record, self.locator, self.residue.source_resname,
                self.atom_name_raw.strip(), self.altloc_raw, self.element_raw.strip())

    @property
    def residue_key(self):
        return self.locator, self.residue.source_resname

    def receipt(self):
        return {"atom_uid": self.uid, "source_line": self.line, "model_index": self.model_index,
                "record": self.record, "serial_raw": self.serial_raw, "atom_name_raw": self.atom_name_raw,
                "altloc_raw": self.altloc_raw, "element_raw": self.element_raw,
                "residue_uid": self.residue.uid, "locator": vars(self.locator),
                "xyz_raw": self.xyz_raw, "occupancy_raw": self.occupancy_raw, "bfactor_raw": self.bfactor_raw}


@dataclass(frozen=True)
class PdbSource:
    original: bytes
    atoms: tuple
    model_ids: tuple

    @property
    def sha256(self):
        return digest(self.original)

    @property
    def reference_atoms(self):
        return tuple(a for a in self.atoms if a.model_index == 0)

    def receipts(self):
        models = [{"source_model_index": n, "source_model_id": model_id,
                   "atom_count": sum(a.model_index == n for a in self.atoms),
                   "reference_selected": n == 0} for n, model_id in enumerate(self.model_ids)]
        return {"source": {"sha256": self.sha256, "bytes": len(self.original), "format": "pdb",
                           "scope": "PDB_input_component_not_M1_mmcif_validation"},
                "source_models": {"source_sha256": self.sha256, "models": models},
                "source_atoms": [a.receipt() for a in self.atoms],
                "source_residues": {a.residue.uid: vars(a.residue) for a in self.atoms},
                "reference_frame_index": 0, "reference_atom_uids": [a.uid for a in self.reference_atoms],
                "record_counts": dict(Counter(a.record for a in self.atoms)),
                "m1_mmcif_intake": "not_verified_by_PDB_component", "scientific_prediction_performed": False}


@measured("source.parse_PDB")
def read_pdb_source(data, *, expected_sha256):
    """Preserve all ATOM/HETATM, models, altlocs and raw fields; never select a conformer."""
    from .local_budget import model_count, source_atom_count, source_bytes
    sha(expected_sha256)
    byte_limit, row_limit, model_limit = source_bytes(), source_atom_count(), model_count()
    require(isinstance(data, bytes) and 0 < len(data) <= byte_limit and digest(data) == expected_sha256,
            "SOURCE_INVALID", "frozen PDB byte/hash bound")
    require(b"\x00" not in data, "SOURCE_INVALID", "NUL in PDB")
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise ValueError("SOURCE_INVALID: non-ASCII PDB") from exc
    atoms, models, active, explicit, closed = [], [], None, False, False
    keys = set()
    first_atom = 0
    for line_number, line in enumerate(lines, 1):
        require(len(line) <= 256, "SOURCE_INVALID", "PDB line bound")
        if line.startswith("MODEL "):
            require(active is None and (explicit or not atoms), "FRAME_SET_INVALID", "mixed/nested models")
            model_id = line[10:14].strip()
            require(model_id and model_id not in models and len(models) < model_limit,
                    "FRAME_SET_INVALID", "duplicate/missing/too many model IDs")
            explicit = True
            active = len(models)
            models.append(model_id)
            first_atom = len(atoms)
        elif line.startswith("ENDMDL"):
            require(explicit and active is not None, "FRAME_SET_INVALID", "unmatched ENDMDL")
            require(len(atoms) > first_atom, "FRAME_SET_INVALID", "empty model")
            active, closed = None, True
        elif line.startswith(("ATOM  ", "HETATM")):
            require(len(line) >= 54 and len(atoms) < row_limit, "SOURCE_INVALID", "PDB atom budget")
            if explicit:
                require(active is not None, "FRAME_SET_INVALID", "atom outside MODEL")
            elif not models:
                models.append(None)
                active = 0
            require(not closed or explicit, "FRAME_SET_INVALID", "atom after closed implicit model")
            num = line[22:26].strip()
            require(re.fullmatch(r"-?[0-9]+", num), "SOURCE_INVALID", "unsupported PDB residue identifier")
            loc = Locator(line[21:22], num, line[26:27], line[72:76].strip())
            xyz = tuple(line[a:b].strip() for a, b in ((30, 38), (38, 46), (46, 54)))
            try:
                require(all(Decimal(x).is_finite() for x in xyz), "SOURCE_INVALID", "nonfinite PDB coordinate")
            except InvalidOperation as exc:
                raise ValueError("SOURCE_INVALID: invalid PDB coordinate") from exc
            residue = SourceResidue(expected_sha256, active, models[active], loc.chain, loc.resnum,
                                    loc.insertion, None, None, line[72:76] if len(line) >= 76 else None,
                                    line[17:20])
            atom = PdbAtom("a_" + digest(canonical([expected_sha256, active, line_number])), line_number, active,
                           line[:6].strip(), line[6:11], line[12:16], line[16:17], line[76:78],
                           residue, loc, xyz, line[54:60], line[60:66])
            identity = active, atom.identity_key
            require(identity not in keys, "IDENTITY_AMBIGUOUS", "duplicate complete PDB atom identity")
            keys.add(identity)
            atoms.append(atom)
    require(atoms and (not explicit or active is None), "FRAME_SET_INVALID", "empty/unclosed PDB")
    return PdbSource(data, tuple(atoms), tuple(models))


@measured("identity.source_prepared_PDB")
def source_prepared_correspondence(source, prepared):
    """Exact identifier join, then exact coordinate check; no offset/proximity guesses.

    Added atoms retain null source-atom IDs. Missing source atoms are recorded,
    not declared deliberately filtered. This proves field correspondence only,
    not the historical preparation invocation or chemical correctness.
    """
    original = {a.identity_key: a for a in source.reference_atoms}
    source_residues = {}
    original_groups = {}
    for a in source.reference_atoms:
        if a.record == "ATOM":
            source_residues.setdefault(a.residue_key, a.residue)
            original_groups.setdefault(a.residue_key, []).append(a)
    matches, added, changed, used = [], [], [], set()
    prepared_groups, matched_originals = {}, {}
    for atom in prepared.reference_atoms:
        prepared_groups.setdefault(atom.residue_key, []).append(atom)
        candidate = original.get(atom.identity_key)
        if candidate is None:
            parent = source_residues.get(atom.residue_key)
            added.append({"prepared_atom_uid": atom.uid, "source_atom_uid": None,
                          "source_residue_uid": parent.uid if parent else None,
                          "element": atom.element_raw.strip(), "reason": "no_identical_source_atom_key"})
            continue
        same_xyz = all(Decimal(a) == Decimal(b) for a, b in zip(candidate.xyz_raw, atom.xyz_raw))
        pair = {"source_atom_uid": candidate.uid, "prepared_atom_uid": atom.uid,
                "source_residue_uid": candidate.residue.uid, "prepared_residue_uid": atom.residue.uid,
                "author_chain": candidate.locator.chain, "author_resnum": candidate.locator.resnum,
                "atom_name": candidate.atom_name_raw.strip(), "resname": candidate.residue.source_resname,
                "source_xyz_raw": candidate.xyz_raw, "prepared_xyz_raw": atom.xyz_raw,
                "coordinates_exactly_equal": same_xyz}
        matches.append(pair)
        matched_originals.setdefault(candidate.residue.uid, set()).add(candidate.uid)
        used.add(candidate.uid)
        if not same_xyz:
            changed.append(pair)
    absent = [{"source_atom_uid": a.uid, "source_residue_uid": a.residue.uid, "record": a.record,
               "resname": a.residue.source_resname, "reason": "not_present_under_identical_prepared_key"}
              for a in source.reference_atoms if a.uid not in used]
    bindings, residue_pairs = {}, []
    for key, group in prepared_groups.items():
        source_residue = source_residues.get(key)
        if source_residue is None:
            continue
        original_group = original_groups[key]
        original_uids = {a.uid for a in original_group}
        corresponding_uids = matched_originals.get(source_residue.uid, set())
        # This is an identifier crosswalk, not a claim of unchanged coordinates.
        # Require every original protein atom key. Coordinate changes and all
        # prepared-only atoms (including heavy atoms) remain explicit, unvalidated
        # transformations; none receives a fabricated original atom ID.
        if original_uids <= corresponding_uids:
            bindings[key[0]] = source_residue
            residue_pairs.append({"source_residue_uid": source_residue.uid, "prepared_locator": vars(key[0]),
                                  "matched_original_atom_count": len(original_group),
                                  "prepared_atom_count": len(group),
                                  "evidence": "all_original_atom_identifiers_present_unique_author_residue_key",
                                  "coordinate_transforms_validated": False})
    receipt = {"schema": "dreamm-pdb-correspondence/1", "source_sha256": source.sha256,
               "prepared_sha256": prepared.sha256, "method": "identifier_first_exact_fields_no_proximity_or_offsets",
               "atom_pairs": matches, "prepared_only_atoms": added, "source_only_atoms": absent,
               "coordinate_changes": changed, "residue_pairs": residue_pairs,
               "counts": {"source_atoms": len(source.reference_atoms), "prepared_atoms": len(prepared.reference_atoms),
                          "matched_atoms": len(matches), "prepared_only_atoms": len(added),
                          "source_only_atoms": len(absent), "coordinate_changes": len(changed),
                          "bound_residues": len(bindings)},
               "all_prepared_residues_bound": len(bindings) == len(prepared_groups),
               "complete_atom_lineage": not added and not absent and not changed,
               "preparation_run_verified": False, "scientific_preparation_validated": False,
               "scope": "static_PDB_field_correspondence_not_M1_mmcif_or_runtime_validation"}
    return receipt, bindings
