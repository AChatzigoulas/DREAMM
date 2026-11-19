"""Pinned single-1RLW inference and postprocessing inside an admitted science worker."""
import csv
import io
import os
from pathlib import Path
import re
import sys

from .capture import _json, _parents
from .execution_profiles import ExecutionProfile
from .features import Profile
from .identity import (AA, CIF_3RUK_SHA256, MODEL_SHA256, O95425_SHA256, PDB_2KS4_REFERENCE_SHA256,
                       PDB_8YOQ_SHA256, Locator, canonical, digest, require,
                       residue_label, residue_label_parts)
from .measurement import StageRecorder, measured
from .mmcif_input import CIF_1RLW_SHA256
from .reference import verify_reference_assets


_POLAR_GROUP = frozenset("ARNDCEQGHSK")  # all-res_testing.py LF125–128
_MEMBRANE_HEADER = ("chain", "resnum", "Amino acid", "broken_chain")


def residue_rows(artifacts):
    links = _json(artifacts["adapter_map"])["residues"]
    by_uid = {row["residue_uid"]: row for row in links}
    chains = {row["python_locator"]["chain"] for row in by_uid.values()}
    require(len(by_uid) == len(links) and chains and all(re.fullmatch(r"[A-Za-z0-9]", chain) for chain in chains),
            "FEATURE_MAPPING_UNPROVEN", "ambiguous chain in classifier export")
    rows = {uid: (row["python_locator"]["chain"], Locator(**row["python_locator"]).residue_label, AA[row["processed_resname"]])
            for uid, row in by_uid.items()}
    require(len({(chain, num) for chain, num, _ in rows.values()}) == len(rows),
            "FEATURE_MAPPING_UNPROVEN", "duplicate classifier residue identity")
    return rows


def membrane_csv(residues, retained):
    """Apply legacy terminal/gap flags to already selected membrane UIDs."""
    positions = {(chain, residue_label_parts(num)[0]) for chain, num, _ in residues.values()}
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(_MEMBRANE_HEADER)
    for chain, label, aa in sorted((residues[uid] for uid in retained),
                                   key=lambda row: (row[0], *residue_label_parts(row[1]), row[2])):
        num, _ = residue_label_parts(label)
        broken = int(any((chain, num + delta) not in positions for delta in (-3, -2, -1, 1, 2, 3)))
        writer.writerow((chain, label, aa, broken))
    return out.getvalue().encode("ascii")


def verify_membrane_reference(raw, residues):
    """Bind the preserved calls and independently verify their loop flags."""
    index = {row: uid for uid, row in residues.items()}
    reader = csv.reader(io.StringIO(raw.decode("ascii")), strict=True)
    require(tuple(next(reader, ())) == _MEMBRANE_HEADER, "FEATURE_SCHEMA_MISMATCH", "historical membrane CSV")
    calls = {}
    for chain, num, aa, flag in reader:
        uid = index.get((chain, residue_label(*residue_label_parts(num)), aa))
        require(uid is not None and uid not in calls and flag in ("0", "1"),
                "FEATURE_MAPPING_UNPROVEN", "historical call is ambiguous or foreign")
        calls[uid] = int(flag)
    return calls, membrane_csv(residues, calls)


def _test_frame(artifacts, profile, pd):
    """Use legacy pandas CSV float conversion on original tokens, keyed by UID."""
    joined = _json(artifacts["join"])["rows"]
    unscaled = _json(artifacts["unscaled"])
    rows = io.StringIO(newline="")
    writer = csv.writer(rows, lineterminator="\n")
    writer.writerow(profile.names)
    for row in joined:
        values = dict(row["values"])
        values.update({aa: int(row["aa"] == aa) for aa in AA.values()})
        values.update({ss + "ss": int(values["Secondary structure"] == ss) for ss in "BCEGHIST"})
        writer.writerow(0 if name in profile.zeroed else values[name] for name in profile.names)
    rows.seek(0)
    test = pd.read_csv(rows, thousands=",", low_memory=False)
    admitted = [i for i, keep in enumerate(test["res_depth"] < 2.5) if keep]
    uids = [row["residue_uid"] for row in unscaled["rows"]]
    require([joined[i]["residue_uid"] for i in admitted] == uids,
            "NUMERIC_VALIDATION_UNPROVEN", "legacy pandas and identity core disagree on depth selection")
    return uids, test.iloc[admitted].fillna(0)


@measured("inference.scaling")
def _scale(artifacts, reference, zeroed, profile_bytes):
    import numpy as np
    import pandas as pd
    from sklearn.preprocessing import StandardScaler
    verify_reference_assets(reference, zeroed, profile_bytes)
    profile = Profile.owner_selected(profile_bytes)
    ref = pd.read_csv(io.BytesIO(reference), thousands=",", low_memory=False).drop(columns=["Unnamed: 0"])
    zero_names = pd.read_csv(io.BytesIO(zeroed), low_memory=False)["0"].tolist()
    ref = pd.concat([ref.drop(columns=zero_names), pd.DataFrame(columns=zero_names)], axis=1).fillna(0)
    ref = ref.reindex(sorted(ref.columns), axis=1).drop(columns=["class"])
    uids, test = _test_frame(artifacts, profile, pd)
    scaler = StandardScaler()
    scaler.fit_transform(ref)  # preserve the legacy reference fit path
    scaled = scaler.transform(test)
    require(scaled.shape == (len(uids), 727) and np.isfinite(scaled).all(),
            "NUMERIC_VALIDATION_UNPROVEN", "invalid scaled row; no positional removal")
    receipt = {"schema": "dreamm-m1-legacy-scaling/1", "reference_sha256": profile.reference_sha256,
               "feature_names": list(profile.names), "residue_uids": uids, "n_reference_rows": len(ref),
               "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(), "var": scaler.var_.tolist(),
               "unscaled": test.to_numpy(dtype=float).tolist(), "scaled": scaled.tolist()}
    return uids, pd.DataFrame(scaled, columns=profile.names), receipt


@measured("inference.classifier")
def _classify(model_bytes, uids, matrix):
    from joblib import load
    model = load(io.BytesIO(model_bytes))
    values = model.predict(matrix)
    require(len(values) == len(uids) and all(value in (0, 1) for value in values),
            "NUMERIC_VALIDATION_UNPROVEN", "classifier output shape/labels")
    return {uid: int(label) for uid, label in zip(uids, values)}


@measured("inference.postprocess")
def _postprocess(artifacts, residues, labels, work):
    nonpolar = [uid for uid, label in labels.items() if label == 1 and residue_label_parts(residues[uid][1])[0] >= 0
                and residues[uid][2] not in _POLAR_GROUP]
    polar = [uid for uid, label in labels.items() if label == 1 and residue_label_parts(residues[uid][1])[0] >= 0
             and residues[uid][2] in _POLAR_GROUP]
    retained = list(nonpolar)
    if nonpolar and polar:
        from MDAnalysis import Universe
        from MDAnalysis.lib.distances import distance_array
        import numpy as np
        # Preserve the legacy segment blanking for geometry on a separate copy.
        path = work / "postprocess.pdb"
        expected_chains = {chain for chain, _, _ in residues.values()}
        with path.open("xb") as output:
            for line in artifacts["prepared_before"].splitlines():
                if len(line) >= 72:
                    # In a complex, explicit chain segments preserve distinct
                    # same-number residues in MDAnalysis. Single-chain bytes
                    # retain the established blank-segment geometry path.
                    segment = line[21:22].ljust(4) if len(expected_chains) > 1 else b"    "
                    line = line[:72] + segment + line[76:]
                output.write(line + b"\n")
        universe = Universe(str(path))
        centers = {}
        for residue in universe.select_atoms("protein").residues:
            chain_ids = set(residue.atoms.chainIDs)
            require(len(chain_ids) == 1 and chain_ids <= expected_chains,
                    "FEATURE_MAPPING_UNPROVEN", "postprocessing MDAnalysis residue map")
            key = next(iter(chain_ids)), residue_label(residue.resid, residue.icode or " ")
            require(key not in centers, "FEATURE_MAPPING_UNPROVEN", "duplicate geometry residue")
            centers[key] = (AA[residue.resname], residue.atoms.center_of_mass())
        require(set(centers) == {(chain, num) for chain, num, _ in residues.values()}
                and all(centers[chain, num][0] == aa for chain, num, aa in residues.values()),
                "FEATURE_MAPPING_UNPROVEN", "postprocessing geometry coverage")
        xyz = lambda group: np.array([centers[residues[uid][:2]][1] for uid in group])
        distances = distance_array(xyz(polar), xyz(nonpolar))
        require(np.isfinite(distances).all(), "NUMERIC_VALIDATION_UNPROVEN", "postprocessing COM distance")
        retained.extend(uid for uid, distances_from_polar in zip(polar, distances) if (distances_from_polar < 14).any())
    return membrane_csv(residues, retained)


def require_classifier_admission(profile):
    require(isinstance(profile, ExecutionProfile) and profile.data["scope"] == "classifier_compatibility"
            and profile.data["parameters"].get("model_deserialization_authorized") is True,
            "SCIENTIFIC_EXECUTION_UNAPPROVED", "specific isolated classifier deserialization profile required")
    profile.require_scientific_admission()


def predict_1rlw(prepared, *, model, reference, zeroed, work, profile):
    return _predict(prepared, model=model, reference=reference, zeroed=zeroed, work=work, profile=profile,
                    source_sha256=CIF_1RLW_SHA256)


def predict_8yoq(prepared, *, model, reference, zeroed, work, profile):
    require_classifier_admission(profile)
    require(profile.data["inputs"].get("fixture", {}).get("sha256") == PDB_8YOQ_SHA256,
            "SOURCE_INVALID", "classifier profile is not bound to frozen 8YOQ")
    return _predict(prepared, model=model, reference=reference, zeroed=zeroed, work=work, profile=profile,
                    source_sha256=PDB_8YOQ_SHA256)


def predict_scaling_fixture(prepared, *, fixture, model, reference, zeroed, work, profile):
    require_classifier_admission(profile)
    pins = {"3RUK": CIF_3RUK_SHA256, "O95425-2": O95425_SHA256, "2KS4": PDB_2KS4_REFERENCE_SHA256}
    require(fixture in pins and profile.data["inputs"].get("fixture", {}).get("sha256") == pins[fixture],
            "SOURCE_INVALID", "classifier profile is not bound to the frozen scaling fixture")
    return _predict(prepared, model=model, reference=reference, zeroed=zeroed, work=work, profile=profile,
                    source_sha256=pins[fixture])


def _predict(prepared, *, model, reference, zeroed, work, profile, source_sha256):
    require_classifier_admission(profile)
    require(os.name == "posix" and os.geteuid() != 0 and sys.version_info[:3] == (3, 7, 16),
            "SCIENTIFIC_RUNTIME_MISMATCH", "non-root isolated CPython 3.7.16 classifier worker required")
    artifacts = prepared.artifacts
    require(digest(artifacts["source"]) == source_sha256 and isinstance(model, bytes) and len(model) == 544460
            and digest(model) == MODEL_SHA256, "SOURCE_INVALID", "frozen fixture and owner-selected model bytes required")
    from importlib_metadata import version
    versions = {"scikit-learn": "0.22.1", "pandas": "1.1.3", "joblib": "1.1.0", "mlxtend": "0.18.0",
                "lightgbm": "2.3.0", "MDAnalysis": "2.1.0", "numpy": "1.21.6", "scipy": "1.7.3"}
    require(all(version(name) == expected for name, expected in versions.items()),
            "SCIENTIFIC_RUNTIME_MISMATCH", "legacy classifier library versions")
    work = Path(work).absolute()
    _parents(work.parent)
    work.mkdir(mode=0o700, exist_ok=False)
    recorder = StageRecorder(evidence_class="scientific_unvalidated")
    try:
        with recorder.activate():
            residues = residue_rows(artifacts)
            uids, matrix, scaling = _scale(artifacts, reference, zeroed, artifacts["compatibility_profile"])
            require(set(uids) <= residues.keys() and len(set(uids)) == len(uids),
                    "FEATURE_MAPPING_UNPROVEN", "classifier UID map")
            labels = _classify(model, uids, matrix)
            calls = _postprocess(artifacts, residues, labels, work)
            return {"scaling.json": canonical(scaling), "labels.json": canonical(labels), "membrane.csv": calls}
    finally:
        (work / "timings.json").write_bytes(canonical(recorder.receipt()))
