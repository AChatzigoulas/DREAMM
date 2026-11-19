"""Private M1 capture and verification, with no queue/result publication API."""
import csv
import io
import json
import os
from pathlib import Path
import re
import stat

from .features import Profile, REFERENCE_SHA256, number
from .identity import (MODEL_SHA256, M1Error, SourceResidue, PythonRow, Locator,
                       descriptor_adapter, read_descriptors, join_features,
                       canonical, digest, require, sha)

STAGES = ("source", "preparation", "features", "descriptors", "scaling", "classification", "postprocess")
ROLES = frozenset(("input", "source", "source_models", "residues", "preparation_map", "prepared_before",
                   "prepared_after", "charges", "python_features", "dssp", "sasa", "depth", "sequence_profile",
                   "dynamics", "hydrophobicity", "gyration", "neighbors", "java_adapter", "project", "parameter",
                   "raw_aa", "descriptors", "adapter_map", "join", "unscaled", "compatibility_profile", "bundle",
                   "scaling", "class_predictions", "membrane_residues", "postprocessing", "runtime"))
REQUIRED = ROLES
CLASS_HEADER = ("model_input_row", "reference_residue_uid", "class_label", "source_record_ref")
MEMBRANE_HEADER = ("reference_residue_uid", "auth_chain_id", "auth_seq_id", "insertion_code_raw",
                   "aa_one_letter", "broken_chain", "source_record_ref")
LIMIT = 8 * 1024**2


def _not_link(path, directory=False):
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
            "ARTIFACT_MISSING_OR_MALFORMED", "link/reparse point refused")
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
            "ARTIFACT_MISSING_OR_MALFORMED", "artifact type")
    if not directory:
        require(info.st_nlink == 1, "ARTIFACT_MISSING_OR_MALFORMED", "multiply-linked artifact refused")
    return info


def _parents(path):
    require(not str(path).startswith(("\\\\", "//")),
            "ARTIFACT_MISSING_OR_MALFORMED", "network/device capture paths refused")
    for parent in (path, *path.parents):
        _not_link(parent, directory=True)


def _read(path, limit=None):
    if limit is None:
        from .local_budget import artifact_bytes
        limit = artifact_bytes()
    before = _not_link(path)
    require(before.st_size <= limit, "RESOURCE_LIMIT", "artifact size")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                "ARTIFACT_MISSING_OR_MALFORMED", "artifact replaced")
        data = stream.read(before.st_size + 1)
        after = os.fstat(stream.fileno())
    require(len(data) == before.st_size and (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
            "ARTIFACT_MISSING_OR_MALFORMED", "artifact changed during read")
    return data


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "ARTIFACT_MISSING_OR_MALFORMED", "duplicate JSON key")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=unique,
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise M1Error("ARTIFACT_MISSING_OR_MALFORMED", "invalid JSON artifact") from exc


def _table(data, header):
    rows = []
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8"), newline=""), delimiter="\t", strict=True)
        require(tuple(next(reader, ())) == header, "ARTIFACT_MISSING_OR_MALFORMED", "result table header")
        for row in reader:
            require(len(rows) < 20000 and len(row) == len(header) and all(len(x) <= 512 for x in row),
                    "ARTIFACT_MISSING_OR_MALFORMED", "result table bounds/schema")
            rows.append(row)
    except (csv.Error, UnicodeError) as exc:
        raise M1Error("ARTIFACT_MISSING_OR_MALFORMED", "invalid result table") from exc
    return rows


def _assess(meta, blobs):
    """Validate captured data, not scientific truth or supervisor containment."""
    require(set(blobs) == REQUIRED, "ARTIFACT_MISSING_OR_MALFORMED", "required M1 capture families missing")
    require(tuple(s["stage_id"] for s in meta["stages"]) == STAGES,
            "TOOL_EXIT", "all seven stages must be recorded in order")
    require(all(type(s["exit_code"]) is int and s["exit_code"] == 0
                and s["signal"] is None for s in meta["stages"]), "TOOL_EXIT", "unsuccessful stage")
    request, bundle = _json(blobs["input"]), _json(blobs["bundle"])
    require(request["mode"] == "M1" and type(request["reference_frame_index"]) is int
            and request["reference_frame_index"] == 0 and request["source"]["format"] == "mmcif"
            and request["source"]["pdb_id"].upper() == "1RLW"
            and request["source"]["sha256"] == meta["source_sha256"] == digest(blobs["source"])
            and request["run_id"] == meta["run_id"] and request["attempt_id"] == meta["attempt_id"],
            "SOURCE_INVALID", "M1/1RLW request and immutable source binding")
    require(bundle["model_sha256"] == MODEL_SHA256 and bundle["reference_sha256"] == REFERENCE_SHA256,
            "FEATURE_SCHEMA_MISMATCH", "selected scientific bundle")
    profile = Profile.owner_selected(blobs["compatibility_profile"])
    adapter, joined, matrix = (_json(blobs[r]) for r in ("adapter_map", "join", "unscaled"))
    require(adapter["prepared_sha256"] == digest(blobs["prepared_before"])
            and adapter["adapter_sha256"] == digest(blobs["java_adapter"])
            and joined["adapter_receipt"] == adapter,
            "DESCRIPTOR_MAPPING_UNPROVEN", "prepared/adapter/join binding")
    require(adapter["source_mapping_evidence_ref"] == "preparation_map",
            "DESCRIPTOR_MAPPING_UNPROVEN", "preparation evidence role")
    prep = _json(blobs["preparation_map"])
    require(prep["source_sha256"] == meta["source_sha256"]
            and prep["prepared_sha256"] == adapter["prepared_sha256"],
            "DESCRIPTOR_MAPPING_UNPROVEN", "source/preparation receipt binding")
    links = adapter["residues"]
    identities = {l["residue_uid"]: l for l in links}
    require(links and len(identities) == len(links), "IDENTITY_AMBIGUOUS", "capture residue identities")
    require(all(l["source"]["source_sha256"] == meta["source_sha256"]
                and l["source"]["model_index"] == 0 for l in links), "FRAME_SET_INVALID", "reference identity")
    bindings = {Locator(**l["python_locator"]): SourceResidue(**l["source"]) for l in links}
    rebuilt = descriptor_adapter(blobs["prepared_before"], bindings,
                                 source_mapping_evidence_ref="preparation_map", stem=adapter["stem"],
                                 chain_map={Locator(**l["python_locator"]): l["tool_chain"] for l in links})
    require(rebuilt.receipt == adapter and rebuilt.data == blobs["java_adapter"],
            "DESCRIPTOR_MAPPING_UNPROVEN", "adapter/atom receipt cannot be reproduced")
    raw_python = _json(blobs["python_features"])
    python_rows = [PythonRow(Locator(**r["locator"]), r["aa"], r["values"], r["row_id"]) for r in raw_python]
    rebuilt_join = join_features(rebuilt, python_rows, read_descriptors(blobs["descriptors"]))
    require(rebuilt_join == joined, "DESCRIPTOR_MAPPING_UNPROVEN", "raw table/join receipt drift")
    from .features import prepare_features
    require(prepare_features(rebuilt_join, profile) == matrix,
            "FEATURE_SCHEMA_MISMATCH", "encoded matrix does not match identity-bound raw features")
    source_models, residue_manifest = _json(blobs["source_models"]), _json(blobs["residues"])
    require(0 < len(source_models["models"]) <= 20
            and source_models["source_sha256"] == meta["source_sha256"]
            and [m["source_model_index"] for m in source_models["models"]] == list(range(len(source_models["models"]))),
            "FRAME_SET_INVALID", "full source-model manifest")
    require(all(l["source"]["model_id"] == source_models["models"][0]["source_model_id"] for l in links),
            "FRAME_SET_INVALID", "original reference model ID")
    require(all(l["residue_uid"] in residue_manifest for l in links)
            and all(residue_manifest[l["residue_uid"]] == l["source"] for l in links),
            "IDENTITY_AMBIGUOUS", "source residue manifest")
    require(_json(blobs["runtime"])["evidence_class"] == meta["evidence_class"] == "synthetic",
            "ENVIRONMENT_UNVERIFIED", "this capture backend is synthetic-only")
    require(matrix["ordered_feature_names"] == list(profile.names)
            and matrix["model_sha256"] == MODEL_SHA256 and matrix["reference_sha256"] == REFERENCE_SHA256,
            "FEATURE_SCHEMA_MISMATCH", "matrix profile binding")
    unsigned = {k: v for k, v in matrix.items() if k != "matrix_sha256"}
    require(digest(canonical(unsigned)) == matrix["matrix_sha256"], "FEATURE_SCHEMA_MISMATCH", "unscaled matrix digest")
    rows = matrix["rows"]
    require(rows and len({r["residue_uid"] for r in rows}) == len(rows), "IDENTITY_AMBIGUOUS", "matrix identities")
    for n, row in enumerate(rows):
        require(row["model_input_row"] == n and row["residue_uid"] in identities
                and len(row["values"]) == len(profile.names), "FEATURE_SCHEMA_MISMATCH", "matrix row")
        for value in row["values"]:
            number(value)
    scale = _json(blobs["scaling"])
    require(scale["unscaled_matrix_sha256"] == matrix["matrix_sha256"]
            and scale["reference_sha256"] == REFERENCE_SHA256
            and scale["ordered_feature_names"] == list(profile.names)
            and scale["n_samples_seen"] == 3010 and len(scale["rows"]) == len(rows),
            "NUMERIC_VALIDATION_UNPROVEN", "scaler population/schema/row binding")
    for key in ("mean", "scale", "var"):
        require(len(scale[key]) == len(profile.names), "FEATURE_SCHEMA_MISMATCH", "scaler dimensions")
        for value in scale[key]:
            x = number(value)
            require(key == "mean" or (x > 0 if key == "scale" else x >= 0),
                    "NUMERIC_VALIDATION_UNPROVEN", "scaler statistic")
    for a, b in zip(rows, scale["rows"]):
        require(b["residue_uid"] == a["residue_uid"] and b["model_input_row"] == a["model_input_row"]
                and len(b["values"]) == len(profile.names), "IDENTITY_AMBIGUOUS", "scaled row identity drift")
        for value in b["values"]:
            number(value)
    predictions = _table(blobs["class_predictions"], CLASS_HEADER)
    require(len(predictions) == len(rows), "IDENTITY_AMBIGUOUS", "class label coverage")
    for n, (prediction, row) in enumerate(zip(predictions, rows)):
        require(prediction[0] == str(n) and prediction[1] == row["residue_uid"]
                and prediction[2] in ("0", "1") and bool(prediction[3]),
                "IDENTITY_AMBIGUOUS", "raw label mapping/encoding")
    membrane = _table(blobs["membrane_residues"], MEMBRANE_HEADER)
    require(len({r[0] for r in membrane}) == len(membrane), "IDENTITY_AMBIGUOUS", "duplicate final residue")
    from .identity import AA
    for row in membrane:
        require(row[0] in identities and row[6], "IDENTITY_AMBIGUOUS", "foreign final residue")
        identity = identities[row[0]]
        source = identity["source"]
        insertion = json.loads(row[3])  # JSON token preserves null vs '?', '.' and blank.
        require((row[1], row[2], insertion, row[4]) ==
                (source["auth_chain"], source["auth_seq"], source["insertion_raw"], AA[identity["processed_resname"]]),
                "IDENTITY_AMBIGUOUS", "final author identity/AA mismatch")
    post = _json(blobs["postprocessing"])
    require(post["class_predictions_sha256"] == digest(blobs["class_predictions"])
            and post["membrane_residues_sha256"] == digest(blobs["membrane_residues"])
            and post["policy"] == "legacy_unchanged"
            and post["decision_evidence_refs"], "ARTIFACT_MISSING_OR_MALFORMED", "postprocessing evidence")
    for role in post["decision_evidence_refs"]:
        require(role in blobs and role not in ("postprocessing", "membrane_residues"),
                "ARTIFACT_MISSING_OR_MALFORMED", "missing postprocessing evidence")
    return "positive" if membrane else "scientific_empty"


def _result(meta, blobs):
    outcome, errors = None, []
    try:
        outcome = _assess(meta, blobs)
    except (M1Error, KeyError, TypeError, ValueError, IndexError) as exc:
        errors = [{"code": exc.code if isinstance(exc, M1Error) else "ARTIFACT_MISSING_OR_MALFORMED"}]
    models = []
    if "source_models" in blobs:
        try:
            models = _json(blobs["source_models"])["models"]
            require(isinstance(models, list), "FRAME_SET_INVALID", "models list")
        except (M1Error, KeyError, TypeError):
            models = []
    frame = "m_" + digest(canonical({"source_sha256": meta["source_sha256"], "model_index": 0,
                                     "model_id": models[0].get("source_model_id") if models and isinstance(models[0], dict) else None}))
    return {"contract_version": "dreamm-scientific/1.0.0", "run_id": meta["run_id"],
            "attempt_id": meta["attempt_id"], "mode": "M1",
            "completion": "failed_incomplete" if errors else "complete",
            "reference_outcome": outcome, "ensemble_outcome": "not_requested",
            "errors": errors, "provenance_status": "candidate",
            "model_selection": "AR-2026-11-05-01", "historical_exporter_required": False,
            "science_bundle_ref": "bundle", "input_manifest_sha256": digest(blobs["input"]) if "input" in blobs else None,
            "reference_frame_uid": frame, "artifact_refs": sorted(blobs),
            "identity_manifest_ref": "residues", "stage_manifest_ref": "manifest.json",
            "frame_counts": {"source_model_count": len(models), "analysis_expected_count": 1,
                             "analysis_complete_count": 0 if errors else 1,
                             "generated_expected_count": 0, "generated_complete_count": 0,
                             "expected_frame_uids": [frame], "complete_frame_uids": [] if errors else [frame],
                             "prepared_frame_uids": [] if errors else [frame],
                             "aligned_frame_uids": [], "pocket_evaluated_frame_uids": []},
            "evidence_class": meta["evidence_class"],
            "validation_level": "model_independent_capture_checks_not_scientific_parity",
            "warnings": [{"code": "TRAINING_EXPORT_HISTORY_UNAVAILABLE", "blocking": False}],
            "empty_reasons": [{"code": "no_postprocessed_membrane_residues",
                               "evidence_refs": ["membrane_residues", "postprocessing"]}]
                              if outcome == "scientific_empty" else []}


class Capture:
    def __init__(self, path, *, run_id, attempt_id, source_sha256, max_total_bytes=64 * 1024**2):
        self.path = Path(path).absolute()
        _parents(self.path.parent)
        require(all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", x) for x in (run_id, attempt_id)),
                "SOURCE_INVALID", "internal attempt/run IDs")
        sha(source_sha256)
        require(type(max_total_bytes) is int and 0 < max_total_bytes <= 64 * 1024**2, "RESOURCE_LIMIT", "capture budget")
        self.path.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.meta = {"schema": "dreamm-m1-capture/1", "run_id": run_id, "attempt_id": attempt_id,
                     "source_sha256": source_sha256, "evidence_class": "synthetic",
                     "artifacts": [], "stages": []}
        self.budget, self.total, self.closed = max_total_bytes, 0, False

    def _write(self, name, data):
        _parents(self.path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(self.path / name, flags, 0o600), "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

    def add(self, role, data):
        require(not self.closed and role in ROLES, "ARTIFACT_MISSING_OR_MALFORMED", "closed/unknown capture role")
        require(isinstance(data, bytes) and len(data) <= LIMIT and self.total + len(data) <= self.budget,
                "RESOURCE_LIMIT", "capture size budget")
        require(role not in {r["artifact_id"] for r in self.meta["artifacts"]}, "ARTIFACT_MISSING_OR_MALFORMED", "duplicate role")
        self._write(role + ".data", data)
        self.meta["artifacts"].append({"artifact_id": role, "relative_path": role + ".data",
                                       "sha256": digest(data), "bytes": len(data)})
        self.total += len(data)

    def stage(self, stage_id, *, exit_code, signal=None):
        require(not self.closed and len(self.meta["stages"]) < len(STAGES)
                and stage_id == STAGES[len(self.meta["stages"])] and type(exit_code) is int
                and (signal is None or type(signal) is int), "TOOL_EXIT", "stage order/receipt")
        self.meta["stages"].append({"stage_id": stage_id, "exit_code": exit_code, "signal": signal})

    def finish(self):
        require(not self.closed, "ARTIFACT_MISSING_OR_MALFORMED", "capture already closed")
        blobs = {r["artifact_id"]: _read(self.path / r["relative_path"]) for r in self.meta["artifacts"]}
        require(all(digest(blobs[r["artifact_id"]]) == r["sha256"] for r in self.meta["artifacts"]),
                "ARTIFACT_MISSING_OR_MALFORMED", "capture modified before finish")
        manifest = canonical(self.meta)
        result = _result(self.meta, blobs)
        result["artifact_manifest_sha256"] = digest(manifest)
        self._write("manifest.json", manifest)
        # Last-file commit marker only. Never exposes a DB result or releases a slot.
        self._write("result.json", canonical(result))
        self.closed = True
        return result


def _validate_capture(path, *, run_id, attempt_id, source_sha256):
    path = Path(path).absolute()
    _parents(path)
    manifest_raw = _read(path / "manifest.json", 128 * 1024)
    manifest = _json(manifest_raw)
    require((manifest["run_id"], manifest["attempt_id"], manifest["source_sha256"]) ==
            (run_id, attempt_id, sha(source_sha256)), "IDENTITY_AMBIGUOUS", "stale/foreign attempt")
    blobs, total = {}, 0
    require(len(manifest["artifacts"]) <= len(ROLES), "RESOURCE_LIMIT", "artifact count")
    for artifact in manifest["artifacts"]:
        role = artifact["artifact_id"]
        require(role in ROLES and role not in blobs and artifact["relative_path"] == role + ".data",
                "ARTIFACT_MISSING_OR_MALFORMED", "artifact role/path")
        raw = _read(path / artifact["relative_path"])
        total += len(raw)
        require(total <= 64 * 1024**2 and len(raw) == artifact["bytes"] and digest(raw) == artifact["sha256"],
                "ARTIFACT_MISSING_OR_MALFORMED", "artifact integrity")
        blobs[role] = raw
    expected = _result(manifest, blobs)
    expected["artifact_manifest_sha256"] = digest(manifest_raw)
    actual = _json(_read(path / "result.json", 64 * 1024))
    require(actual == expected, "ARTIFACT_MISSING_OR_MALFORMED", "capture result mismatch")
    return actual


def validate_capture(path, *, run_id, attempt_id, source_sha256):
    """Read-only, fail-closed validation with stable errors for malformed data."""
    try:
        return _validate_capture(path, run_id=run_id, attempt_id=attempt_id, source_sha256=source_sha256)
    except M1Error:
        raise
    except (OSError, KeyError, TypeError, ValueError, IndexError, RecursionError) as exc:
        raise M1Error("ARTIFACT_MISSING_OR_MALFORMED", "capture unavailable or malformed") from exc
