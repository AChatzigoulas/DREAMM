"""Identifier-first mmCIF/PDB field comparison; never a preparation attestation."""
from decimal import Decimal
import re

from .identity import Locator, canonical, require
from .mmcif_input import CIF_1RLW_SHA256, read_mmcif_source
from .pdb_evidence import inspect_1rlw_pdb_assets
from .pdb_input import PDB_1RLW_SHA256, read_pdb_source
from .identity import digest


def _blank_or_literal(field, *, width):
    if field.missing:
        return " "
    require(len(field.value) == width and field.value not in (".", "?"),
            "IDENTITY_UNREPRESENTABLE", "literal identifier cannot be projected to PDB blank field")
    return field.value


def _cif_key(atom):
    f = atom.field
    chain, num = f("auth_asym_id").value, f("auth_seq_id").value
    require(len(chain) == 1 and re.fullmatch(r"-?[0-9]+", num) and len(num) <= 4,
            "IDENTITY_UNREPRESENTABLE", "author chain/number cannot be represented by this PDB evidence")
    insertion = _blank_or_literal(f("pdbx_PDB_ins_code"), width=1)
    altloc = _blank_or_literal(f("label_alt_id"), width=1)
    # PDB's chemical element spelling is uppercase; the original spellings are
    # retained in receipts. No atom-name or chain case folding is permitted.
    return (atom.record, Locator(chain, num, insertion, ""), f("auth_comp_id").value,
            f("auth_atom_id").value, altloc, f("type_symbol").value.upper())


def compare_mmcif_to_pdb(source, pdb):
    """Unique author identifiers select pairs; coordinates only classify pairs.

    Full label identities cannot be proven equal to PDB (it has no namespace).
    Null insertion/altloc -> blank is an explicit lossy projection; collisions
    fail. No source atom ID is assigned to a newly prepared atom.
    """
    original, pdb_index = {}, {}
    for atom in source.reference_atoms:
        key = _cif_key(atom)
        require(key not in original, "IDENTITY_AMBIGUOUS", "mmCIF identities collide under PDB projection")
        original[key] = atom
    for atom in pdb.reference_atoms:
        # Strip PDB fixed-column padding only (e.g. ' CD'); this is a declared
        # format projection, not residue renaming or coordinate matching.
        key = (atom.record, atom.locator, atom.residue.source_resname.strip(),
               atom.atom_name_raw.strip(), atom.altloc_raw, atom.element_raw.strip())
        require(key not in pdb_index, "IDENTITY_AMBIGUOUS", "PDB key collision")
        pdb_index[key] = atom
    matches, source_only, pdb_only, groups, used = [], [], [], {}, set()
    for key, atom in original.items():
        groups.setdefault(key[1:3], []).append(atom)
        other = pdb_index.get(key)
        if other is None:
            source_only.append({"source_atom_uid": atom.uid, "source_residue_uid": atom.residue.uid,
                                "record": atom.record, "reason": "no_identical_projected_author_key"})
            continue
        used.add(other.uid)
        xyz = tuple(atom.field(n).value for n in ("Cartn_x", "Cartn_y", "Cartn_z"))
        coordinate_equal = all(Decimal(a) == Decimal(b) for a, b in zip(xyz, other.xyz_raw))
        differences = []
        if atom.field("id").value != other.serial_raw.strip():
            differences.append("atom_site_id_vs_PDB_serial")
        if atom.field("type_symbol").value != other.element_raw.strip():
            differences.append("element_spelling")
        if atom.residue.model_id != other.residue.model_id:
            differences.append("original_model_id_vs_PDB_MODEL_id")
        pair = {"source_atom_uid": atom.uid, "pdb_atom_uid": other.uid,
                "source_residue_uid": atom.residue.uid, "pdb_residue_uid": other.residue.uid,
                "projected_author_locator": vars(key[1]), "resname": key[2], "atom_name": key[3],
                "source_atom_site_id": atom.field("id").receipt(), "pdb_serial_raw": other.serial_raw,
                "source_xyz_raw": xyz, "pdb_xyz_raw": other.xyz_raw,
                "source_occupancy": atom.field("occupancy").receipt(), "pdb_occupancy_raw": other.occupancy_raw,
                "source_bfactor": atom.field("B_iso_or_equiv").receipt(), "pdb_bfactor_raw": other.bfactor_raw,
                "coordinates_exactly_equal": coordinate_equal, "identifier_differences": differences,
                "classification": ("coordinate_changed_under_unique_identifier_key" if not coordinate_equal else
                    "coordinate_equivalent_identifiers_differ" if differences else "exact_shared_source_identity_fields"),
                "null_to_blank_projection": {n: atom.field(n).raw for n in ("pdbx_PDB_ins_code", "label_alt_id")
                                              if atom.field(n).missing},
                "full_namespace_identity_proven": False}
        matches.append(pair)
    for atom in pdb.reference_atoms:
        if atom.uid not in used:
            pdb_only.append({"pdb_atom_uid": atom.uid, "source_atom_uid": None,
                             "pdb_residue_uid": atom.residue.uid, "source_residue_uid": None,
                             "pdb_locator": vars(atom.locator), "resname": atom.residue.source_resname.strip(),
                             "atom_name_raw": atom.atom_name_raw, "xyz_raw": atom.xyz_raw,
                             "element": atom.element_raw.strip(), "record": atom.record,
                             "reason": "no_identical_projected_source_key"})
    bindings, residue_pairs = {}, []
    matched_source = {p["source_atom_uid"] for p in matches}
    for (locator, resname), group in groups.items():
        if any(a.record != "ATOM" for a in group):
            continue
        source_residues = {a.residue.uid: a.residue for a in group}
        require(len(source_residues) == 1, "IDENTITY_AMBIGUOUS", "PDB residue projection collision")
        required = {a.uid for a in group}
        related = [p for p in matches if p["source_atom_uid"] in required]
        if required <= matched_source and len({p["pdb_residue_uid"] for p in related}) == 1:
            bindings[locator] = next(iter(source_residues.values()))
            residue_pairs.append({"source_residue_uid": bindings[locator].uid,
                "pdb_locator": vars(locator), "pdb_residue_uid": related[0]["pdb_residue_uid"],
                "matched_original_atom_count": len(required), "evidence": "unique_projected_identifiers_all_original_protein_atoms",
                "coordinate_transforms_validated": False})
    for atom in pdb_only:
        parent = bindings.get(Locator(**atom["pdb_locator"]))
        if parent is not None and parent.source_resname == atom["resname"]:
            atom["source_residue_uid"] = parent.uid
            atom["residue_association_evidence"] = "static_parent_identifiers_only_not_added_atom_lineage"
    pdb_protein_groups = {a.residue_key for a in pdb.reference_atoms if a.record == "ATOM"}
    receipt = {"schema": "dreamm-mmcif-pdb-correspondence/1", "source_sha256": source.sha256,
               "pdb_sha256": pdb.sha256, "reference_model_index": 0,
               "method": "unique_author_identifiers_then_exact_Decimal_coordinates_no_proximity_no_offsets",
               "declared_format_projections": ["PDB_fixed_column_name_padding", "element_symbol_uppercase",
                   "mmCIF_null_insertion_or_altloc_to_PDB_blank_only_if_unique", "reference_model_index_0_not_original_model_id_equality"],
               "atom_pairs": matches, "source_only_atoms": source_only, "pdb_only_atoms": pdb_only,
               "residue_pairs": residue_pairs,
               "counts": {"source_atoms": len(source.reference_atoms), "pdb_atoms": len(pdb.reference_atoms),
                   "matched_atoms": len(matches), "source_only_atoms": len(source_only), "pdb_only_atoms": len(pdb_only),
                   "coordinate_changes": sum(not p["coordinates_exactly_equal"] for p in matches),
                   "coordinate_equal": sum(p["coordinates_exactly_equal"] for p in matches),
                   "bound_protein_residues": len(bindings)},
               "all_pdb_protein_residues_bound": len(bindings) == len(pdb_protein_groups),
               "label_namespace_absent_in_PDB": True, "preparation_run_verified": False,
               "scientific_preparation_validated": False, "complete_atom_lineage": False}
    return receipt, bindings


def inspect_1rlw_mmcif_assets(assets, profile_bytes):
    source = read_mmcif_source(assets["source"], expected_sha256=CIF_1RLW_SHA256)
    pdb = read_pdb_source(assets["source_pdb"], expected_sha256=PDB_1RLW_SHA256)
    prepared = read_pdb_source(assets["prepared_pdb"], expected_sha256=digest(assets["prepared_pdb"]))
    pdb_map, _ = compare_mmcif_to_pdb(source, pdb)
    prepared_map, bindings = compare_mmcif_to_pdb(source, prepared)
    require(prepared_map["all_pdb_protein_residues_bound"], "DESCRIPTOR_MAPPING_UNPROVEN",
            "incomplete mmCIF/prepared identifier correspondence; preparation instrumentation still required")
    # Deliberately separate PDB-derived numerical compatibility checks. They
    # retain their original source UIDs and are not relabelled as mmCIF results.
    pdb_report, pdb_payloads = inspect_1rlw_pdb_assets(assets, profile_bytes)
    receipts = source.receipts()
    report = {"schema": "dreamm-1rlw-mmcif-intake/1", "status": "source_intake_only", "parsed": True,
              "source_bytes_preserved": True, "source": receipts["source"],
              "source_model_count": len(source.model_ids), "reference_model_index": 0,
              "source_record_counts": receipts["record_counts"], "parser_runtime": dict(source.runtime),
              "source_to_PDB": pdb_map["counts"], "source_to_prepared": prepared_map["counts"],
              "static_residue_crosswalk_complete": True, "preparation_run_verified": False,
              "scientific_preparation_validated": False, "complete_atom_lineage": False,
              "separate_PDB_compatibility": {k: pdb_report[k] for k in (
                  "java_adapter_full_bytes_equal", "java_adapter_sha256", "eligible_unscaled_rows",
                  "depth_excluded_rows", "reference_binding")},
              "classification_performed": False, "scaler_fit_executed": False,
              "scientifically_validated_golden": False,
              "remaining_blocking_step": "approved_instrumented_preparation_and_vetted_scientific_runtime"}
    payloads = {"pdb_" + name: data for name, data in pdb_payloads.items()}
    payloads.update({"source.cif": source.original, "source_models.json": canonical(receipts["source_models"]),
        "source_atoms.json": canonical(receipts["source_atoms"]), "source_residues.json": canonical(receipts["source_residues"]),
        "source_to_pdb.json": canonical(pdb_map), "source_to_prepared.json": canonical(prepared_map),
        "parser_runtime.json": canonical(dict(source.runtime)),
        "core_bindings.json": canonical([{"prepared_locator": vars(k), "source_residue": vars(v),
            "source_residue_uid": v.uid, "scope": "static_identifier_crosswalk_not_preparation_attestation"}
            for k, v in bindings.items()]), "intake.json": canonical(report)})
    return report, payloads
