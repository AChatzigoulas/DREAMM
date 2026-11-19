"""M1 data core shared by public evidence replay and the admitted native worker."""
from dataclasses import dataclass
from types import MappingProxyType

from .features import Profile, prepare_features
from .measurement import measured, note_artifacts
from .identity import (CIF_3RUK_SHA256, MODEL_SHA256, O95425_SHA256, PDB_2KS4_REFERENCE_SHA256, PDB_8YOQ_SHA256, PythonRow, descriptor_adapter, join_features,
                       read_descriptors, canonical, digest, require, sha)


@dataclass(frozen=True)
class PreparedM1:
    artifacts: object
    next_stage: str = "approved_isolated_scaler_and_classifier_required"

    def __post_init__(self):
        object.__setattr__(self, "artifacts", MappingProxyType(dict(self.artifacts)))


@measured("M1.reference_data_core")
def prepare_m1(*, source, expected_source_sha256, prepared, bindings, python_rows,
               descriptors, profile_bytes, pdb_id="1RLW", chain_map=None, adapter_stem="reference_Prot.pdb",
               admitted_single_chain=False, admitted_local=False):
    """Prepare identity-checked M1 features and immutable capture payloads.

    The first supported pilot is 1RLW; callers supply already captured preparation
    bindings. This does not parse/acquire mmCIF, prepare chemistry, compute tools,
    attest those supplied bindings, fit a scaler, load a model or publish a job.
    """
    pinned = {"8YOQ": (PDB_8YOQ_SHA256, 367), "3RUK": (CIF_3RUK_SHA256, 464),
              "2KS4": (PDB_2KS4_REFERENCE_SHA256, 176),
              "O95425-2": (O95425_SHA256, 1788)}
    bounded_local = admitted_single_chain is True and pdb_id is None and len({k.chain for k in bindings}) == 1
    from .local_budget import source_bytes, trusted_local
    general = admitted_local is True and trusted_local() and pdb_id is None
    require(general or bounded_local or isinstance(pdb_id, str) and (pdb_id.upper() == "1RLW" or
            pinned.get(pdb_id) == (expected_source_sha256, len(bindings))),
            "SOURCE_INVALID", "unadmitted fixture or identity count")
    sha(expected_source_sha256)
    require(isinstance(source, bytes) and 0 < len(source) <= source_bytes()
            and digest(source) == expected_source_sha256, "SOURCE_INVALID", "immutable source hash/size")
    profile = Profile.owner_selected(profile_bytes)
    require(0 < len(bindings) <= (9999 if general else 1788 if pdb_id == "O95425-2" else 524),
            "RESOURCE_LIMIT", "fixture residue admission bound")
    require(all(r.source_sha256 == expected_source_sha256 for r in bindings.values()),
            "DESCRIPTOR_MAPPING_UNPROVEN", "bindings belong to another source")
    python_rows = tuple(python_rows)
    require(all(isinstance(r, PythonRow) for r in python_rows), "FEATURE_SCHEMA_MISMATCH", "typed Python rows")
    adapter = descriptor_adapter(prepared, bindings, source_mapping_evidence_ref="preparation_map",
                                 chain_map=chain_map, stem=adapter_stem)
    descriptor_capture = None
    if not isinstance(descriptors, bytes):
        from .protdcal import DescriptorExtraction
        require(isinstance(descriptors, DescriptorExtraction) and descriptors.source_pdb_sha256 == digest(adapter.data),
                "DESCRIPTOR_MAPPING_UNPROVEN", "compiled descriptor capture belongs to another prepared adapter")
        descriptor_capture = descriptors
        from .capture import _json
        from .pdb_evidence import parse_descriptor_aa
        receipt = _json(descriptor_capture.receipt_bytes)
        require(parse_descriptor_aa(descriptor_capture.raw_aa)[0] == descriptor_capture.descriptors
                and receipt.get("input_sha256") == digest(adapter.data)
                and receipt.get("raw_AA_sha256") == digest(descriptor_capture.raw_aa)
                and receipt.get("descriptor_sha256") == digest(descriptor_capture.descriptors),
                "ARTIFACT_MISSING_OR_MALFORMED", "inconsistent compiled descriptor capture")
        descriptors = descriptor_capture.descriptors
    joined = join_features(adapter, python_rows, read_descriptors(descriptors))
    matrix = prepare_features(joined, profile)
    artifacts = {"source": source, "prepared_before": prepared, "java_adapter": adapter.data,
                 "adapter_map": canonical(adapter.receipt), "descriptors": descriptors,
                 "python_features": canonical([{"locator": vars(r.locator), "aa": r.aa,
                                                 "values": dict(r.values), "row_id": r.row_id} for r in python_rows]),
                 "join": canonical(joined), "unscaled": canonical(matrix), "compatibility_profile": profile_bytes,
                 "bundle": canonical({"model_sha256": MODEL_SHA256, "reference_sha256": profile.reference_sha256,
                                      "selection_record": "AR-2026-11-05-01",
                                      "historical_training_exporter_required": False})}
    if descriptor_capture is not None:
        artifacts["raw_aa"] = descriptor_capture.raw_aa
        artifacts["descriptor_stage"] = descriptor_capture.receipt_bytes
    note_artifacts(artifacts)
    return PreparedM1(artifacts)
