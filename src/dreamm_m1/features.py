"""Model-independent compatibility matrix preparation; never fit a scaler/model.

Real scaling must use the pinned sklearn implementation in approved isolation.
No reimplementation of floating-point scaler statistics is introduced here.
"""
from dataclasses import dataclass
import json
import math

from .identity import AA, MODEL_SHA256, M1Error, canonical, digest, require, sha

REFERENCE_SHA256 = "c9f60ac6cdad86551b74152d711ac141f93f61f04fe566076990d9a59620deed"
NAMES_SHA256 = "f5c8b153b42609c6620cfbdd60b27c83dae263cdf4f824629300481f0f13e138"


def number(value):
    require(not isinstance(value, bool) and isinstance(value, (str, int, float)),
            "NUMERIC_VALIDATION_UNPROVEN", "numeric feature type")
    try:
        result = float(value.replace(",", "") if isinstance(value, str) else value)
    except (ValueError, OverflowError) as exc:
        raise M1Error("NUMERIC_VALIDATION_UNPROVEN", "invalid numeric feature") from exc
    require(math.isfinite(result), "NUMERIC_VALIDATION_UNPROVEN", "nonfinite feature; no silent row removal")
    return result


@dataclass(frozen=True)
class Profile:
    names: tuple
    zeroed: frozenset
    model_sha256: str = MODEL_SHA256
    reference_sha256: str = REFERENCE_SHA256

    def __post_init__(self):
        object.__setattr__(self, "names", tuple(self.names))
        object.__setattr__(self, "zeroed", frozenset(self.zeroed))
        require(0 < len(self.names) <= 4096 and len(set(self.names)) == len(self.names)
                and list(self.names) == sorted(self.names)
                and all(isinstance(x, str) and 0 < len(x) <= 256 for x in self.names),
                "FEATURE_SCHEMA_MISMATCH", "unique sorted names")
        require(self.zeroed <= set(self.names) and "class" not in self.names and "resnum" not in self.names,
                "FEATURE_SCHEMA_MISMATCH", "feature/zero-fill set")
        sha(self.model_sha256)
        sha(self.reference_sha256)

    @property
    def names_sha256(self):
        return digest(("\n".join(self.names) + "\n").encode("utf-8"))

    @classmethod
    def owner_selected(cls, data):
        require(isinstance(data, bytes) and len(data) <= 128 * 1024, "FEATURE_SCHEMA_MISMATCH", "profile bounds")
        try:
            raw = json.loads(data)
            profile = cls(raw["ordered_feature_names"], raw["zero_filled_columns"],
                          raw["model_sha256"], raw["reference_sha256"])
        except (ValueError, KeyError, TypeError) as exc:
            raise M1Error("FEATURE_SCHEMA_MISMATCH", "malformed compatibility profile") from exc
        require(profile.model_sha256 == MODEL_SHA256 and profile.reference_sha256 == REFERENCE_SHA256
                and profile.names_sha256 == NAMES_SHA256 and len(profile.names) == 727
                and len(profile.zeroed) == 167,
                "FEATURE_SCHEMA_MISMATCH", "owner-selected model/reference/schema binding")
        expected_zeroed = raw.get("zero_filled_names_sha256")
        require(digest(("\n".join(sorted(profile.zeroed)) + "\n").encode()) == expected_zeroed,
                "FEATURE_SCHEMA_MISMATCH", "zero-fill list fingerprint")
        # Pin to the reviewed public list, not a caller-recomputed checksum.
        require(expected_zeroed == ZERO_NAMES_SHA256, "FEATURE_SCHEMA_MISMATCH", "different zero-fill policy")
        return profile


# Filled from the preserved public list's names, not by loading a scientific asset.
ZERO_NAMES_SHA256 = "d94214a034ae7c9a7caf3b0e1035b43f03d2ef92f8acb591c11d52037030d306"


def _vector(profile, values):
    missing = set(profile.names) - profile.zeroed - set(values)
    require(not missing, "FEATURE_SCHEMA_MISMATCH", "missing computed features: " + ",".join(sorted(missing)[:5]))
    return [0.0 if name in profile.zeroed else number(values[name]) for name in profile.names]


def prepare_features(joined, profile):
    """Retain strict depth/one-hot/zero-fill/name order; bind every row to UID.

    Inputs are explicitly joined raw Python/Java features. Unsupported missing
    numeric values fail for review; they are never padded, imputed or dropped.
    This is not a claim of pandas parser/numerical parity on real artifacts.
    """
    rows = joined["rows"]
    require(0 < len(rows) <= 20000, "FEATURE_SCHEMA_MISMATCH", "joined rows")
    seen, admitted, excluded = set(), [], []
    for row in rows:
        uid = row["residue_uid"]
        require(uid not in seen, "IDENTITY_AMBIGUOUS", "duplicate feature UID")
        seen.add(uid)
        values = dict(row["values"])
        require("res_depth" in values, "FEATURE_SCHEMA_MISMATCH", "depth required")
        if number(values["res_depth"]) >= 2.5:
            excluded.append({"residue_uid": uid, "reason": "legacy_depth_not_strictly_below_2.5"})
            continue
        require(row["aa"] in AA.values() and "Secondary structure" in values,
                "FEATURE_SCHEMA_MISMATCH", "AA/secondary-structure evidence required")
        encoded = {a: int(row["aa"] == a) for a in AA.values()}
        encoded.update({s + "ss": int(values["Secondary structure"] == s) for s in "BCEGHIST"})
        require(not set(encoded).intersection(values), "FEATURE_SCHEMA_MISMATCH", "raw/encoded feature collision")
        values.update(encoded)
        admitted.append({"model_input_row": len(admitted), "residue_uid": uid,
                         "python_row_id": row["python_row_id"], "descriptor_line": row["descriptor_line"],
                         "values": _vector(profile, values)})
    require(admitted, "NUMERIC_VALIDATION_UNPROVEN", "no eligible rows; not automatically a scientific negative")
    result = {"schema": "dreamm-m1-unscaled/1", "ordered_feature_names": list(profile.names),
              "ordered_names_sha256": profile.names_sha256,
              "model_sha256": profile.model_sha256, "reference_sha256": profile.reference_sha256,
              "zero_filled_columns": sorted(profile.zeroed), "rows": admitted, "excluded": excluded,
              "scaler": {"implementation": "pinned sklearn.preprocessing.StandardScaler",
                         "fit_population": "transformed 3010-row reference only",
                         "fit_executed": False, "transform_executed": False},
              "zero_fill_policy": "replace designated 167 columns; never infer missing computed columns",
              "numeric_policy": "finite-only fail-closed; no post-scaling positional row discard"}
    result["matrix_sha256"] = digest(canonical(result))
    return result


def prepare_reference(rows, profile):
    """Pure matrix construction; class/index excluded, designated columns zeroed.

    Does not fit StandardScaler, calculate statistics or claim a fitted model's
    training provenance. Caller must first verify the reference file hash.
    """
    rows = tuple(rows)
    require(0 < len(rows) <= 20000, "FEATURE_SCHEMA_MISMATCH", "reference row bounds")
    result = []
    for row in rows:
        require("class" in row and number(row["class"]) in (0.0, 1.0), "FEATURE_SCHEMA_MISMATCH", "reference class")
        result.append(_vector(profile, row))
    return {"ordered_feature_names": list(profile.names), "rows": result,
            "reference_sha256": profile.reference_sha256, "scaler_fit_executed": False}
