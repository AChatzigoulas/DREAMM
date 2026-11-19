"""M1/1RLW preparation input, fixed requests and instrumented-output validation.

No scientific package import or executable launch. Native execution is disabled
until a distinct preparation runtime is reviewed/approved. Synthetic receipts
never attest real execution or scientific equivalence. Uses existing M1 types,
identity comparators, private file guards and capture artifact conventions.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
from types import MappingProxyType

from .capture import _parents, _read, _json
from .identity import AA, O95425_SHA256, Locator, SourceResidue, M1Error, canonical, digest, require
from .mmcif_input import CIF_1RLW_SHA256, CifSource, CifAtom, CifValue
from .mmcif_evidence import _cif_key, compare_mmcif_to_pdb
from .pdb_input import read_pdb_source
from .measurement import measured, note_artifacts

EXTRACTOR_SHA256 = "bd485b7c58decb69ee1ad09edeeb2e83482b2e7b6be225fa8e178800837e8e8e"
LEGACY_PREPARATION_SHA256 = "568ac66a72f2bf4418a37f0423468066ac240b1ee42df099a9e13d5a828028b0"
PDB2PQR_VERSION = "3.8.0"
PDB2PQR_OPTIONS = ("--with-ph=7.0", "--ff=PARSE", "--ffout=AMBER", "--whitespace",
                  "--titration-state-method", "propka")
STEPS = ("reference_bridge", "filter_mutate", "pre_geometry", "charge_pass", "structure_pass", "post_names")
POST_NAMES = dict(zip("AR0 ASH CYM CYX GLH HIE HSE HID HSD HIP HSP LYN TYM".split(),
                     "ARG ASP CYS CYS GLU HIS HIS HIS HIS HIS HIS LYS TYR".split()))
FILE_LIMIT, LOG_LIMIT, TOTAL_LIMIT = 8 * 1024**2, 64 * 1024, 48 * 1024**2


def _number(text):
    require(isinstance(text, str) and re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text),
            "PREPARATION_OUTPUT_INVALID", "numeric grammar")
    try:
        value = Decimal(text)
        require(value.is_finite(), "PREPARATION_OUTPUT_INVALID", "nonfinite number")
        return value
    except InvalidOperation as exc:
        raise M1Error("PREPARATION_OUTPUT_INVALID", "malformed number") from exc


def recipe():
    return {"schema": "dreamm-m1-preparation-recipe/1", "pdb_id": "1RLW", "reference_model_index": 0,
        "extractor_sha256": EXTRACTOR_SHA256, "legacy_moleculekit_preparation_sha256": LEGACY_PREPARATION_SHA256,
        "steps": list(STEPS), "filter": "protein and not resname UNK", "chain_selection": None,
        "pre_mutation": "Molecule.mutateResidue_only_if_nonstandard_after_filter",
        "initial_pilot": "standard_residue_1RLW_only_nonstandard_input_fails",
        "pre_CA": "before_both_preparation_passes_no_centering_no_renumbering",
        "explicit_pdb2pqr_options": list(PDB2PQR_OPTIONS),
        "terminal_neutralization_flags": [], "terminal_flag_explanation": "owner_intentionally_removed_after_errors",
        "proteinPrepare": {"pH": "7.0", "returnDetails": False, "input": "original_filtered_molecule_not_charge_PQR",
                           "internal_hook": "capture_then_decimal_serial_patch_before_original__pdb2pqr"},
        "post_names": POST_NAMES, "merge_or_skip_passes": False,
        "evidence": {"bridge": "extract_features.py:104-145", "filter_pre_geometry": "extract_features.py:319-363",
                     "charge_pass": "extract_features.py:370-391", "structure_pass": "extract_features.py:398-415",
                     "internal_patch": "extract_features.py:147-220,251-262"}}


def fixed_pdb2pqr_request(input_pdb):
    """A request object, never shell text, paths or extra options supplied by a user.

    The input must already be the filtered/substituted pre-prepare snapshot.
    It is NOT the final structure or the charge PQR fed to a second pass.
    """
    before = read_pdb_source(input_pdb, expected_sha256=digest(input_pdb))
    require(len(before.model_ids) == 1 and all(a.record == "ATOM" and a.residue.source_resname in AA
            and a.altloc_raw == " " for a in before.atoms), "PREPARATION_INPUT_INVALID", "filtered standard reference required")
    require(decimal_serial_adapter(input_pdb)[0] == input_pdb, "PREPARATION_INPUT_INVALID", "charge input must already use reviewed decimal serial adapter")
    return {"schema": "dreamm-preparation-invocation/1", "stage": "charge_pass",
        "argv": ["/opt/dreamm-preparation/bin/pdb2pqr30", *PDB2PQR_OPTIONS,
                 "/work/preparation/charge-input.pdb", "/work/preparation/charges.pqr"],
        "cwd": "/work/preparation", "input_sha256": digest(input_pdb), "expected_version": PDB2PQR_VERSION,
        "shell": False, "stdin": "closed", "network": "disabled", "timeout_seconds": 120,
        "log_limit_bytes_each": LOG_LIMIT, "output_limit_bytes_each": FILE_LIMIT,
        "required_output": "charges.pqr", "publication": "private_pending_whole_namespace_exit",
        "execution_authorized": False, "required_profile": "separately_approved_1RLW_scientific_preparation"}


def execute_preparation(*args, **kwargs):
    """Fail before any process/package action. Parser admission cannot grant this.

    A reviewed namespace/cgroup launcher must consume the fixed request only
    after explicit scientific approval; no process-group-only fallback, generic
    executable/callback or mock approval token is accepted by this module.
    """
    raise M1Error("SCIENTIFIC_EXECUTION_UNAPPROVED", "no approved preparation runtime/launcher registered; Gemmi approval is parser-only")


def decimal_serial_adapter(pdb):
    """Independent narrow implementation of the reviewed legacy serial policy.

    Does not modify atoms, identities or coordinates. TERs are rebuilt from the
    preceding atom; CONECT is removed. Evidence records each changed line.
    """
    from .local_budget import artifact_bytes
    require(isinstance(pdb, bytes) and 0 < len(pdb) <= artifact_bytes(), "PREPARATION_INPUT_INVALID", "PDB bound")
    try:
        lines = pdb.decode("ascii").splitlines(keepends=True)
    except UnicodeError as exc:
        raise M1Error("PREPARATION_INPUT_INVALID", "ASCII PDB required") from exc
    serial, last, output, ledger = 1, None, [], []
    for number, line in enumerate(lines, 1):
        if line.startswith(("ATOM  ", "HETATM")):
            require(len(line.rstrip("\r\n")) >= 54, "PREPARATION_INPUT_INVALID", "short PDB record")
            after = line[:6] + f"{serial:5d}" + line[11:]
            last = line[17:20], line[21:22], line[22:26], line[26:27]
            serial = serial % 99999 + 1
        elif line.startswith("TER"):
            require(last is not None, "PREPARATION_INPUT_INVALID", "TER before first atom")
            name, chain, residue, insertion = last
            after = f"TER   {serial:5d}      {name} {chain}{residue}{insertion}\n"
            serial = serial % 99999 + 1
        elif line.startswith("CONECT"):
            ledger.append({"line": number, "operation": "drop_CONECT", "before": line})
            continue
        else:
            after = line
        output.append(after)
        if after != line:
            ledger.append({"line": number, "operation": "serial_or_TER_only", "before": line, "after": after})
    result = "".join(output).encode("ascii")
    read_pdb_source(result, expected_sha256=digest(result))
    return result, {"input_sha256": digest(pdb), "output_sha256": digest(result), "changes": ledger,
                    "scientific_coordinates_changed": False}


def reference_bridge(source):
    """Write only the admitted 1RLW reference's raw fields, with inverse atom IDs.

    A separate PDB projection, not canonical source replacement or a general
    mmCIF parser. Unsupported precision, alternate locations, identifiers and
    missing occupancy/B-factors fail rather than being silently normalized.
    """
    rows, mappings = [], []
    require(0 < len(source.reference_atoms) < 99999, "PREPARATION_INPUT_INVALID", "pilot atom bound")
    for serial, atom in enumerate(source.reference_atoms, 1):
        record, loc, resname, name, altloc, element = _cif_key(atom)
        require(altloc == " " and len(resname) <= 3 and 0 < len(name) <= 4 and len(element) <= 2,
                "PREPARATION_INPUT_INVALID", "pilot does not choose altlocs or truncate names")
        xyz = [atom.field(k).value for k in ("Cartn_x", "Cartn_y", "Cartn_z")]
        occupancy, bfactor = atom.field("occupancy"), atom.field("B_iso_or_equiv")
        require(not occupancy.missing and not bfactor.missing, "PREPARATION_INPUT_INVALID", "missing numeric PDB field")
        fields = [f"{_number(x):8.3f}" for x in xyz] + [f"{_number(occupancy.value):6.2f}", f"{_number(bfactor.value):6.2f}"]
        for raw, formatted in zip([*xyz, occupancy.value, bfactor.value], fields):
            require(_number(raw) == _number(formatted.strip()), "PREPARATION_INPUT_INVALID", "PDB precision loss")
        require(all(len(x) == (8 if i < 3 else 6) for i, x in enumerate(fields)), "PREPARATION_INPUT_INVALID", "PDB field overflow")
        atom_name = ((" " + name) if len(name) < 4 and name[0].isalpha() and len(element) < 2 else name).ljust(4)
        row = f"{record:<6}{serial:5d} {atom_name}{altloc}{resname:>3} {loc.chain}{int(loc.resnum):4d}{loc.insertion}   "
        row += "".join(fields) + "          " + f"{element:>2}  \n"
        rows.append(row)
        mappings.append({"PDB_line": serial, "source_atom_uid": atom.uid, "source_residue_uid": atom.residue.uid,
                         "source_atom_site_row": atom.row_index, "PDB_locator": vars(loc)})
    data = ("".join(rows) + "END\n").encode("ascii")
    pdb = read_pdb_source(data, expected_sha256=digest(data))
    comparison, _ = compare_mmcif_to_pdb(source, pdb)
    require(comparison["counts"]["coordinate_changes"] == 0 and comparison["counts"]["source_only_atoms"] == 0
            and comparison["counts"]["pdb_only_atoms"] == 0, "IDENTITY_AMBIGUOUS", "lossy reference projection")
    return data, {"source_sha256": source.sha256, "bridge_sha256": digest(data), "reference_model_index": 0,
                  "inverse_atoms": mappings, "full_source_preserved_separately": True,
                  "legacy_Biopython_MoleculeKit_reader_equivalence_validated": False}


def normalize_post_names(raw):
    pdb = read_pdb_source(raw, expected_sha256=digest(raw))
    require(len(pdb.model_ids) == 1, "FRAME_SET_INVALID", "one prepared reference required")
    changes, output = [], raw.decode("ascii").splitlines(keepends=True)
    for atom in pdb.atoms:
        require(atom.record == "ATOM" and atom.altloc_raw == " ", "PREPARATION_OUTPUT_INVALID", "unsupported prepared atom")
        name = atom.residue.source_resname
        target = name if name in AA else POST_NAMES.get(name)
        require(target is not None, "PREPARATION_OUTPUT_INVALID", "unknown prepared residue; no unreviewed substitution")
        if target != name:
            line = output[atom.line - 1]
            output[atom.line - 1] = line[:17] + target + line[20:]
            changes.append({"line": atom.line, "input_atom_uid": atom.uid, "before_resname": name,
                            "after_resname": target, "locator": vars(atom.locator), "operation": "name_assignment_not_mutation"})
    return "".join(output).encode("ascii"), changes


def read_pqr(raw, *, legacy_zero_hydrogen_radius=False, fixture_sha256=None, serialization=None):
    from .local_budget import artifact_bytes, trusted_local
    require(isinstance(raw, bytes) and 0 < len(raw) <= artifact_bytes(), "PREPARATION_OUTPUT_INVALID", "PQR byte bound")
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise M1Error("PREPARATION_OUTPUT_INVALID", "ASCII PQR required") from exc
    rows, seen, serials = [], set(), set()
    row_limit = 99999 if trusted_local() else 28036 if fixture_sha256 == O95425_SHA256 else 10000
    raw_sha256 = digest(raw)
    block = 0
    attached = {}
    if serialization is not None:
        require(trusted_local() and isinstance(serialization,list) and len(serialization)<=row_limit,
                "CHARGE_MAPPING_UNPROVEN", "unexpected PQR serializer metadata")
        attached = {row["serial"]:row for row in serialization}
        require(len(attached)==len(serialization),"CHARGE_MAPPING_UNPROVEN","duplicate serializer serials")
    for number, line in enumerate(lines, 1):
        if line.startswith("TER") and trusted_local():
            block += 1
        if line.startswith(("REMARK", "HEADER", "TITLE", "TER", "END")) or not line.strip():
            continue
        require(len(line) <= 512, "PREPARATION_OUTPUT_INVALID", "PQR line bound")
        parts = line.split()
        require(len(parts) in (10, 11) and parts[0] in ("ATOM", "HETATM"), "PREPARATION_OUTPUT_INVALID", "PQR whitespace schema")
        record, serial, name, resname = parts[:4]
        chain = parts[4] if len(parts) == 11 else None
        seq = parts[5] if chain is not None else parts[4]
        values = parts[6:] if chain is not None else parts[5:]
        sequence = re.fullmatch(r"(-?[0-9]+)([A-Za-z]?)", seq)
        require(sequence and re.fullmatch(r"[0-9]+", serial) and serial not in serials and len(rows) < row_limit,
                "PREPARATION_OUTPUT_INVALID", "PQR identity/row bound")
        for value in values:
            _number(value)
        radius = _number(values[-1])
        # The preserved PARSE/AMBER charge pass emits zero-radius hydrogens.
        require(radius > 0 or (legacy_zero_hydrogen_radius and radius == 0 and name.startswith("H")),
                "PREPARATION_OUTPUT_INVALID", "nonpositive radius")
        key = record, chain, block, sequence[1], sequence[2], resname, name
        saved = attached.get(serial)
        if serialization is not None:
            require(saved is not None and chain is None and len(saved["chain"])==1
                    and (name, sequence[1], sequence[2])==(saved["name"],saved["resnum"],saved["insertion"]),
                    "CHARGE_MAPPING_UNPROVEN", "PQR serialized atom identity differs")
            key = record, saved["chain"], sequence[1], sequence[2], resname, name
        require(key not in seen, "IDENTITY_AMBIGUOUS", "duplicate PQR atom identity")
        seen.add(key)
        serials.add(serial)
        rows.append({"pqr_atom_uid": "q_" + digest(canonical([raw_sha256, number])), "line": number,
            "record": record, "serial_raw": serial, "atom_name": name, "resname": resname, "chain_raw": chain,
            "resnum_raw": sequence[1], "insertion_raw": sequence[2], "xyz_raw": values[:3],
            "charge_raw": values[3], "radius_raw": values[4]})
        if trusted_local():
            rows[-1]["chain_block"] = block
        if saved is not None:
            rows[-1]["serializer_chain"] = saved["chain"]
    require(serialization is None or serials==attached.keys(),
            "CHARGE_MAPPING_UNPROVEN", "PQR serializer atom coverage differs")
    require(rows, "PREPARATION_OUTPUT_INVALID", "empty PQR is not scientific empty")
    return rows


def charge_correspondence(before, rows, bindings):
    """Names/author locators first; no coordinate or row-position matching."""
    chains = {a.locator.chain for a in before.atoms}
    resolved = {}
    if len(chains) > 1 and any(row["chain_raw"] is None and "serializer_chain" not in row for row in rows):
        from .local_budget import trusted_local
        require(trusted_local() and all(row["chain_raw"] is None and "chain_block" in row for row in rows),
                "CHARGE_MAPPING_UNPROVEN", "multi-chain PQR must expose complete chain blocks")
        expected = {chain: {} for chain in chains}
        for atom in before.atoms:
            if atom.atom_name_raw.strip() == "CA":
                loc = atom.locator
                expected[loc.chain][loc.resnum, loc.insertion] = tuple(map(_number, atom.xyz_raw))
        observed = {}
        for row in rows:
            coordinates = observed.setdefault(row["chain_block"], {})
            if row["atom_name"] == "CA":
                key = row["resnum_raw"], row["insertion_raw"] or " "
                require(key not in coordinates, "CHARGE_MAPPING_UNPROVEN", "repeated CA within a PQR chain block")
                coordinates[key] = tuple(map(_number, row["xyz_raw"]))
        for block, coordinates in observed.items():
            matches = [c for c, reference in expected.items() if coordinates and coordinates == reference]
            require(len(matches) == 1, "CHARGE_MAPPING_UNPROVEN",
                    "PQR chain block has no unique exact author residue/CA identity")
            resolved[block] = matches[0]
        require(len(resolved) == len(chains) and set(resolved.values()) == chains,
                "CHARGE_MAPPING_UNPROVEN", "PQR chain block coverage differs")
    index = {(a.locator, a.residue.source_resname, a.atom_name_raw.strip()): a for a in before.atoms}
    require(len(index) == len(before.atoms), "IDENTITY_AMBIGUOUS", "preparation atom key collision")
    result, used, projected = [], set(), set()
    for row in rows:
        chain = row["chain_raw"] or row.get("serializer_chain")
        if chain is None:
            chain = resolved[row["chain_block"]] if resolved else next(iter(chains))
        locator = Locator(chain, row["resnum_raw"], row["insertion_raw"] or " ", "")
        require(locator in bindings, "CHARGE_MAPPING_UNPROVEN", "PQR residue has no explicit pre-prepare binding")
        source_residue = bindings[locator]
        resname = POST_NAMES.get(row["resname"], row["resname"])
        require(resname == source_residue.scientific_resname, "CHARGE_MAPPING_UNPROVEN", "unexplained PQR residue name")
        identity = locator, resname, row["atom_name"]
        require(identity not in projected, "CHARGE_MAPPING_UNPROVEN", "PQR identifiers collide after declared name/chain projection")
        projected.add(identity)
        atom = index.get((locator, resname, row["atom_name"]))
        if atom is not None:
            used.add(atom.uid)
        result.append({**row, "source_residue_uid": source_residue.uid,
            "preparation_atom_uid": atom.uid if atom else None,
            "derived_locator": vars(locator), "chain_evidence": ("explicit_PQR_chain" if row["chain_raw"] is not None
                else "pinned_PDB2PQR_serializer_identity" if "serializer_chain" in row
                else "unique_TER_block_exact_CA" if resolved
                else "declared_single_input_chain_projection_unchanged_author_residue_identifier"),
            "name_projection": {"PQR": row["resname"], "input": resname},
            "added_atom_ancestry_proven": False})
    return {"rows": result, "missing_input_atom_uids": sorted({a.uid for a in before.atoms} - used),
            "mapping_method": "unique_identifiers_not_coordinates_or_row_counts"}


@dataclass(frozen=True)
class StepOutput:
    stage: str
    input_sha256: str
    tool: str
    version: str
    argv: tuple
    exit_code: int
    signal: int | None = None
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False
    complete_tree_termination_proven: bool = False
    evidence_class: str = "submitted_unattested"

    def assess(self, *, stage, input_sha256, argv=None):
        require(self.stage == stage and self.input_sha256 == input_sha256 and self.tool and self.version,
                "PREPARATION_EXECUTION_INVALID", "stage/input/version binding")
        require(type(self.exit_code) is int and (self.signal is None or type(self.signal) is int),
                "PREPARATION_EXECUTION_INVALID", "exit/signal schema")
        require(isinstance(self.stdout, bytes) and isinstance(self.stderr, bytes)
                and len(self.stdout) <= LOG_LIMIT and len(self.stderr) <= LOG_LIMIT,
                "RESOURCE_LIMIT", "bounded child diagnostics required; truncation is not success")
        if argv is not None:
            require(self.argv == tuple(argv), "PREPARATION_EXECUTION_INVALID", "fixed argv mismatch")
        require(self.exit_code == 0 and self.signal is None and not self.timed_out,
                "TOOL_EXIT", "failed/timed-out preparation cannot be complete or scientific empty")
        require(self.complete_tree_termination_proven, "PREPARATION_CONTAINMENT_UNPROVEN", "whole child tree exit required before accepting outputs")
        require(self.evidence_class == "synthetic", "SCIENTIFIC_EXECUTION_UNAPPROVED",
                "real output acceptance requires a separately approved scientific runtime; caller assertions are not admission")
        return {**vars(self), "argv": list(self.argv), "stdout": None, "stderr": None,
                "stdout_sha256": digest(self.stdout), "stderr_sha256": digest(self.stderr)}


@dataclass(frozen=True)
class PreparationEvidence:
    artifacts: object
    bindings: object
    report: object

    def __post_init__(self):
        for field in ("artifacts", "bindings", "report"):
            value = dict(getattr(self, field))
            if field == "report":
                value = _json(canonical(value))
            object.__setattr__(self, field, MappingProxyType(value))

    def m1_preparation_inputs(self):
        """Compatible with prepare_m1; partial evidence, not a complete M1 job."""
        return {"prepared": self.artifacts["prepared.pdb"], "bindings": dict(self.bindings),
                "preparation_map": self.artifacts["preparation_map.json"], "charges": self.artifacts["charges.pqr"]}


@measured("preparation.validate_capture")
def collect_preparation(source, *, bridge, before, pre_cif, charge_pqr, charge_step, raw_prepared,
                        prepared_cif, structure_step, internal_before, internal_after, diagnostics=None, synthetic=False):
    """Validate/capture two-pass outputs. Currently synthetic execution only.

    MoleculeKit input filtering/mutateResidue and the second pass remain native
    integration gates, not reimplemented selection/protonation algorithms here.
    For real 1RLW no pre-mutation is required; unknown source protein names fail.
    """
    require(synthetic or source.sha256 == CIF_1RLW_SHA256, "SOURCE_INVALID", "1RLW-only pilot")
    require(synthetic, "SCIENTIFIC_EXECUTION_UNAPPROVED", "no scientific acceptance profile approved")
    payloads = (bridge, before, pre_cif, charge_pqr, raw_prepared, prepared_cif, internal_before, internal_after)
    require(all(isinstance(p, bytes) and 0 < len(p) <= FILE_LIMIT for p in payloads)
            and sum(map(len, payloads)) <= TOTAL_LIMIT, "RESOURCE_LIMIT", "preparation artifact bounds")
    bp = read_pdb_source(bridge, expected_sha256=digest(bridge))
    pre = read_pdb_source(before, expected_sha256=digest(before))
    require(len(bp.model_ids) == len(pre.model_ids) == 1, "FRAME_SET_INVALID", "one reference only")
    bridge_map, _ = compare_mmcif_to_pdb(source, bp)
    require(bridge_map["counts"]["source_only_atoms"] == bridge_map["counts"]["pdb_only_atoms"] == 0
            and bridge_map["counts"]["coordinate_changes"] == 0,
            "PREPARATION_INPUT_INVALID", "bridge lost/changed source reference atoms")
    require(all(a.record != "ATOM" or a.field("auth_comp_id").value in AA for a in source.reference_atoms),
            "PREPARATION_INPUT_INVALID", "nonstandard input requires reviewed native mutation capture")
    pre_map, bindings = compare_mmcif_to_pdb(source, pre)
    require(pre_map["counts"]["pdb_only_atoms"] == pre_map["counts"]["coordinate_changes"] == 0
            and all(a.record == "ATOM" and a.residue.source_resname in AA for a in pre.atoms)
            and pre_map["all_pdb_protein_residues_bound"]
            and all(a["record"] == "HETATM" for a in pre_map["source_only_atoms"]),
            "PREPARATION_INPUT_INVALID", "initial standard-protein filter changed/lost protein identity or coordinates")
    request = fixed_pdb2pqr_request(before)
    charge_record = charge_step.assess(stage="charge_pass", input_sha256=digest(before), argv=request["argv"])
    require(charge_step.tool == "pdb2pqr" and charge_step.version == PDB2PQR_VERSION,
            "PREPARATION_EXECUTION_INVALID", "candidate PDB2PQR version differs")
    qmap = charge_correspondence(pre, read_pqr(charge_pqr), bindings)
    require(not qmap["missing_input_atom_uids"], "CHARGE_MAPPING_UNPROVEN", "PQR lost original atoms; inspect before proceeding")
    structure_record = structure_step.assess(stage="structure_pass", input_sha256=digest(before))
    require(structure_step.tool == "moleculekit" and structure_step.version == "1.3.4",
            "PREPARATION_EXECUTION_INVALID", "second-pass library transition is not approved")
    require(structure_step.argv == ("proteinPrepare", "pH=7.0", "returnDetails=False"),
            "PREPARATION_EXECUTION_INVALID", "second-pass settings changed")
    patched, patch = decimal_serial_adapter(internal_before)
    require(patched == internal_after, "PREPARATION_EXECUTION_INVALID", "internal decimal serial patch differs")
    internal = read_pdb_source(internal_after, expected_sha256=digest(internal_after))
    pre_index = {a.identity_key: a for a in pre.atoms}
    require({a.identity_key for a in internal.atoms} == set(pre_index)
            and all(all(Decimal(x) == Decimal(y) for x, y in zip(a.xyz_raw, pre_index[a.identity_key].xyz_raw)) for a in internal.atoms),
            "PREPARATION_EXECUTION_INVALID", "internal tool input changed identities/coordinates unexpectedly")
    final, post_names = normalize_post_names(raw_prepared)
    final, final_patch = decimal_serial_adapter(final)
    final_pdb = read_pdb_source(final, expected_sha256=digest(final))
    final_map, final_bindings = compare_mmcif_to_pdb(source, final_pdb)
    require(final_map["all_pdb_protein_residues_bound"], "PREPARATION_MAPPING_UNPROVEN", "incomplete final residue crosswalk")
    ca = [{"source_residue_uid": bindings[a.locator].uid, "preparation_atom_uid": a.uid, "xyz_raw": a.xyz_raw}
          for a in pre.atoms if a.atom_name_raw.strip() == "CA"]
    require(len(ca) == len(bindings) and len({a["source_residue_uid"] for a in ca}) == len(ca),
            "PREPARATION_MAPPING_UNPROVEN", "CA identity coverage")
    report = {"schema": "dreamm-m1-preparation/1", "source_sha256": source.sha256, "prepared_sha256": digest(final),
        "recipe_sha256": digest(canonical(recipe())), "evidence_class": "synthetic", "status": "synthetic_outputs_checked",
        "stage_order": list(STEPS), "charge_pass": charge_record, "structure_pass": structure_record,
        "source_to_pre": pre_map, "source_to_prepared": final_map, "pre_CA": ca,
        "post_substitutions": post_names, "internal_serial_patch": patch, "final_serial_patch": final_patch, "charge_mapping": qmap,
        "canonical_CIF_identity_verified": False, "preparation_run_verified": False,
        "scientific_preparation_validated": False, "complete_atom_lineage": False,
        "native_filter_and_mutation_execution_verified": False, "classification_performed": False}
    artifacts = {"source.cif": source.original, "reference.pdb": bridge, "preparation_input.pdb": before,
        "preparation_input.cif": pre_cif, "charges.pqr": charge_pqr, "proteinprepare_raw.pdb": raw_prepared,
        "prepared.pdb": final, "prepared.cif": prepared_cif, "internal_before.pdb": internal_before,
        "internal_after.pdb": internal_after, "charge_stdout.log": charge_step.stdout, "charge_stderr.log": charge_step.stderr,
        "structure_stdout.log": structure_step.stdout, "structure_stderr.log": structure_step.stderr,
        "preparation_map.json": canonical(report), "recipe.json": canonical(recipe()),
        "source_receipt.json": canonical(source.receipts()),
        "source_models.json": canonical(source.receipts()["source_models"]),
        "source_residues.json": canonical(source.receipts()["source_residues"])}
    diagnostics = diagnostics or {}
    require(set(diagnostics) <= {"charge_propka.txt", "structure_propka.txt", "charge_tool.log", "structure_tool.log"}
            and all(isinstance(b, bytes) and len(b) <= LOG_LIMIT for b in diagnostics.values()),
            "RESOURCE_LIMIT", "fixed bounded diagnostic roles only")
    artifacts.update(diagnostics)
    note_artifacts(artifacts)
    return PreparationEvidence(artifacts, final_bindings, report)


def compare_preserved_preparation(source, candidate, historical):
    """Static diagnostic. Per-atom identities, not matching 10/988/155 counts."""
    historical_pdb = read_pdb_source(historical, expected_sha256=digest(historical))
    candidate_pdb = read_pdb_source(candidate, expected_sha256=digest(candidate))
    old, _ = compare_mmcif_to_pdb(source, historical_pdb)
    new, _ = compare_mmcif_to_pdb(source, candidate_pdb)
    def index(pdb):
        return {a.identity_key: a for a in pdb.reference_atoms}
    old_atoms, new_atoms = index(historical_pdb), index(candidate_pdb)
    same, changed = [], []
    for key in sorted(old_atoms.keys() & new_atoms.keys()):
        a, b = old_atoms[key], new_atoms[key]
        equal = all(Decimal(x) == Decimal(y) for x, y in zip(a.xyz_raw, b.xyz_raw))
        pair = {"historical_atom_uid": a.uid, "candidate_atom_uid": b.uid,
                "locator": vars(a.locator), "resname": a.residue.source_resname,
                "atom_name": a.atom_name_raw.strip(), "historical_xyz_raw": a.xyz_raw,
                "candidate_xyz_raw": b.xyz_raw, "coordinates_equal": equal}
        (same if equal else changed).append(pair)
    return {"schema": "dreamm-preparation-comparison/1", "source_sha256": source.sha256,
        "historical_sha256": digest(historical), "candidate_sha256": digest(candidate),
        "historical_source_correspondence": old, "candidate_source_correspondence": new,
        "coordinate_equal_pairs": same, "coordinate_changed_pairs": changed,
        "historical_only_atom_uids": sorted(old_atoms[k].uid for k in old_atoms.keys() - new_atoms.keys()),
        "candidate_only_atom_uids": sorted(new_atoms[k].uid for k in new_atoms.keys() - old_atoms.keys()),
        "historical_status": "candidate_evidence_not_golden", "added_atom_ancestry_proven": False,
        "scientific_equivalence_proven": False}


def write_preparation_evidence(path, evidence):
    """Private capture using the existing M1 filesystem guards; no job commit."""
    path = Path(path).absolute()
    _parents(path.parent)
    require(sum(map(len, evidence.artifacts.values())) <= TOTAL_LIMIT, "RESOURCE_LIMIT", "capture total bound")
    require(all(re.fullmatch(r"[a-z_]+\.(json|pdb|cif|pqr|log|txt)", n) and len(b) <= FILE_LIMIT
                for n, b in evidence.artifacts.items()), "RESOURCE_LIMIT", "internal artifact names/bounds")
    path.mkdir(mode=0o700, exist_ok=False)
    files = {n: {"sha256": digest(b), "bytes": len(b)} for n, b in evidence.artifacts.items()}
    payloads = {**evidence.artifacts, "manifest.json": canonical({"schema": "dreamm-preparation-files/1", "files": files})}
    for name, raw in payloads.items():
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path / name, flags, 0o600), "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())


def failed_preparation_evidence(source, step, *, error_code, partial=None):
    """Retain bounded failed/partial evidence without marking preparation done."""
    require(isinstance(step, StepOutput) and step.stage in ("charge_pass", "structure_pass")
            and re.fullmatch(r"[A-Z_]{1,64}", error_code), "PREPARATION_EXECUTION_INVALID", "failure evidence")
    require(isinstance(step.stdout, bytes) and isinstance(step.stderr, bytes), "PREPARATION_EXECUTION_INVALID", "diagnostic bytes")
    prefix = step.stage.replace("_pass", "")
    partial = partial or {}
    require(set(partial) <= {"charges.pqr", "proteinprepare_raw.pdb", "internal_before.pdb", "internal_after.pdb"}
            and all(isinstance(b, bytes) and len(b) <= FILE_LIMIT for b in partial.values()),
            "RESOURCE_LIMIT", "bounded fixed partial roles")
    report = {"schema": "dreamm-m1-preparation/1", "source_sha256": source.sha256,
        "prepared_sha256": None, "recipe_sha256": digest(canonical(recipe())), "status": "failed_incomplete",
        "errors": [{"code": error_code, "stage": step.stage}], "evidence_class": "synthetic" if step.evidence_class == "synthetic" else "submitted_unattested",
        "exit_code": step.exit_code, "signal": step.signal, "timed_out": step.timed_out,
        "step": {"stage": step.stage, "input_sha256": step.input_sha256, "tool": step.tool,
                 "version": step.version, "argv": list(step.argv)},
        "complete_tree_termination_proven": step.complete_tree_termination_proven,
        "diagnostics_truncated": len(step.stdout) > LOG_LIMIT or len(step.stderr) > LOG_LIMIT,
        "preparation_run_verified": False, "scientific_preparation_validated": False,
        "classification_performed": False, "slot_release_or_publication_authorized": False}
    return PreparationEvidence({"source.cif": source.original, "recipe.json": canonical(recipe()),
        "preparation_map.json": canonical(report), prefix + "_stdout.log": step.stdout[:LOG_LIMIT],
        prefix + "_stderr.log": step.stderr[:LOG_LIMIT], **partial}, {}, report)


def plan_1rlw_preparation(source, historical_prepared):
    """Real frozen-input staging only. Does not impersonate native filtering.

    The candidate standard-ATOM view is useful for an explicitly approved first
    charge-pass diagnostic, not proof of MoleculeKit filtering/default parity.
    """
    require(source.sha256 == CIF_1RLW_SHA256, "SOURCE_INVALID", "frozen public 1RLW only")
    bridge, inverse = reference_bridge(source)
    require(all(a.record != "ATOM" or a.field("auth_comp_id").value in AA for a in source.reference_atoms),
            "PREPARATION_INPUT_INVALID", "pilot has unhandled nonstandard protein")
    candidate = b"".join(x for x in bridge.splitlines(keepends=True) if x.startswith(b"ATOM  ")) + b"END\n"
    candidate, _ = decimal_serial_adapter(candidate)
    request = fixed_pdb2pqr_request(candidate)
    historical = read_pdb_source(historical_prepared, expected_sha256=digest(historical_prepared))
    old_map, _ = compare_mmcif_to_pdb(source, historical)
    report = {"schema": "dreamm-m1-preparation/1", "status": "blocked_before_native_preparation",
        "source_sha256": source.sha256, "prepared_sha256": None, "recipe_sha256": digest(canonical(recipe())),
        "candidate_input_sha256": digest(candidate), "historical_prepared_sha256": digest(historical_prepared),
        "historical_counts": old_map["counts"], "native_execution_authorized": False,
        "native_MoleculeKit_filter_verified": False, "bridge_reader_equivalence_validated": False,
        "preparation_run_verified": False, "scientific_preparation_validated": False,
        "classification_performed": False, "errors": [
            {"code": "SCIENTIFIC_RUNTIME_APPROVAL_REQUIRED"}, {"code": "MOLECULEKIT_SECOND_PASS_BINDING_REVIEW_REQUIRED"}],
        "scope": "real_input_plan_and_historical_candidate_comparison_reference_not_preparation"}
    artifacts = {"source.cif": source.original, "reference.pdb": bridge, "reference_map.json": canonical(inverse),
        "candidate_input.pdb": candidate, "invocation.json": canonical(request), "recipe.json": canonical(recipe()),
        "historical_reference.json": canonical(old_map), "preparation_map.json": canonical(report)}
    return PreparationEvidence(artifacts, {}, report)


def _verify_preparation_evidence(path):
    path = Path(path).absolute()
    _parents(path)
    manifest = _json(_read(path / "manifest.json", 64 * 1024))
    require(manifest["schema"] == "dreamm-preparation-files/1" and 0 < len(manifest["files"]) <= 24,
            "PREPARATION_OUTPUT_INVALID", "manifest schema/count")
    blobs, total = {}, 0
    for name, entry in manifest["files"].items():
        require(re.fullmatch(r"[a-z_]+\.(json|pdb|cif|pqr|log|txt)", name), "PREPARATION_OUTPUT_INVALID", "manifest path")
        raw = _read(path / name)
        total += len(raw)
        require(total <= TOTAL_LIMIT and len(raw) == entry["bytes"] and digest(raw) == entry["sha256"],
                "PREPARATION_OUTPUT_INVALID", "artifact integrity")
        blobs[name] = raw
    report = _json(blobs["preparation_map.json"])
    require(report["source_sha256"] == digest(blobs["source.cif"])
            and report["recipe_sha256"] == digest(blobs["recipe.json"])
            and not report["preparation_run_verified"] and not report["scientific_preparation_validated"],
            "PREPARATION_OUTPUT_INVALID", "source/output/recipe/evidence-class binding")
    if report["status"] == "failed_incomplete":
        require(report["errors"] and report["prepared_sha256"] is None
                and not report["slot_release_or_publication_authorized"], "PREPARATION_OUTPUT_INVALID", "failure semantics")
        return report
    if report["status"] == "blocked_before_native_preparation":
        require(report["prepared_sha256"] is None and not report["native_execution_authorized"]
                and report["candidate_input_sha256"] == digest(blobs["candidate_input.pdb"]),
                "PREPARATION_OUTPUT_INVALID", "blocked plan cannot attest preparation")
        return report
    require(report["prepared_sha256"] == digest(blobs["prepared.pdb"]), "PREPARATION_OUTPUT_INVALID", "prepared output hash")
    saved = _json(blobs["source_receipt.json"])
    residues = {uid: SourceResidue(**value) for uid, value in saved["source_residues"].items()}
    require(all(k == v.uid for k, v in residues.items()), "PREPARATION_OUTPUT_INVALID", "source residue UIDs")
    atoms = tuple(CifAtom(a["atom_uid"], a["atom_site_row_index"], a["model_index"], residues[a["residue_uid"]],
        MappingProxyType({k: CifValue(**v) for k, v in a["fields"].items()})) for a in saved["source_atoms"])
    models = tuple(m["source_model_id"] for m in saved["source_models"]["models"])
    require(len(atoms) <= 10000 and all(a.row_index == i and a.residue.source_sha256 == digest(blobs["source.cif"])
            and a.residue.model_index == a.model_index and a.residue.model_id == models[a.model_index]
            for i, a in enumerate(atoms)), "PREPARATION_OUTPUT_INVALID", "synthetic source snapshot binding")
    source = CifSource(blobs["source.cif"], saved["source"]["block_name"],
        tuple(saved["source"]["atom_site_tags_in_order"]), atoms, models, MappingProxyType(saved["parser_runtime"]))
    def step(name):
        r = report[name]
        return StepOutput(r["stage"], r["input_sha256"], r["tool"], r["version"], tuple(r["argv"]), r["exit_code"],
            r["signal"], blobs[name.replace("_pass", "") + "_stdout.log"], blobs[name.replace("_pass", "") + "_stderr.log"],
            r["timed_out"], r["complete_tree_termination_proven"], r["evidence_class"])
    rebuilt = collect_preparation(source, bridge=blobs["reference.pdb"], before=blobs["preparation_input.pdb"],
        pre_cif=blobs["preparation_input.cif"], charge_pqr=blobs["charges.pqr"], charge_step=step("charge_pass"),
        raw_prepared=blobs["proteinprepare_raw.pdb"], prepared_cif=blobs["prepared.cif"], structure_step=step("structure_pass"),
        internal_before=blobs["internal_before.pdb"], internal_after=blobs["internal_after.pdb"], synthetic=True)
    require(canonical(dict(rebuilt.report)) == canonical(report), "PREPARATION_OUTPUT_INVALID", "mapping/charge/geometry receipt differs on recomputation")
    return report


def verify_preparation_evidence(path):
    try:
        return _verify_preparation_evidence(path)
    except M1Error:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError) as exc:
        raise M1Error("PREPARATION_OUTPUT_INVALID", "unavailable/malformed preparation capture") from exc
