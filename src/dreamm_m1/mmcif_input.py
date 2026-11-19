"""Lossless-source, raw atom-site intake using only the admitted Gemmi CIF DOM.

No Structure hierarchy, PDB conversion, conformer choice or scientific tools.
Unsupported identities fail; the original source always remains untouched.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from types import MappingProxyType

from .gemmi_runtime import verified_gemmi
from .identity import M1Error, SourceResidue, canonical, digest, require, sha

CIF_1RLW_SHA256 = "54c4a52e2969fed187d22355ca13fa0af7b716bf5705b0768d90f081563aa2c4"
REQUIRED = ("group_PDB id type_symbol label_atom_id label_alt_id label_comp_id label_asym_id "
            "label_entity_id label_seq_id pdbx_PDB_ins_code Cartn_x Cartn_y Cartn_z occupancy "
            "B_iso_or_equiv auth_seq_id auth_comp_id auth_asym_id auth_atom_id pdbx_PDB_model_num").lower().split()


@dataclass(frozen=True)
class CifValue:
    raw: str
    value: str | None
    missing: str | None

    def receipt(self):
        return vars(self)

    @property
    def key(self):
        # Quoted '?' and '.' are literal identifiers, not nulls.
        return self.missing, self.value


@dataclass(frozen=True)
class CifAtom:
    uid: str
    row_index: int
    model_index: int
    residue: SourceResidue
    fields: object

    def field(self, name):
        return self.fields[name.lower()]

    @property
    def record(self):
        return self.field("group_PDB").value

    def receipt(self):
        return {"atom_uid": self.uid, "atom_site_row_index": self.row_index,
                "model_index": self.model_index, "residue_uid": self.residue.uid,
                "fields": {k: v.receipt() for k, v in self.fields.items()}}


@dataclass(frozen=True)
class CifSource:
    original: bytes
    block_name: str
    tags: tuple
    atoms: tuple
    model_ids: tuple
    runtime: object

    @property
    def sha256(self):
        return digest(self.original)

    @property
    def reference_atoms(self):
        return tuple(a for a in self.atoms if a.model_index == 0)

    def receipts(self):
        counts = Counter(a.model_index for a in self.atoms)
        return {"schema": "dreamm-mmcif-source/1", "source": {
                    "sha256": self.sha256, "bytes": len(self.original), "format": "mmcif",
                    "block_name": self.block_name, "atom_site_tags_in_order": self.tags},
                "source_models": {"source_sha256": self.sha256, "models": [
                    {"source_model_index": i, "source_model_id": model, "atom_count": counts[i],
                     "reference_selected": i == 0} for i, model in enumerate(self.model_ids)]},
                "source_atoms": [a.receipt() for a in self.atoms],
                "source_residues": {a.residue.uid: vars(a.residue) for a in self.atoms},
                "reference_frame_index": 0, "reference_atom_uids": [a.uid for a in self.reference_atoms],
                "record_counts": dict(Counter(a.record for a in self.atoms)),
                "parser_runtime": dict(self.runtime), "scientific_prediction_performed": False}


def _number(token, *, nullable=False, occupancy=False):
    if token.missing:
        require(nullable, "SOURCE_INVALID", "missing required numeric atom-site value")
        return
    require(re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", token.value),
            "SOURCE_INVALID", "unsupported numeric token (no coercion of uncertainty/underscores)")
    try:
        number = Decimal(token.value)
        require(number.is_finite() and (not occupancy or 0 <= number <= 1),
                "SOURCE_INVALID", "invalid/nonfinite atom-site number")
    except InvalidOperation as exc:
        raise M1Error("SOURCE_INVALID", "malformed numeric atom-site value") from exc


def read_mmcif_source(data, *, expected_sha256):
    """Read all rows in order, select reference index 0 without dropping others.

    Gemmi 0.7.5 read_string(check_level=2) checks CIF syntax, duplicate tags,
    missing pair values and empty/ragged loops. This layer validates M1 identity
    representability, not the entire PDBx dictionary or chemical correctness.
    """
    from .local_budget import model_count, source_atom_count, source_bytes
    sha(expected_sha256)
    require(isinstance(data, bytes) and 0 < len(data) <= source_bytes()
            and digest(data) == expected_sha256 and b"\x00" not in data,
            "SOURCE_INVALID", "frozen mmCIF byte/hash bound")
    gemmi, runtime = verified_gemmi()
    try:
        document = gemmi.cif.read_string(data, check_level=2)
        require(len(document) == 1, "SOURCE_INVALID", "exactly one mmCIF entry required; no block selection")
        block = document.sole_block()
        # Reject split atom_site categories or save frames rather than silently
        # reading just the first loop or hiding atoms in another scope.
        loops = []
        for item in block:
            require(item.frame is None, "SOURCE_INVALID", "save frames unsupported for M1 source intake")
            if item.pair and item.pair[0].lower().startswith("_atom_site."):
                raise M1Error("SOURCE_INVALID", "atom_site must be one complete loop")
            if item.loop is not None and any(t.lower().startswith("_atom_site.") for t in item.loop.tags):
                loops.append(item.loop)
        require(len(loops) == 1, "SOURCE_INVALID", "missing/split atom_site loops")
        loop = loops[0]
        tags = tuple(loop.tags)
        require(all(t.lower().startswith("_atom_site.") for t in tags),
                "SOURCE_INVALID", "mixed category in atom_site loop")
        names = tuple(t[len("_atom_site."):].lower() for t in tags)
        require(len(names) == len(set(names)) and set(REQUIRED) <= set(names)
                and len(names) <= 128 and 0 < loop.length() <= source_atom_count(),
                "SOURCE_INVALID", "required atom-site columns/row bounds")
        atoms, models, seen_ids, seen_atoms, residue_identities = [], [], set(), set(), {}
        author_atoms, label_atoms = set(), set()
        model_indices, limit = {}, model_count()
        author_residues, label_residues = {}, {}
        for row_index in range(loop.length()):
            fields = {}
            for col, name in enumerate(names):
                raw = loop[row_index, col]
                require(len(raw) <= 256, "SOURCE_INVALID", "atom-site token bound")
                missing = {"?": "unknown", ".": "inapplicable"}.get(raw)
                value = None if missing else gemmi.cif.as_string(raw)
                require(value is None or (value and not any(ord(c) < 32 for c in value)),
                        "SOURCE_INVALID", "empty/control atom-site token unsupported")
                fields[name] = CifValue(raw, value, missing)
            def f(name):
                return fields[name.lower()]
            for name in ("group_PDB", "id", "type_symbol", "label_atom_id", "label_comp_id",
                         "label_asym_id", "label_entity_id", "auth_seq_id", "auth_comp_id",
                         "auth_asym_id", "auth_atom_id", "pdbx_PDB_model_num"):
                require(f(name).missing is None, "IDENTITY_UNREPRESENTABLE", "missing required atom/residue identity")
            require(f("group_PDB").value in ("ATOM", "HETATM"), "SOURCE_INVALID", "unknown atom record group")
            model = f("pdbx_PDB_model_num").value
            require(re.fullmatch(r"[1-9][0-9]*", model), "FRAME_SET_INVALID", "model number must be a positive integer")
            if model not in model_indices:
                require(len(models) < limit, "FRAME_SET_INVALID", "model-count bound")
                model_indices[model] = len(models)
                models.append(model)
            model_index = model_indices[model]
            # Preserve interleaved models/rows; order is first appearance, not a
            # sorted model number and not Gemmi's normalized Structure hierarchy.
            require((model, f("id").key) not in seen_ids, "IDENTITY_AMBIGUOUS", "duplicate atom_site id within model")
            seen_ids.add((model, f("id").key))
            for name in ("Cartn_x", "Cartn_y", "Cartn_z"):
                _number(f(name))
            _number(f("occupancy"), nullable=True, occupancy=True)
            _number(f("B_iso_or_equiv"), nullable=True)
            residue = SourceResidue(expected_sha256, model_index, model,
                f("auth_asym_id").value, f("auth_seq_id").value, f("pdbx_PDB_ins_code").raw,
                f("label_asym_id").value, f("label_seq_id").raw, None, f("auth_comp_id").value)
            residue_key = tuple(f(n).key for n in ("group_PDB", "label_entity_id", "label_comp_id",
                "auth_asym_id", "auth_seq_id", "pdbx_PDB_ins_code", "label_asym_id", "label_seq_id", "auth_comp_id"))
            prior = residue_identities.setdefault(residue.uid, residue_key)
            require(prior == residue_key, "IDENTITY_AMBIGUOUS", "residue cannot be represented by M1 core")
            # Full namespace crosswalks must be one-to-one where a label seq is
            # present. Nonpolymers legitimately have missing label_seq_id.
            auth_key = model, f("auth_asym_id").key, f("auth_seq_id").key, f("pdbx_PDB_ins_code").key
            require(author_residues.setdefault(auth_key, residue.uid) == residue.uid,
                    "IDENTITY_AMBIGUOUS", "author residue maps to multiple label/component identities")
            if not f("label_seq_id").missing:
                label_key = model, f("label_asym_id").key, f("label_seq_id").key
                require(label_residues.setdefault(label_key, residue.uid) == residue.uid,
                        "IDENTITY_AMBIGUOUS", "label residue maps to multiple author identities")
            identity = (model, residue.uid, f("auth_atom_id").key, f("label_atom_id").key,
                        f("label_alt_id").key, f("type_symbol").key)
            require(identity not in seen_atoms, "IDENTITY_AMBIGUOUS", "duplicate complete atom identity")
            seen_atoms.add(identity)
            author_atom = model, residue.uid, f("auth_atom_id").key, f("label_alt_id").key
            label_atom = model, residue.uid, f("label_atom_id").key, f("label_alt_id").key
            require(author_atom not in author_atoms and label_atom not in label_atoms,
                    "IDENTITY_AMBIGUOUS", "nonbijective author/label atom correspondence")
            author_atoms.add(author_atom)
            label_atoms.add(label_atom)
            atom = CifAtom("a_" + digest(canonical([expected_sha256, row_index, f("id").raw])),
                           row_index, model_index, residue, MappingProxyType(fields))
            atoms.append(atom)
        return CifSource(data, block.name, tags, tuple(atoms), tuple(models), MappingProxyType(runtime))
    except M1Error:
        raise
    except (ValueError, RuntimeError, IndexError, KeyError, TypeError) as exc:
        raise M1Error("SOURCE_INVALID", "Gemmi rejected malformed/unrepresentable CIF") from exc
