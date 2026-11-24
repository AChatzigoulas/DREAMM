import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from dreamm import cli
from dreamm_m1.identity import M1Error
import build


def pdb_bytes():
    row = "ATOM      1  CA AALA B  41A     2.000   1.000   3.000  0.50 10.00           C\n"
    # Keep nonconsecutive source model IDs and alternate conformer evidence.
    return ("MODEL        3\n" + row + "ENDMDL\nMODEL        8\n" + row + "ENDMDL\nEND\n").encode()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "public.pdb"
        self.raw = pdb_bytes()
        self.source.write_bytes(self.raw)
        self.sha = hashlib.sha256(self.raw).hexdigest()

    def args(self, mode="M4", **changes):
        values = dict(mode=mode, input=self.source, sha256=self.sha,
                      format="pdb", output=self.root / "request", pdb_id=None,
                      chains=None, additional=None)
        if mode in cli.ID_MODES:
            values.update(format="mmcif", pdb_id="1rlw")
        if mode == "M5":
            values["additional"] = 2
        values.update(changes)
        return argparse.Namespace(**values)

    def test_all_five_requests_preserve_exact_bytes_and_defer_science(self):
        for mode in ("M1", "M2", "M3", "M4", "M5"):
            with self.subTest(mode=mode):
                args = self.args(mode, output=self.root / mode)
                receipt = cli.prepare(args)
                self.assertFalse(receipt["scientific_prediction_performed"])
                self.assertEqual(receipt["source_parse_status"], "pending")
                self.assertEqual((args.output / receipt["source"]["path"]).read_bytes(), self.raw)
                self.assertTrue(cli.verify_request(argparse.Namespace(directory=args.output))["verified"])
                self.assertEqual(receipt["analysis_frame_count"], 3 if mode == "M5" else
                                 (1 if mode in ("M1", "M2") else None))

    def test_id_chain_identifiers_are_preserved_without_renumbering(self):
        receipt = cli.prepare(self.args("M3", chains=["A_1", "B-2"]))
        self.assertEqual(receipt["chain_selection"], {"namespace": "auth_asym_id", "ids": ["A_1", "B-2"]})

    def test_invalid_subcases_do_not_create_output(self):
        cases = [("M1", {"pdb_id": "../x"}), ("M1", {"chains": ["A", "A"]}),
                 ("M1", {"chains": ["A\nB"]}), ("M1", {"additional": 2}),
                 ("M2", {"chains": ["A"]}), ("M4", {"pdb_id": "1RLW"}),
                 ("M4", {"format": "mmcif"}), ("M5", {"additional": 0}),
                 ("M5", {"additional": 51}), ("M5", {"additional": True})]
        for mode, changes in cases:
            with self.subTest(mode=mode, changes=changes), self.assertRaises(M1Error):
                cli.prepare(self.args(mode, **changes))
            self.assertFalse((self.root / "request").exists())

    def test_hash_mismatch_does_not_create_output(self):
        with self.assertRaisesRegex(M1Error, "SHA-256 differs"):
            cli.prepare(self.args(sha256="0" * 64))
        self.assertFalse((self.root / "request").exists())

    def test_oversized_request_is_rejected_before_writing(self):
        chains = [str(index) + "A" * 250 for index in range(100)]
        with self.assertRaisesRegex(M1Error, "request manifest exceeds"):
            cli.prepare(self.args("M3", chains=chains))
        self.assertFalse((self.root / "request").exists())

    def test_existing_output_is_never_overwritten(self):
        args = self.args()
        cli.prepare(args)
        before = (args.output / "request.json").read_bytes()
        with self.assertRaises(FileExistsError):
            cli.prepare(args)
        self.assertEqual((args.output / "request.json").read_bytes(), before)

    def test_interrupted_publication_cannot_be_verified(self):
        args = self.args()
        with patch("dreamm.cli.os.rename", side_effect=OSError("interrupted")), self.assertRaises(OSError):
            cli.prepare(args)
        self.assertTrue((args.output / ".request.pending").exists())
        self.assertFalse((args.output / "request.json").exists())
        with self.assertRaises(OSError):
            cli.verify_request(argparse.Namespace(directory=args.output))

    def test_modified_source_and_manifest_are_rejected(self):
        args = self.args()
        cli.prepare(args)
        source = args.output / "source.pdb"
        source.write_bytes(self.raw + b"REMARK changed\n")
        with self.assertRaises(M1Error):
            cli.verify_request(argparse.Namespace(directory=args.output))
        source.write_bytes(self.raw)
        path = args.output / "request.json"
        receipt = json.loads(path.read_bytes())
        receipt["reference_frame_index"] = 1
        path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaises(M1Error):
            cli.verify_request(argparse.Namespace(directory=args.output))

    def test_path_traversal_duplicate_keys_and_extra_files_are_rejected(self):
        args = self.args()
        original = cli.prepare(args)
        path = args.output / "request.json"
        for payload in ({**original, "source": {**original["source"], "path": "../public.pdb"}},
                        {**original, "chain_selection": {"namespace": "row_index", "ids": ["A"]}}):
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(M1Error):
                cli.verify_request(argparse.Namespace(directory=args.output))
        path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
        with self.assertRaises(M1Error):
            cli.verify_request(argparse.Namespace(directory=args.output))
        path.write_text(json.dumps(original), encoding="utf-8")
        (args.output / "uncommitted").write_bytes(b"partial")
        with self.assertRaises(M1Error):
            cli.verify_request(argparse.Namespace(directory=args.output))

    def test_pdb_inspection_preserves_models_insertions_and_altlocs(self):
        report = cli.inspect_source(self.args())
        self.assertEqual([m["source_model_id"] for m in report["source_models"]], ["3", "8"])
        self.assertEqual([m["atoms"] for m in report["source_models"]], [1, 1])
        self.assertEqual(report["source_models"][0]["residues_per_author_chain"], {"B": 1})
        from dreamm_m1.pdb_input import read_pdb_source
        source = read_pdb_source(self.raw, expected_sha256=self.sha)
        self.assertEqual(source.atoms[0].locator.insertion, "A")
        self.assertEqual(source.atoms[0].altloc_raw, "A")

    def test_mmcif_admission_never_falls_back_to_a_partial_parser(self):
        with patch("dreamm_m1.mmcif_input.verified_gemmi",
                   side_effect=M1Error("PARSER_ENVIRONMENT_UNAVAILABLE", "missing sandbox")):
            with self.assertRaisesRegex(M1Error, "PARSER_ENVIRONMENT_UNAVAILABLE"):
                cli.inspect_source(self.args("M1"))

    def test_size_bound_and_hardlinked_input(self):
        self.source.write_bytes(b"x" * (cli.SOURCE_LIMIT + 1))
        with self.assertRaises(M1Error):
            cli.prepare(self.args())
        self.source.write_bytes(self.raw)
        linked = self.root / "alias.pdb"
        try:
            os.link(self.source, linked)
        except OSError:
            if os.name == 'nt':self.skipTest('current Windows filesystem disallows hardlink creation; Linux gate remains mandatory')
            raise
        with self.assertRaises(M1Error):
            cli.prepare(self.args())

    def test_reference_rejection_never_loads_a_model(self):
        args = argparse.Namespace(model=self.source, reference=self.source, zeroed=self.source)
        with self.assertRaisesRegex(M1Error, "MODEL_IDENTITY_INVALID"):
            cli.verify_reference(args)
        profile = cli.profiles(None)
        self.assertEqual((profile["ordered_features"], profile["zero_filled_columns"]), (727, 167))
        self.assertNotIn("sklearn", sys.modules)
        self.assertNotIn("joblib", sys.modules)
        self.assertNotIn("gemmi", sys.modules)

    def test_actual_public_model_and_reference_bind_to_the_exported_core(self):
        if not (ROOT/'.git').exists():
            self.skipTest("the source archive deliberately omits the original model and Git objects")
        assets = {}
        for role, name in (("model", "final_classifier.sav"),
                           ("reference", "dataset_res_depth<2.5_selected.csv"),
                           ("zeroed", "unimport_feats.csv")):
            raw = subprocess.run(["git", "-C", str(ROOT), "-c", "safe.directory=" + str(ROOT),
                                  "show", "HEAD:data/" + name],
                                  capture_output=True, check=True, timeout=30).stdout
            path = self.root / (role + ".data")
            path.write_bytes(raw)
            assets[role] = path
        receipt = cli.verify_reference(argparse.Namespace(**assets))
        self.assertEqual(receipt["reference_rows"], 3010)
        self.assertEqual(receipt["ordered_feature_count"], 727)
        self.assertEqual(receipt["zero_filled_count"], 167)
        self.assertFalse(receipt["model_loaded"])
        self.assertFalse(receipt["scaler_fit_executed"])

    def test_cli_errors_are_explicit_and_machine_readable(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = cli.main(["inspect", "--input", str(self.source), "--format", "pdb", "--sha256", "0" * 64])
        self.assertEqual(status, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(json.loads(err.getvalue())["error"], "SOURCE_INVALID")

    def test_installed_cli_help_and_profiles_describe_the_current_workflows(self):
        parser = cli.parser()
        help_text = parser.format_help()
        self.assertIn("dreamm run -i 1RLW -c A", help_text)
        ordinary, advanced = help_text.split("Advanced commands")
        self.assertIn("run", ordinary)
        self.assertIn("profiles", ordinary)
        expert, server = advanced.split("Server integration")
        self.assertIn("install-runtime", expert)
        self.assertNotIn("install-runtime", ordinary)
        self.assertIn("submit", server)
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as stop:
            parser.parse_args(["run", "--help"])
        self.assertEqual(stop.exception.code, 0)
        self.assertIn("Basic or full installation", out.getvalue())
        self.assertIn("Full installation adds pocket analysis", out.getvalue())
        mode = parser.parse_args(["run", "--mode", "M5", "-i", "1RLW", "--additional", "1"])
        profile = cli.profiles(None)
        self.assertEqual(mode.ensemble_profile, profile["ensemble"]["default_profile"])
        self.assertEqual(profile["ensemble"]["additional_conformations"], [1, 50])
        self.assertTrue(profile["ensemble"]["reference_included"])
        self.assertNotIn("chimera", profile["ensemble"])

    def test_posix_request_modes(self):
        if os.name != "posix":
            self.skipTest("POSIX file modes require the Linux test run")
        args = self.args()
        cli.prepare(args)
        self.assertEqual(args.output.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in args.output.iterdir()))

    def test_reproducible_zipapp_runs_from_an_unrelated_directory(self):
        first, second = self.root / "first.pyz", self.root / "second.pyz"
        one, two = build.build(first), build.build(second)
        self.assertEqual(one["sha256"], two["sha256"])
        with zipfile.ZipFile(first) as archive:
            self.assertFalse(any(name.endswith((".sav", ".pkl", ".jar", ".exe")) for name in archive.namelist()))
            self.assertFalse(any(name.startswith(("assessment/", "web/", "data/")) for name in archive.namelist()))
        process = subprocess.run([sys.executable, "-I", "-S", "-B", str(first), "profiles"],
                                 cwd=self.root, capture_output=True, timeout=30, check=True)
        self.assertEqual(json.loads(process.stdout)["ordered_features"], 727)

    def test_builder_rejects_an_altered_scientific_core(self):
        self.assertIn("dreamm_m1/identity.py", build.members())
        original = build.regular
        def changed(path):
            data = original(path)
            return data + b"\n# changed\n" if path.name == "identity.py" else data
        with patch("build.regular", side_effect=changed), self.assertRaisesRegex(ValueError, "scientific source differs"):
            build.members()


if __name__ == "__main__":
    unittest.main(verbosity=2)
