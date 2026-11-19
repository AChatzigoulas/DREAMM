"""Native two-pass preparation and single-reference extraction inside an admitted worker."""
from dataclasses import replace
from importlib import import_module
from pathlib import Path
import os
import subprocess
import sys
import time

from .capture import _parents, _read
from .execution_profiles import ExecutionProfile
from .extraction import assemble_csv, bound_records, conservation_scores, dssp_features, hydrophobicity, rsa_features
from .hotspot_benchmarks import query_input
from .identity import (AA, CIF_3RUK_SHA256, O95425_SHA256, PDB_2KS4_REFERENCE_SHA256,
                       PDB_8YOQ_SHA256, Locator, canonical, descriptor_adapter, digest, require)
from .inference import predict_1rlw, predict_8yoq, predict_scaling_fixture, require_classifier_admission
from .measurement import StageRecorder, measured
from .mmcif_evidence import compare_mmcif_to_pdb
from .mmcif_input import CIF_1RLW_SHA256
from .msms_mesh import MESH_PROFILE, capture_o95425_mesh
from .native_checkpoints import HH_FILES, conservation_checkpoint, validate_python_features
from .pdb_evidence import read_python_features
from .pdb_input import PdbSource, read_pdb_source, source_prepared_correspondence
from .pipeline import prepare_m1
from .preparation import (PDB2PQR_OPTIONS, POST_NAMES, charge_correspondence, decimal_serial_adapter,
                          fixed_pdb2pqr_request, read_pqr, reference_bridge)
from .protdcal import collect_descriptors, plan_m1_descriptors
from .receipt_chunks import write_scientific_receipt
from .rocky_admission import M1_SECONDS
from .scaling_fixtures import (ALT_3RUK, JAVA_3RUK_SHA256, STEM_3RUK, STEM_O95425, Frozen3Ruk,
                              exact_prepared_projection, frozen_o95425, identity_segment, legacy_3ruk_bridge,
                              selected_3ruk_source, verify_3ruk_charge_altlocs)
from .source_packet import load_1rlw_source_packet


TOOLS = Path("/opt/dreamm-features/bin")
PDB2PQR = "/opt/dreamm-preparation/bin/pdb2pqr30"
DSSP_TOOL = TOOLS / "mkdssp"
SASA_TOOL = TOOLS / "freesasa"
MSMS_TOOL = TOOLS / "msms"
HHBLITS = "/opt/dreamm-hhsuite/bin/hhblits"
DATABASE = "/data/uniclust30_2018_08/uniclust30_2018_08"
FULL_BENCHMARK_SHA = {"8YOQ": PDB_8YOQ_SHA256, "3RUK": CIF_3RUK_SHA256, "O95425-2": O95425_SHA256,
                      "2KS4": PDB_2KS4_REFERENCE_SHA256}
STEMS = {"1RLW": "1rlw_Prot.pdb", "8YOQ": "8YOQhFSP1_20261015094400_Prot.pdb",
         "3RUK": STEM_3RUK, "O95425-2": STEM_O95425, "2KS4": "2KS4_model0_Prot.pdb"}
ATOMS_2KS4 = 2705


def _tool(argv, work, stage, seconds):
    """The admitting supervisor owns aggregate limits, tree exit and publication."""
    stdout, stderr = work / (stage + ".stdout"), work / (stage + ".stderr")
    with stdout.open("xb") as out, stderr.open("xb") as err:
        result = subprocess.run(argv, cwd=work, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                timeout=seconds, check=False)
    require(result.returncode == 0, "TOOL_EXIT", stage + " failed; inspect attempt logs")
    return _read(stdout)


def _pdb(raw):
    return read_pdb_source(raw, expected_sha256=digest(raw))


def frozen_2ks4_reference(raw):
    source = read_pdb_source(raw, expected_sha256=PDB_2KS4_REFERENCE_SHA256)
    require(source.model_ids == ("1",) and len({a.locator for a in source.atoms}) == 176,
            "SOURCE_INVALID", "only the original 2KS4 model-0 derivative is admitted")
    identity_segment(source, ATOMS_2KS4)
    return source


def frozen_8yoq(raw):
    source = read_pdb_source(raw, expected_sha256=PDB_8YOQ_SHA256)
    protein = [a for a in source.atoms if a.record == "ATOM"]
    ca = {a.locator for a in protein if a.atom_name_raw.strip() == "CA"}
    require(len(source.model_ids) == 1 and len(source.atoms) == 3151 and len(protein) == 2734
            and len(ca) == 367 and {a.locator for a in protein} == ca
            and all(a.locator.chain == "A" and a.locator.insertion == " " and not a.locator.segment
                    and a.altloc_raw == " " and a.residue.source_resname in AA for a in protein),
            "PREPARATION_INPUT_INVALID", "frozen 8YOQ protein identity")
    return source


def _8yoq_precharge_identity(pdb):
    """Bind the one synthetic reader segment without modifying the tool input."""
    require(len(pdb.model_ids) == 1 and len(pdb.atoms) == 2734 and
            {a.locator.segment for a in pdb.atoms} in ({""}, {"0"}) and
            all(a.record == "ATOM" and a.locator.chain == "A" and a.locator.insertion == " " for a in pdb.atoms),
            "PREPARATION_MAPPING_UNPROVEN", "unexpected 8YOQ pre-charge segment layout")
    atoms = tuple(replace(a, locator=replace(a.locator, segment="")) for a in pdb.atoms)
    require(len({a.identity_key for a in atoms}) == len(atoms), "IDENTITY_AMBIGUOUS", "8YOQ segment projection collision")
    return replace(pdb, atoms=atoms)


def _8yoq_legacy_prepared(pdb):
    """Project the proven writer segment to the exact legacy tool artifact."""
    parsed = _pdb(pdb)
    require(len(parsed.model_ids) == 1 and len(parsed.atoms) == 5691 and
            {a.locator.segment for a in parsed.atoms} in ({""}, {"0"}) and
            all(a.record == "ATOM" and a.locator.chain == "A" and a.locator.insertion == " " for a in parsed.atoms),
            "PREPARATION_MAPPING_UNPROVEN", "unexpected 8YOQ prepared segment layout")
    projected = b"".join(line[:72] + b"    " + line[76:] if line.startswith(b"ATOM  ") else line
                         for line in pdb.splitlines(keepends=True))
    require(digest(projected) == "8c342a664042ffa8ef07f37a9ef1308f6833c20c7232df90b5dd8cb842df3324",
            "PREPARATION_MAPPING_UNPROVEN", "8YOQ legacy projection differs from archived prepared bytes")
    return projected


@measured("preparation.native_filter")
def _filter(source, work, molecule_type, fixture):
    if fixture == "3RUK":
        bridge = legacy_3ruk_bridge(source)
        inverse = {"source_sha256": source.sha256, "bridge_sha256": digest(bridge),
                   "operation": "legacy_Bio.PDB_model_0_before_MoleculeKit_altloc_selection"}
    elif isinstance(source, PdbSource):
        bridge = source.original
        inverse = {"source_sha256": source.sha256, "operation": "unchanged_archived_PDB"}
    else:
        bridge, inverse = reference_bridge(source)
    (work / "bridge.pdb").write_bytes(bridge)
    (work / "bridge-map.json").write_bytes(canonical(inverse))
    molecule = molecule_type(str(work / "bridge.pdb"))
    molecule.dropFrames(keep=0)
    molecule.filter("protein and chain A and not resname UNK" if fixture == "3RUK" else "protein and not resname UNK")
    require(set(molecule.resname) <= set(AA), "PREPARATION_INPUT_INVALID", "frozen fixture requires standard residues")
    molecule.write(str(work / "before.pdb"))
    before, _ = decimal_serial_adapter(_read(work / "before.pdb"))
    (work / "before.pdb").write_bytes(before)
    molecule.write(str(work / "before.cif"))
    pre = _pdb(before)
    selected = source
    if fixture == "3RUK":
        selected, alternate = selected_3ruk_source(source, _pdb(bridge), pre)
        (work / "alternate-conformers.json").write_bytes(canonical(alternate))
        report, bindings = source_prepared_correspondence(selected, identity_segment(pre, 3700, altlocs=ALT_3RUK))
        all_bound, added = report["all_prepared_residues_bound"], report["counts"]["prepared_only_atoms"]
    elif isinstance(source, PdbSource):
        normalized = (_8yoq_precharge_identity(pre) if fixture == "8YOQ" else
                      identity_segment(pre, ATOMS_2KS4 if fixture == "2KS4" else 14108))
        report, bindings = source_prepared_correspondence(source, normalized)
        all_bound, added = report["all_prepared_residues_bound"], report["counts"]["prepared_only_atoms"]
        (work / "precharge-segment-map.json").write_bytes(canonical({"before_sha256": digest(before),
            "source_sha256": source.sha256, "observed_segment": pre.atoms[0].locator.segment,
            "identity_segment": "", "counts": report["counts"], "tool_input_bytes_unchanged": True}))
    else:
        report, bindings = compare_mmcif_to_pdb(source, pre)
        all_bound, added = report["all_pdb_protein_residues_bound"], report["counts"]["pdb_only_atoms"]
    require(all_bound and report["counts"]["coordinate_changes"] == 0
            and added == 0 and all(a["record"] == "HETATM" for a in report["source_only_atoms"]),
            "PREPARATION_MAPPING_UNPROVEN", "native filter changed protein atoms or coordinates")
    ca = {}
    for i, name in enumerate(molecule.name):
        if name == "CA":
            locator = Locator(str(molecule.chain[i]), str(molecule.resid[i]), str(molecule.insertion[i]).strip() or " ")
            require(locator in bindings and locator not in ca, "PREPARATION_MAPPING_UNPROVEN", "native pre-prepare CA identity")
            ca[locator] = molecule.coords[i, :, 0].copy()
    require(ca.keys() == bindings.keys(), "PREPARATION_MAPPING_UNPROVEN", "native pre-prepare CA coverage")
    (work / "pre-ca.json").write_bytes(canonical([
        {"locator": vars(loc), "xyz": [float(v) for v in xyz]} for loc, xyz in ca.items()
    ]))
    return molecule, before, ca, bindings, selected


@measured("preparation.native_charge")
def _charge(before, work, bindings, seconds, *, fixture="1RLW"):
    # Reuse the candidate request's input validation. The full worker requires
    # the preserved 3.5.2 compatibility line rather than the 3.8 diagnostic.
    if fixture == "3RUK":
        require(decimal_serial_adapter(before)[0] == before, "PREPARATION_INPUT_INVALID", "3RUK charge serials")
        pdb = identity_segment(_pdb(before), 3700, altlocs=ALT_3RUK)
    else:
        fixed_pdb2pqr_request(before)
        pdb = _pdb(before)
    target = work / "charges.pqr"
    argv = (PDB2PQR, *PDB2PQR_OPTIONS, str(work / "before.pdb"), str(target))
    _tool(argv, work, "charge", seconds)
    if fixture == "8YOQ":
        pdb = _8yoq_precharge_identity(pdb)
    elif fixture == "O95425-2":
        pdb = identity_segment(pdb, 14108)
    elif fixture == "2KS4":
        pdb = identity_segment(pdb, ATOMS_2KS4)
    source_sha256 = next(iter(bindings.values())).source_sha256
    rows = read_pqr(_read(target), legacy_zero_hydrogen_radius=True, fixture_sha256=source_sha256)
    mapping = charge_correspondence(pdb, rows, bindings)
    require(not mapping["missing_input_atom_uids"], "CHARGE_MAPPING_UNPROVEN", "charge pass lost original atoms")
    if fixture == "3RUK":
        mapping["verified_retained_altlocs"] = verify_3ruk_charge_altlocs(pdb, rows)
    write_scientific_receipt(work / "charge-map.json", canonical(mapping), fixture_sha256=source_sha256)
    return mapping["rows"]


@measured("preparation.native_structure")
def _structure(source, molecule, work, before, pre_bindings, mkprep, fixture):
    # The dedicated worker runs one attempt. Restore the original hook on all exits.
    original = mkprep._pdb2pqr
    invocations = []

    def patch_serials(pdb_file, *args, **kwargs):
        require(not invocations, "PREPARATION_EXECUTION_INVALID", "unexpected second internal PDB2PQR invocation")
        path = Path(pdb_file)
        raw = _read(path)
        patched, _ = decimal_serial_adapter(raw)
        (work / "internal-before.pdb").write_bytes(raw)
        (work / "internal-after.pdb").write_bytes(patched)
        key = lambda a: (a.identity_key, tuple(float(x) for x in a.xyz_raw))
        require({key(a) for a in _pdb(patched).atoms} == {key(a) for a in _pdb(before).atoms},
                "PREPARATION_MAPPING_UNPROVEN", "internal charge input differs from filtered molecule")
        path.write_bytes(patched)
        invocations.append(digest(patched))
        return original(pdb_file, *args, **kwargs)

    mkprep._pdb2pqr = patch_serials
    try:
        prepared = mkprep.proteinPrepare(molecule, pH=7.0, returnDetails=False)
    finally:
        mkprep._pdb2pqr = original
    require(len(invocations) == 1, "PREPARATION_EXECUTION_INVALID", "missing internal PDB2PQR capture")
    for name in set(prepared.resname):
        if name not in AA:
            require(name in POST_NAMES, "PREPARATION_OUTPUT_INVALID", "unrecognized prepared residue name")
            prepared.resname[prepared.resname == name] = POST_NAMES[name]
    prepared.write(str(work / "prepared.cif"))
    prepared.write(str(work / "prepared.pdb"))
    final, _ = decimal_serial_adapter(_read(work / "prepared.pdb"))
    if isinstance(source, PdbSource):
        (work / "prepared-native.pdb").write_bytes(final)
        if fixture == "2KS4":
            # Blank the writer's synthetic segment, retaining the original chemical preparation.
            identity_segment(_pdb(final), ATOMS_2KS4)
            projected = b"".join(line[:72] + b"    " + line[76:] if line.startswith(b"ATOM  ") else line
                                 for line in final.splitlines(keepends=True))
        else:
            projected = (_8yoq_legacy_prepared(final) if fixture == "8YOQ" else exact_prepared_projection(final, fixture))
        (work / "prepared-segment-map.json").write_bytes(canonical({"native_sha256": digest(final),
            "legacy_tool_input_sha256": digest(projected), "ATOM_slice": [72, 76],
            "verified_against_archive": fixture != "2KS4"}))
        final = projected
    (work / "prepared.pdb").write_bytes(final)
    if isinstance(source, PdbSource):
        report, bindings = source_prepared_correspondence(source, _pdb(final))
        all_bound = report["all_prepared_residues_bound"]
    else:
        report, bindings = compare_mmcif_to_pdb(source, _pdb(final))
        all_bound = report["all_pdb_protein_residues_bound"]
    require(all_bound and bindings == pre_bindings,
            "PREPARATION_MAPPING_UNPROVEN", "prepared residue map differs from filtered source")
    write_scientific_receipt(work / "preparation-map.json", canonical(report), fixture_sha256=source.sha256)
    return final, bindings


@measured("features.native_DSSP")
def _dssp(work, bindings, seconds):
    from Bio.PDB import DSSP, MMCIFParser, PDBParser
    try:
        model = MMCIFParser(QUIET=True).get_structure("1rlw", str(work / "prepared.cif"))[0]
    except Exception:
        model = PDBParser(QUIET=True).get_structure("1rlw", str(work / "prepared.pdb"))[0]
    raw = _tool((str(DSSP_TOOL), str(work / "prepared.pdb")), work, "dssp", seconds)
    require(raw, "TOOL_EXIT", "empty DSSP artifact")
    result = DSSP(model, str(work / "dssp.stdout"), file_type="DSSP")
    return model, dssp_features(result.property_dict, bindings)


@measured("features.native_FreeSASA")
def _sasa(work, bindings, seconds):
    output = work / "surface.rsa"
    _tool((str(SASA_TOOL), "--resolution=1000", "--n-threads=16", "--format=rsa", "--hetatm",
           str(work / "prepared.pdb"), "-o", str(output)), work, "sasa", seconds)
    return rsa_features(_read(output), bindings)


@measured("features.native_MSMS")
def _depth(model, work, bindings, seconds, *, mesh_profile=None):
    from Bio.PDB import Selection, is_aa
    from Bio.PDB.ResidueDepth import _get_atom_radius, _read_vertex_array, ca_depth, residue_depth
    xyzr = work / "surface.xyzr"
    atoms = Selection.unfold_entities(model, "A")
    with xyzr.open("x", encoding="ascii") as out:
        for atom in atoms:
            x, y, z = atom.coord
            radius = _get_atom_radius(atom, rtype="united")
            out.write(f"{x:6.3f}\t{y:6.3f}\t{z:6.3f}\t{radius:1.2f}\n")
    prefix = work / "msms-surface"
    if mesh_profile is None:
        _tool((str(MSMS_TOOL), "-probe_radius", "1.5", "-if", str(xyzr), "-of", str(prefix)), work, "msms", seconds)
        vertices = _read(prefix.with_suffix(".vert"))  # cap the legacy parser input before parsing
        from .local_budget import trusted_local
        if trusted_local():
            from .msms_mesh import validate_mesh
            faces, count = validate_mesh(_read(prefix.with_suffix(".face")), vertices, xyzr=xyzr, atom_count=len(atoms))
            (work / "mesh.json").write_bytes(canonical({"faces": faces, "vertices": count}))
        surface = _read_vertex_array(str(prefix.with_suffix(".vert")))
    else:
        require(mesh_profile == MESH_PROFILE, "SCIENTIFIC_EXECUTION_UNAPPROVED", "unknown MSMS transport")
        surface = capture_o95425_mesh(MSMS_TOOL, xyzr, prefix, seconds, atom_count=len(atoms),
                                      fixture_sha256=next(iter(bindings.values())).source_sha256,
                                      reader=import_module("Bio.PDB.ResidueDepth"))
    require(surface.ndim == 2 and surface.shape[1] == 3 and len(surface) > 0,
            "FEATURE_SCHEMA_MISMATCH", "MSMS surface vertices")
    return bound_records(((r.parent.id, r.id[1], r.id[2], r.resname,
                           {"res_depth": residue_depth(r, surface), "ca_depth": ca_depth(r, surface)})
                          for r in Selection.unfold_entities(model, "R") if is_aa(r)), bindings, "MSMS")


@measured("features.native_HHblits")
def _conservation(work, dssp, bindings, seconds, cpu_limit, *, checkpoint=None, context=None, replay_only=False):
    require(os.cpu_count() is not None, "RESOURCE_LIMIT", "HHblits requires a visible CPU count")
    n_cpus = os.cpu_count() - 1
    require(0 < n_cpus <= cpu_limit, "RESOURCE_LIMIT", "admission must cover os.cpu_count()-1 HHblits threads")
    sequence = "".join(row["Amino acid"] for row in dssp.values())
    fasta, query = query_input(sequence, [bindings[loc].uid for loc in dssp])
    argv = (HHBLITS, "-i", str(work / "input.fasta"), "-d", DATABASE, "-cpu", str(n_cpus),
            "-M", "first", "-n", "3", "-ohhm", str(work / "output.hhm"), "-v", "0")
    if checkpoint is not None:
        require(n_cpus == 15, "RESOURCE_LIMIT", "the verified HHblits checkpoint profile requires 15 threads")
        settings = (*argv[:2], "<query>", *argv[3:12], "<hhm>", *argv[13:])
        context = {**context, "query": digest(canonical(query)),
                   "argv": digest(canonical(settings))}
        seconds = checkpoint.seconds(min(seconds, M1_SECONDS["hhblits"]))
        saved = checkpoint.load("hhblits", context, HH_FILES)
        if saved is not None:
            scores, verified = conservation_checkpoint(fasta, query, saved["output.hhm"], saved["input.hhr"])
            require(saved == verified, "CHECKPOINT_INVALID", "stored HHblits query or Conservation changed")
            for name, raw in verified.items():
                (work / name).write_bytes(raw)
            return dict(zip(dssp, scores))
    if replay_only:
        return None
    (work / "input.fasta").write_bytes(fasta)
    (work / "query.json").write_bytes(canonical(query))
    output = work / "output.hhm"
    _tool(argv, work, "hhblits", seconds)
    raw = _read(output)
    if checkpoint is None:
        scores = conservation_scores(raw, sequence)
    else:
        scores, files = conservation_checkpoint(fasta, query, raw, _read(work / "input.hhr"))
        checkpoint.commit("hhblits", context, files)
        (work / "conservation.json").write_bytes(files["conservation.json"])
    return dict(zip(dssp, scores))


@measured("features.native_dynamics")
def _dynamics(work, bindings):
    from prody import ANM, GNM, calcSqFlucts, parsePDB
    ca = parsePDB(str(work / "prepared.pdb")).select("calpha")
    require(ca is not None, "FEATURE_MAPPING_UNPROVEN", "ProDy CA selection")
    gnm = GNM("Protein")
    gnm.buildKirchhoff(ca)
    gnm.calcModes()
    anm = ANM("prot ANM analysis")
    anm.buildHessian(ca)
    anm.calcModes()
    g, a = calcSqFlucts(gnm), calcSqFlucts(anm)
    require(g.max() > g.min() and a.max() > a.min(), "NUMERIC_VALIDATION_UNPROVEN", "constant normal-mode feature")
    g = (g - g.min()) / (g.max() - g.min())
    a = (a - a.min()) / (a.max() - a.min())
    return bound_records(((chain, num, icode, name, {"GNM": x, "ANM": y}) for chain, num, icode, name, x, y in
                          zip(ca.getChids(), ca.getResnums(), ca.getIcodes(), ca.getResnames(), g, a)), bindings, "ProDy")


@measured("features.native_charge_and_gyration")
def _charge_features(work, bindings, charge_rows):
    from MDAnalysis import Universe
    path = work / "charges.pqr"
    if len({loc.chain for loc in bindings}) > 1 and any(row["chain_raw"] is None for row in charge_rows):
        # Restore only the validated chain field for MDAnalysis's residue
        # grouping. Keep the original PQR and all numeric tokens immutable.
        by_line = {row["line"]: row for row in charge_rows}
        path = work / "charges-chain-identity.pqr"
        with path.open("xb") as out:
            for line, raw in enumerate((work / "charges.pqr").read_bytes().splitlines(keepends=True), 1):
                row = by_line.get(line)
                if row is not None:
                    fields = raw.split(None, 5)
                    require(len(fields) == 6, "CHARGE_MAPPING_UNPROVEN", "PQR projection fields")
                    fields.insert(4, row["derived_locator"]["chain"].encode("ascii"))
                    raw = b" ".join(fields)
                out.write(raw)
    universe = Universe(str(path))
    by_serial = {int(row["serial_raw"]): row for row in charge_rows}
    require(len(by_serial) == len(charge_rows) == len(universe.atoms),
            "FEATURE_MAPPING_UNPROVEN", "PQR atom serial coverage")
    records, seen = [], set()
    for residue, charge in zip(universe.residues, universe.residues.charges):
        locators = set()
        for atom in residue.atoms:
            raw = by_serial.get(atom.id)
            require(raw is not None and atom.id not in seen and atom.name == raw["atom_name"]
                    and atom.resname == raw["resname"] and int(raw["resnum_raw"]) == residue.resid
                    and (raw["insertion_raw"] or " ") == (residue.icode or " "),
                    "FEATURE_MAPPING_UNPROVEN", "MDAnalysis PQR atom identity")
            seen.add(atom.id)
            locators.add(Locator(**raw["derived_locator"]))
        require(len(locators) == 1, "FEATURE_MAPPING_UNPROVEN", "MDAnalysis PQR grouped distinct residue identities")
        loc = next(iter(locators))
        records.append((loc.chain, loc.resnum, loc.insertion, POST_NAMES.get(residue.resname, residue.resname),
                        {"Charges": charge, "Radius of gyration": residue.atoms.radius_of_gyration()}))
    require(seen == by_serial.keys(), "FEATURE_MAPPING_UNPROVEN", "MDAnalysis PQR incomplete atom mapping")
    return bound_records(records, bindings, "MDAnalysis")


@measured("features.native_neighborhoods")
def _assemble(dssp, ca, *stages):
    import numpy as np
    import pandas as pd
    from MDAnalysis.lib.distances import distance_array
    rows = {}
    for loc, base in dssp.items():
        row = dict(base)
        for stage in stages:
            row.update(stage[loc])
        row.update(hydrophobicity(row["Amino acid"], row["Charges"]))
        rows[loc] = row
    return assemble_csv(rows, np.asarray([ca[loc] for loc in rows], dtype=float), distance_array, pd)


@measured("descriptors.native_compiled_total")
def _descriptors(work, adapter, template, runtime, seconds, *, whole_stage=False, checkpoint=None, context=None):
    if checkpoint is not None:
        seconds = checkpoint.seconds(min(seconds, M1_SECONDS["protdcal"]))
        whole_stage = True
    deadline = time.monotonic() + seconds if whole_stage else None
    folder = work / "descriptors"
    folder.mkdir(mode=0o700)
    plan = plan_m1_descriptors(adapter, template, workspace=str(folder), runtime=runtime)
    for name, data in plan.payloads.items():
        path = folder / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    for name in ("Outputs", "tmp"):
        (folder / name).mkdir()
    inputs = {}
    for name in ("parameter.cfg", "groups.gdm", "PropertyIndices.idx"):
        data = _read(Path(runtime["jar"]).parent / name)
        (folder / name).write_bytes(data)
        inputs[name.lower()] = digest(data)
    complete = folder / "Outputs/reference/complete_AA.txt"
    if checkpoint is not None:
        context = {**context, **inputs, "adapter": digest(canonical(adapter.receipt)), "project": digest(template),
                   "jar": runtime["jar_sha256"], "helper": runtime["helper_sha256"]}
        saved = checkpoint.load("protdcal", context, ("complete-aa.txt",))
        if saved is not None:
            raw = saved["complete-aa.txt"]
            result = collect_descriptors(plan, raw)
            complete.parent.mkdir()
            complete.write_bytes(raw)
            return result
    for i, argv in enumerate(plan.commands):
        stage = ("original_470", "wclq_29", "compiled_merge")[i]
        remaining = seconds if deadline is None else deadline - time.monotonic()
        require(remaining > 0, "TOOL_TIMEOUT", "ProtDCal stage budget exhausted")
        measured("descriptors.native_" + stage)(_tool)(argv, folder, f"descriptor-{i}", remaining)
    raw = _read(complete)
    result = collect_descriptors(plan, raw)
    require(deadline is None or time.monotonic() < deadline, "TOOL_TIMEOUT", "ProtDCal stage budget exhausted")
    if checkpoint is not None:
        checkpoint.commit("protdcal", context, {"complete-aa.txt": raw})
    return result


def extract_1rlw(source, *, work, profiles, template, descriptor_runtime, profile_bytes, checkpoint=None):
    """Fixed 1RLW worker entry point. Profiles must be admitted externally first."""
    return _extract(source, work=work, profiles=profiles, template=template, descriptor_runtime=descriptor_runtime,
                    profile_bytes=profile_bytes, fixture="1RLW", stem="1rlw_Prot.pdb", checkpoint=checkpoint)


def _extract(source, *, work, profiles, template, descriptor_runtime, profile_bytes, fixture, stem, checkpoint=None):
    scopes = ("preparation_charge", "preparation_structure", "features", "descriptors")
    require(set(profiles) == set(scopes), "SCIENTIFIC_EXECUTION_UNAPPROVED", "four exact stage profiles required")
    for scope in scopes:
        profile = profiles[scope]
        require(isinstance(profile, ExecutionProfile) and profile.data["scope"] == scope,
                "SCIENTIFIC_EXECUTION_UNAPPROVED", "incorrect extraction profile scope")
        profile.require_scientific_admission()
    if fixture in FULL_BENCHMARK_SHA:
        pin = FULL_BENCHMARK_SHA[fixture]
        expected_type = Frozen3Ruk if fixture == "3RUK" else PdbSource
        require(isinstance(source, expected_type) and source.sha256 == pin and all(
            p.data["inputs"].get("fixture", {}).get("sha256") == pin for p in profiles.values()),
            "SCIENTIFIC_EXECUTION_UNAPPROVED", "frozen fixture source and profiles required")
    else:
        require(fixture == "1RLW" and source.sha256 == CIF_1RLW_SHA256,
                "SCIENTIFIC_EXECUTION_UNAPPROVED", "frozen 1RLW source required")
    require(os.name == "posix" and os.geteuid() != 0,
            "SCIENTIFIC_EXECUTION_UNAPPROVED", "non-root worker required")
    require(sys.version_info[:3] == (3, 7, 16), "SCIENTIFIC_RUNTIME_MISMATCH", "legacy CPython 3.7.16 required")
    from importlib_metadata import version
    require(version("MoleculeKit") == "1.3.4" and version("biopython") == "1.78"
            and version("MDAnalysis") == "2.1.0" and version("ProDy") == "2.2.0"
            and version("pandas") == "1.1.3" and version("numpy") == "1.21.6"
            and version("scipy") == "1.7.3", "SCIENTIFIC_RUNTIME_MISMATCH", "legacy feature library versions")
    require(version("pdb2pqr").split("+")[0] == "3.5.2" and version("propka") == "3.4.0",
            "SCIENTIFIC_RUNTIME_MISMATCH", "legacy preparation package versions")
    from moleculekit.molecule import Molecule
    mkprep = import_module("moleculekit.tools.preparation")
    work = Path(work).absolute()
    _parents(work.parent)
    work.mkdir(mode=0o700, exist_ok=False)
    limits = {scope: profiles[scope].data["resources"] for scope in scopes}
    recorder = StageRecorder(evidence_class="scientific_unvalidated")
    try:
        with recorder.activate():
            molecule, before, ca, pre_bindings, selected = _filter(source, work, Molecule, fixture)
            stage_seconds = checkpoint.seconds if checkpoint is not None else lambda maximum: maximum
            charges = _charge(before, work, pre_bindings,
                              stage_seconds(limits["preparation_charge"]["wall_seconds"]), fixture=fixture)
            prepared, bindings = _structure(selected, molecule, work, before, pre_bindings, mkprep, fixture)
            seconds = limits["features"]["wall_seconds"]
            model, dssp = _dssp(work, bindings, stage_seconds(seconds))
            mesh_profile = limits["features"].get("msms_mesh_output")
            require(mesh_profile is None or (fixture == "O95425-2" and mesh_profile == MESH_PROFILE),
                    "SCIENTIFIC_EXECUTION_UNAPPROVED", "unapproved fixture-specific MSMS transport")
            hhblits_seconds = limits["features"].get("hhblits_wall_seconds", seconds)
            require(hhblits_seconds == seconds or (fixture in FULL_BENCHMARK_SHA and type(hhblits_seconds) is int
                    and hhblits_seconds == 14400 and limits["features"]["whole_job_memory_bytes"] == 12884901888),
                    "SCIENTIFIC_EXECUTION_UNAPPROVED", "unexpected HHblits deadline override")
            adapter = None
            context = None
            conservation = python_csv = None
            if checkpoint is not None:
                adapter = descriptor_adapter(prepared, bindings, source_mapping_evidence_ref="preparation_map", stem=stem)
                context = {"source": source.sha256, "prepared": digest(prepared),
                           "prepared-cif": digest(_read(work / "prepared.cif")),
                           "bindings": digest(canonical(adapter.receipt)),
                           "profile": digest(canonical({key: value.sha256 for key, value in profiles.items()})),
                           "features": digest(profile_bytes), "dssp": digest(_read(work / "dssp.stdout"))}
                conservation = _conservation(work, dssp, bindings, hhblits_seconds, limits["features"]["CPU_threads"],
                                             checkpoint=checkpoint, context=context, replay_only=True)

                def python_inputs():
                    return {**context, "precharge": digest(before), "pre-ca": digest(_read(work / "pre-ca.json")),
                            "pqr": digest(_read(work / "charges.pqr")),
                            "charge-map": digest(canonical(charges)),
                            "conservation": digest(_read(work / "conservation.json"))}

                if conservation is not None:
                    saved = checkpoint.load("python-features", python_inputs(), ("python-features.csv",))
                    if saved is not None:
                        python_csv = validate_python_features(saved["python-features.csv"], adapter, dssp, conservation)
            if python_csv is None:
                sasa = _sasa(work, bindings, stage_seconds(seconds))
                depth = _depth(model, work, bindings, stage_seconds(seconds), mesh_profile=mesh_profile)
                if conservation is None:
                    conservation = _conservation(work, dssp, bindings, hhblits_seconds, limits["features"]["CPU_threads"],
                                                 checkpoint=checkpoint, context=context)
                dynamics = _dynamics(work, bindings)
                charge_features = _charge_features(work, bindings, charges)
                sequence_features = {loc: {"Conservation score": value} for loc, value in conservation.items()}
                python_csv = _assemble(dssp, ca, sasa, depth, sequence_features, dynamics, charge_features)
                if checkpoint is not None:
                    validate_python_features(python_csv, adapter, dssp, conservation)
                    checkpoint.commit("python-features", python_inputs(), {"python-features.csv": python_csv})
            (work / "python-features.csv").write_bytes(python_csv)
            if adapter is None:
                adapter = descriptor_adapter(prepared, bindings, source_mapping_evidence_ref="preparation_map", stem=stem)
            if fixture == "3RUK":
                require(digest(adapter.data) == JAVA_3RUK_SHA256, "DESCRIPTOR_MAPPING_UNPROVEN", "3RUK archived Java input bytes")
            descriptors = _descriptors(work, adapter, template, descriptor_runtime, limits["descriptors"]["wall_seconds"],
                                       checkpoint=checkpoint, context=context)
            return prepare_m1(source=source.original, expected_source_sha256=source.sha256, prepared=prepared,
                              bindings=bindings, python_rows=read_python_features(python_csv, adapter), descriptors=descriptors,
                              profile_bytes=profile_bytes, pdb_id=fixture, adapter_stem=adapter.stem)
    finally:
        (work / "timings.json").write_bytes(canonical(recorder.receipt()))


def run_1rlw(source_packet, *, work, profiles, template, descriptor_runtime, profile_bytes, model, reference, zeroed,
             checkpoint=None):
    """Compute one complete attempt in an externally isolated scientific worker."""
    require_classifier_admission(profiles.get("classifier_compatibility"))
    source = load_1rlw_source_packet(source_packet)
    return _run(source, work=work, profiles=profiles, template=template, descriptor_runtime=descriptor_runtime,
                profile_bytes=profile_bytes, model=model, reference=reference, zeroed=zeroed, fixture="1RLW",
                checkpoint=checkpoint)


def run_8yoq(raw, *, work, profiles, template, descriptor_runtime, profile_bytes, model, reference, zeroed,
             checkpoint=None):
    """Run only the frozen archived 8YOQ PDB through the M1 reference stages."""
    require_classifier_admission(profiles.get("classifier_compatibility"))
    source = frozen_8yoq(raw)
    return _run(source, work=work, profiles=profiles, template=template, descriptor_runtime=descriptor_runtime,
                profile_bytes=profile_bytes, model=model, reference=reference, zeroed=zeroed, fixture="8YOQ",
                checkpoint=checkpoint)


def run_scaling_fixture(raw, *, fixture, work, profiles, template, descriptor_runtime, profile_bytes, model, reference, zeroed,
                        checkpoint=None):
    require_classifier_admission(profiles.get("classifier_compatibility"))
    sources = {"3RUK": Frozen3Ruk, "O95425-2": frozen_o95425, "2KS4": frozen_2ks4_reference}
    require(fixture in sources, "SOURCE_INVALID", "unsupported scaling fixture")
    source = sources[fixture](raw)
    return _run(source, work=work, profiles=profiles, template=template, descriptor_runtime=descriptor_runtime,
                profile_bytes=profile_bytes, model=model, reference=reference, zeroed=zeroed, fixture=fixture,
                checkpoint=checkpoint)


def _run(source, *, work, profiles, template, descriptor_runtime, profile_bytes, model, reference, zeroed, fixture,
         checkpoint=None):
    work = Path(work).absolute()
    _parents(work.parent)
    work.mkdir(mode=0o700, exist_ok=False)
    extraction_profiles = {key: profile for key, profile in profiles.items() if key != "classifier_compatibility"}
    stem = STEMS[fixture]
    prepared = _extract(source, work=work / "extraction", profiles=extraction_profiles, template=template,
                         descriptor_runtime=descriptor_runtime, profile_bytes=profile_bytes, fixture=fixture, stem=stem,
                         checkpoint=checkpoint)
    if fixture in ("1RLW", "8YOQ"):
        predict = predict_1rlw if fixture == "1RLW" else predict_8yoq
        prediction = predict(prepared, model=model, reference=reference, zeroed=zeroed, work=work / "prediction",
                             profile=profiles["classifier_compatibility"])
    else:
        prediction = predict_scaling_fixture(prepared, fixture=fixture, model=model, reference=reference, zeroed=zeroed,
                                             work=work / "prediction", profile=profiles["classifier_compatibility"])
    for name in ("adapter_map", "join", "unscaled"):
        write_scientific_receipt(work / "extraction" / (name + ".json"), prepared.artifacts[name],
                                 fixture_sha256=source.sha256)
    for name, data in prediction.items():
        if name == "scaling.json":
            write_scientific_receipt(work / "prediction" / name, data, fixture_sha256=source.sha256)
        else:
            (work / "prediction" / name).write_bytes(data)
    return prepared, prediction
