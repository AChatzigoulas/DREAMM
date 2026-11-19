"""Bounded readers for the versioned P2Rank prediction CSV formats."""
import csv
from dataclasses import dataclass
from decimal import Decimal
import io
import math
import re
import struct
from typing import Optional

from dreamm_m1.identity import digest
from dreamm_m1.pdb_input import read_pdb_source
from .pockets import Pocket

HEADER = ("name", "rank", "score", "sas_points", "surf_atoms", "center_x", "center_y",
          "center_z", "residue_ids", "surf_atom_ids")
HEADER_251 = HEADER[:3] + ("probability",) + HEADER[3:]
PREDICTION_HEADERS = {"2.1": HEADER, "2.5.1": HEADER_251}
MAX_BYTES = 8 * 1024**2


def _integer(value, *, minimum=1):
    if not re.fullmatch(r"0|[1-9][0-9]{0,8}", value) or int(value) < minimum:
        raise ValueError("invalid P2Rank integer")
    return int(value)


def _number(value):
    if not re.fullmatch(r"[+-]?[0-9]{1,20}(?:\.[0-9]{1,20})?(?:[eE][+-]?[0-9]{1,3})?", value):
        raise ValueError("invalid P2Rank decimal")
    number = Decimal(value)
    if not math.isfinite(float(number)):
        raise ValueError("nonfinite P2Rank value")
    return number


def _probability(value):
    number = _number(value)
    if not 0 <= number <= 1:
        raise ValueError("P2Rank probability outside [0, 1]")
    return number


@dataclass(frozen=True)
class Prediction:
    name: str
    rank: int
    score: Decimal
    sas_points: int
    center: tuple
    residue_tokens: tuple
    residue_keys: tuple
    surface_serials: tuple
    probability: Optional[Decimal] = None

    def for_distance(self):
        return Pocket(self.rank, float(self.score), tuple(map(float, self.center)), self.surface_serials)


def prepared_serials(data, expected_sha256):
    source = read_pdb_source(data, expected_sha256=expected_sha256)
    if len(source.model_ids) != 1:
        raise ValueError("P2Rank replay requires exactly one prepared frame")
    serials = {}
    for atom in source.atoms:
        serial = _integer(atom.serial_raw.strip())
        if serial in serials:
            raise ValueError("duplicate PDB atom serial")
        serials[serial] = atom
    return serials


def geometry_by_serial(atoms_by_serial, md_atoms):
    """Require explicit MDAnalysis-global position and exact atom identity/geometry.

    Each input row is (serial, global_index, identity_key, xyz). The runtime
    adapter must capture both identifiers from the *full* Universe before
    filtering it. Capture coordinates from its original float32 array.
    """
    seen_indices = set()
    coordinates = {}
    legacy_compatible = set()
    for serial, index, key, xyz in md_atoms:
        atom = atoms_by_serial.get(serial)
        if (type(serial) is not int or type(index) is not int or index < 0 or serial in coordinates
                or index in seen_indices or atom is None or atom.identity_key != key or len(xyz) != 3):
            raise ValueError("unproven PDB to MDAnalysis atom correspondence")
        # Comparing the same round-to-float32 coordinates preserves the legacy
        # MDAnalysis arithmetic and avoids a tolerance or decimal rewrite.
        for source, live in zip(atom.xyz_raw, xyz):
            if not math.isfinite(live) or struct.pack("=f", float(source)) != struct.pack("=f", live):
                raise ValueError("prepared coordinate differs")
        seen_indices.add(index)
        coordinates[serial] = tuple(xyz)
        if serial == index + 1:
            legacy_compatible.add(serial)
    return coordinates, frozenset(legacy_compatible)


def read_predictions(data, *, coordinates, residue_ids, version="2.1"):
    """Use independently bound BioJava residue tokens and protein atom serials."""
    if version not in PREDICTION_HEADERS:
        raise ValueError("unsupported P2Rank prediction version")
    header = PREDICTION_HEADERS[version]
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES or b"\0" in data:
        raise ValueError("invalid prediction CSV size/content")
    reader = csv.reader(io.StringIO(data.decode("ascii"), newline=""), strict=True)
    if tuple(s.strip() for s in next(reader, ())) != header:
        raise ValueError(f"unexpected P2Rank {version} prediction header")
    seen_names, seen_ranks, result = set(), set(), []
    for fields in reader:
        if len(fields) != len(header) or len(result) >= 10000:
            raise ValueError("unexpected P2Rank row shape")
        row = dict(zip(header, (value.strip() for value in fields)))
        name, rank = row["name"], _integer(row["rank"])
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) or name in seen_names or rank in seen_ranks):
            raise ValueError("ambiguous P2Rank pocket identity")
        seen_names.add(name)
        seen_ranks.add(rank)
        serials = tuple(_integer(value) for value in row["surf_atom_ids"].split())
        tokens = tuple(row["residue_ids"].split())
        if (not serials or _integer(row["surf_atoms"]) != len(serials) or len(set(serials)) != len(serials)
                or not set(serials) <= coordinates.keys()):
            raise ValueError("duplicate, missing or unresolved P2Rank surface serial")
        if not tokens or len(set(tokens)) != len(tokens) or not set(tokens) <= residue_ids.keys():
            raise ValueError("unresolved P2Rank residue identity")
        keys = tuple(residue_ids[token] for token in tokens)
        if len(keys) != len(set(keys)):
            raise ValueError("ambiguous P2Rank residue correspondence")
        result.append(Prediction(name, rank, _number(row["score"]), _integer(row["sas_points"], minimum=0),
                                 tuple(_number(row["center_" + axis]) for axis in "xyz"), tokens, keys,
                                 serials, _probability(row["probability"]) if "probability" in row else None))
    return tuple(result)


def read_residue_scores(data, *, identities, ranks):
    """Check the common 2.1/2.5.1 residue table, including its native probability."""
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES or b"\0" in data:
        raise ValueError("invalid residue CSV size/content")
    reader = csv.reader(io.StringIO(data.decode("ascii"), newline=""), strict=True)
    header = ("chain", "residue_label", "residue_name", "score", "zscore", "probability", "pocket")
    if tuple(s.strip() for s in next(reader, ())) != header:
        raise ValueError("unexpected P2Rank residue header")
    result = {}
    for fields in reader:
        if len(fields) != len(header):
            raise ValueError("unexpected P2Rank residue row shape")
        chain, label, name, score, zscore, prob, rank = (s.strip() for s in fields)
        key = (chain, label)
        rank = _integer(rank, minimum=0)
        probability = _probability(prob)
        if key in result or identities.get(key) != name or rank and rank not in ranks:
            raise ValueError("ambiguous or unexpected P2Rank residue score")
        result[key] = (name, _number(score), _number(zscore), probability, rank)
    if result.keys() != identities.keys():
        raise ValueError("missing P2Rank residue score")
    return result


def compare_reference_csvs(single, batch_reference):
    """Compare raw tables from the same prediction profile and reference frame."""
    if not single or not batch_reference or set(single) != {"predictions", "residues"} or set(single) != set(batch_reference):
        raise ValueError("missing reference-frame output table")
    if not all(single.values()) or not all(batch_reference.values()):
        raise ValueError("empty file cannot establish completed prediction")
    return {key: {"identical": single[key] == batch_reference[key], "single_sha256": digest(single[key]),
                  "batch_sha256": digest(batch_reference[key])} for key in sorted(single)}
