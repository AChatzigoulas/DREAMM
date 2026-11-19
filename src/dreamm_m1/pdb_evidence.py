"""Real 1RLW PDB component evidence. No mmCIF parser or scientific execution."""
import csv
import io
import re

from .features import prepare_features, Profile
from .identity import (PythonRow, canonical, descriptor_adapter, digest, join_features,
                       read_descriptors, require)
from .pdb_input import PDB_1RLW_SHA256, read_pdb_source, source_prepared_correspondence
from .reference import verify_reference_assets
from .measurement import measured, note_artifacts
from .local_budget import artifact_bytes


def parse_descriptor_aa(raw):
    require(isinstance(raw, bytes) and len(raw) <= artifact_bytes(),
            "ARTIFACT_MISSING_OR_MALFORMED", "AA output size")
    lines = raw.splitlines(keepends=True)
    require(len(lines) >= 3 and b"PARAMETERS" in lines[0] and b"t_cont:" in lines[1]
            and lines[2].startswith(b"AA\t"), "FEATURE_SCHEMA_MISMATCH", "expected ProtDCal two-line preamble")
    converted = b"".join(lines[2:])
    return converted, read_descriptors(converted)


def descriptor_csv_from_aa(raw):
    return parse_descriptor_aa(raw)[0]


def read_python_features(raw, adapter):
    """Read the observed legacy CSV/index grammar; never eval serialized tuples."""
    require(isinstance(raw, bytes) and len(raw) <= artifact_bytes(),
            "ARTIFACT_MISSING_OR_MALFORMED", "Python feature size")
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    header = next(reader, [])
    require(len(header) > 4 and header[:4] == ["", "", "resnum", "Amino acid"]
            and len(set(header[2:])) == len(header[2:]), "FEATURE_SCHEMA_MISMATCH", "legacy Python feature header")
    locator_index = {}
    for link in adapter.links:
        key = link.locator.chain, link.locator.resnum, link.locator.insertion
        locator_index.setdefault(key, []).append(link.locator)
    result = []
    for line, row in enumerate(reader, 2):
        require(line <= 20001 and len(row) == len(header) and all(len(v) <= 256 for v in row),
                "FEATURE_SCHEMA_MISMATCH", "Python feature bounds")
        index = re.fullmatch(r"\(' ', (-?[0-9]+), '([ A-Za-z0-9])'\)", row[1])
        require(index and index[1] == row[2], "IDENTITY_AMBIGUOUS", "unsupported/conflicting residue tuple")
        candidates = locator_index.get((row[0], row[2], index[2]), [])
        require(len(candidates) == 1, "IDENTITY_AMBIGUOUS", "missing/ambiguous Python chain-residue-insertion")
        result.append(PythonRow(candidates[0], row[3], dict(zip(header[2:], row[2:])), f"legacy-python-line-{line}"))
    return tuple(result)


@measured("M1.static_PDB_component")
def inspect_1rlw_pdb_assets(assets, profile_bytes):
    """Return receipts/payloads for real preserved data, not a classified M1 run."""
    source = read_pdb_source(assets["source_pdb"], expected_sha256=PDB_1RLW_SHA256)
    prepared = read_pdb_source(assets["prepared_pdb"], expected_sha256=digest(assets["prepared_pdb"]))
    correspondence, bindings = source_prepared_correspondence(source, prepared)
    require(correspondence["all_prepared_residues_bound"],
            "DESCRIPTOR_MAPPING_UNPROVEN", "real source/prepared residue correspondence incomplete")
    adapter = descriptor_adapter(assets["prepared_pdb"], bindings,
                                 source_mapping_evidence_ref="preparation_map", stem="1rlw_Prot.pdb")
    require(adapter.data == assets["java_adapter"], "DESCRIPTOR_MAPPING_UNPROVEN", "new/actual legacy Java PDB differ")
    converted, descriptors = parse_descriptor_aa(assets["raw_aa"])
    require(converted == assets["descriptors"], "FEATURE_SCHEMA_MISMATCH", "AA preamble conversion differs from preserved wrapper")
    python = read_python_features(assets["python_features"], adapter)
    joined = join_features(adapter, python, descriptors)
    profile = Profile.owner_selected(profile_bytes)
    matrix = prepare_features(joined, profile)
    reference = verify_reference_assets(assets["reference"], assets["zeroed"], profile_bytes)
    source_receipt = source.receipts()
    prepared_receipt = prepared.receipts()
    report = {"schema": "dreamm-1rlw-pdb-component/1", "scope": "real_frozen_PDB_component_not_M1_end_to_end",
              "source": source_receipt["source"], "reference_frame_index": 0,
              "source_model_count": len(source.model_ids), "source_reference_model_id": source.model_ids[0],
              "source_record_counts": source_receipt["record_counts"],
              "source_prepared_correspondence": correspondence["counts"],
              "complete_atom_lineage": correspondence["complete_atom_lineage"],
              "preparation_run_verified": False, "scientific_preparation_validated": False,
              "java_adapter_full_bytes_equal": True, "java_adapter_sha256": digest(adapter.data),
              "AA_namespace": "1rlw_Prot.pdb_<chain>_<resname>_<dense_0_based_resnum>",
              "AA_two_line_conversion_matches_wrapper": True,
              "python_rows": len(python), "descriptor_rows": len(descriptors[1]),
              "exact_duplicate_rows": len(joined["duplicates"]),
              "joined_residue_rows": len(joined["rows"]), "eligible_unscaled_rows": len(matrix["rows"]),
              "depth_excluded_rows": len(matrix["excluded"]), "reference_binding": reference,
              "m1_mmcif_intake": {"status": "blocked", "code": "PARSER_ENVIRONMENT_NOT_VERIFIED_BY_PDB_COMPONENT", "parser_approved": True},
              "classification_performed": False, "scaler_fit_executed": False, "scientifically_validated_golden": False,
              "remaining_blocking_step": "verified_parser_sandbox_and_instrumented_trusted_scientific_runtime"}
    payloads = {"source.pdb": source.original, "source_models.json": canonical(source_receipt["source_models"]),
                "source_atoms.json": canonical(source_receipt["source_atoms"]),
                "source_residues.json": canonical(source_receipt["source_residues"]),
                "prepared_atoms.json": canonical(prepared_receipt["source_atoms"]),
                "source_to_prepared.json": canonical(correspondence),
                "java_adapter_receipt.json": canonical(adapter.receipt), "unscaled_candidate.json": canonical(matrix),
                "reference_binding.json": canonical(reference), "intake.json": canonical(report)}
    note_artifacts(payloads)
    return report, payloads
