import argparse
import ast
import copy
import hashlib
import json
import os
import pkgutil
from pathlib import Path
import sys
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dreamm import cli, runtime, worker
from dreamm_m1.identity import canonical, digest, M1Error
from dreamm_m1.execution_profiles import ExecutionProfile
from test_cli import pdb_bytes


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.roles, self.content = {}, {}
        for role in sorted(runtime.ROLES - {"descriptor_python"}):
            name = "tools/" + role
            if role in ("protdcal_jar", "parameter", "groups", "indices"):
                name = "protdcal/" + {"protdcal_jar": "ProtDCal.jar", "parameter": "parameter.cfg",
                                         "groups": "groups.gdm", "indices": "PropertyIndices.idx"}[role]
            self.roles[role] = name
            self.content[name] = ("fixed synthetic " + role + "\n").encode()
        self.roles["descriptor_python"] = self.roles["science_python"]
        schema = pkgutil.get_data("dreamm", "data/protdcal-schema.json")
        self.content[self.roles["schema_json"]] = schema
        self.content[self.roles["schema_tsv"]] = ("\t".join(["AA"] + json.loads(schema)) + "\n").encode("ascii")
        for name, raw in self.content.items():
            path = self.root / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(raw)
        self.data = {"schema": runtime.SCHEMA, "runtime_id": "synthetic-closure-v1",
                     "versions": dict(runtime.VERSIONS), "roles": self.roles,
                     "files": {name: {"bytes": len(raw), "sha256": digest(raw)} for name, raw in self.content.items()},
                     "links": {}, "database_fingerprint": runtime.FINGERPRINT, "hhblits_threads": 15}

    def bundle(self, data=None):
        data = self.data if data is None else data
        # This fixture exercises integrity mechanics only. It cannot qualify a scientific runtime.
        with patch.dict(runtime.PINNED, {}, clear=True):
            raw = canonical(data)
            return runtime.RuntimeBundle(raw, expected_sha256=digest(raw))

    def test_complete_synthetic_closure_is_hashed_without_execution(self):
        report = self.bundle().verify(self.root)
        self.assertEqual(report["verified_files"], len(self.content))
        self.assertTrue(report["integrity_verified"])
        self.assertFalse(report["native_execution_qualified"])
        self.assertFalse(report["executables_started"])
        self.assertFalse(report["model_loaded"])

    def test_real_protected_pins_reject_the_synthetic_model(self):
        with self.assertRaisesRegex(M1Error, "protected scientific asset differs"):
            runtime.RuntimeBundle(canonical(self.data), expected_sha256=digest(canonical(self.data)))

    def test_descriptor_file_hash_and_canonical_schema_hash_are_distinct(self):
        raw = pkgutil.get_data("dreamm", "data/protdcal-schema.json")
        self.assertEqual(digest(raw), runtime.PINNED["schema_json"])
        self.assertEqual(digest(canonical(json.loads(raw))), runtime.SCHEMA_SHA256)
        self.assertNotEqual(digest(raw), runtime.SCHEMA_SHA256)
        tsv = ("\t".join(["AA"] + json.loads(raw)) + "\n").encode("ascii")
        self.assertEqual(digest(tsv), runtime.PINNED["schema_tsv"])

    def test_manifest_hash_and_compatibility_settings_are_not_optional(self):
        with self.assertRaises(M1Error):
            runtime.RuntimeBundle(canonical(self.data), expected_sha256="0" * 64)
        for section, key, value in (("versions", "pdb2pqr", "3.8.0"), ("versions", "CPython", "3.12.13"),
                                     (None, "hhblits_threads", 8), (None, "database_fingerprint", "0" * 64)):
            altered = copy.deepcopy(self.data)
            (altered[section] if section else altered)[key] = value
            with self.subTest(key=key), self.assertRaises(M1Error):
                self.bundle(altered)

    def test_duplicate_keys_traversal_wrong_roles_and_config_layout_are_rejected(self):
        with self.assertRaises(M1Error):
            runtime.RuntimeBundle(b'{"schema":0,"schema":1}', expected_sha256=digest(b'{"schema":0,"schema":1}'))
        for mutation in (lambda d: d["files"].update({"../escape": {"bytes": 1, "sha256": "1" * 64}}),
                         lambda d: d["roles"].pop("java"),
                         lambda d: d["roles"].update(parameter=d["roles"]["model"]),
                         lambda d: d["files"][d["roles"]["java"]].update(bytes=True),
                         lambda d: d["files"][d["roles"]["java"]].update(bytes=runtime.MAX_FILE + 1)):
            altered = copy.deepcopy(self.data)
            mutation(altered)
            with self.assertRaises(M1Error):
                self.bundle(altered)

    def test_modified_missing_and_unlisted_members_fail_closed(self):
        bundle = self.bundle()
        path = self.root / self.roles["model"]
        original = path.read_bytes()
        path.write_bytes(original[:-1] + b"x")
        with self.assertRaisesRegex(M1Error, "runtime bytes changed"):
            bundle.verify(self.root)
        path.write_bytes(original)
        extra = self.root / "unlisted"
        extra.write_bytes(b"unexpected")
        with self.assertRaises(M1Error):
            bundle.verify(self.root)
        extra.unlink()
        path.unlink()
        with self.assertRaises(M1Error):
            bundle.verify(self.root)

    def test_empty_dependency_files_are_preserved(self):
        self.data["files"]["empty-init.py"] = {"bytes": 0, "sha256": digest(b"")}
        (self.root / "empty-init.py").write_bytes(b"")
        self.assertTrue(self.bundle().verify(self.root)["integrity_verified"])

    @unittest.skipUnless(os.name == "posix", "native link checks require Linux")
    def test_internal_aliases_are_checked_and_external_links_are_rejected(self):
        self.data["links"]["python-alias"] = self.roles["science_python"]
        alias = self.root / "python-alias"
        alias.symlink_to(self.roles["science_python"])
        self.assertEqual(self.bundle().verify(self.root)["verified_aliases"], 1)
        alias.unlink()
        alias.symlink_to("/etc/passwd")
        with self.assertRaises(M1Error):
            self.bundle().verify(self.root)

    @unittest.skipUnless(os.name == "posix", "native link checks require Linux")
    def test_runtime_hardlinks_are_rejected(self):
        path = self.root / self.roles["model"]
        os.link(path, self.root / "model-alias")
        self.data["files"]["model-alias"] = self.data["files"][self.roles["model"]]
        with self.assertRaises(M1Error):
            self.bundle().verify(self.root)

    def test_callers_cannot_poison_validated_manifest_data(self):
        bundle = self.bundle()
        bundle.data["versions"]["CPython"] = "different"
        self.assertEqual(bundle.data["versions"]["CPython"], "3.7.16")


class NativeAdapterTests(unittest.TestCase):
    def test_bridge_source_retains_python37_syntax_compatibility(self):
        for name in ("cli.py", "runtime.py", "coordinator.py", "jobs.py", "worker.py", "local_entry.py"):
            ast.parse((ROOT / "src/dreamm" / name).read_text(encoding="utf-8"), feature_version=(3, 7))
    def test_reference_fixture_selection_and_explicit_chain_identity(self):
        request = {"mode": "M1", "source": {"sha256": worker.CIF_3RUK_SHA256}, "pdb_id": "3RUK",
                   "chain_selection": {"namespace": "auth_asym_id", "ids": ["A"]}}
        self.assertEqual(worker.reference_fixture(request), "3RUK")
        for chains in (None, {"namespace": "auth_asym_id", "ids": ["B"]}):
            with self.assertRaises(M1Error):
                worker.reference_fixture({**request, "chain_selection": chains})
        for mode in ("M3", "M4", "M5"):
            with self.assertRaises(M1Error):
                worker.reference_fixture({**request, "mode": mode})

    def test_tool_relocation_is_scoped_and_restored_on_errors(self):
        names = ("PDB2PQR", "DSSP_TOOL", "SASA_TOOL", "MSMS_TOOL", "HHBLITS", "DATABASE")
        native = SimpleNamespace(**{name: "original-" + name for name in names})
        roles = {role: Path("/runtime") / role for role in ("pdb2pqr", "dssp", "freesasa", "msms", "hhblits")}
        original = vars(native).copy()
        with self.assertRaisesRegex(RuntimeError, "native failure"):
            with worker.bind_tools(native, roles, "/database/reference"):
                self.assertEqual(native.HHBLITS, str(roles["hhblits"]))
                self.assertEqual(native.DATABASE, "/database/reference")
                with self.assertRaises(M1Error):
                    with worker.bind_tools(native, roles, "/another-database"):
                        self.fail("a second invocation must not be admitted")
                raise RuntimeError("native failure")
        self.assertEqual(vars(native), original)
        with worker.bind_tools(native, roles, "/database/reference"):
            pass
        self.assertEqual(vars(native), original)

    def test_a_bundle_cannot_grant_itself_classifier_admission(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "input.pdb"
            source.write_bytes(pdb_bytes())
            raw_sha = digest(pdb_bytes())
            target = Path(temporary) / "request"
            cli.prepare(argparse.Namespace(mode="M2", input=source, sha256=raw_sha, format="pdb", output=target,
                                           pdb_id=None, chains=None, additional=None))
            profiles = {}
            for scope in worker.SCOPES:
                data = {"schema": "dreamm-execution-profile/1", "version": 1, "profile_id": "test-" + scope,
                        "scope": scope, "modes": ["M2"], "python": {}, "tool": {}, "dependencies": {},
                        "parameters": {"model_deserialization_authorized": scope == "classifier_compatibility"},
                        "inputs": {"fixture": {"schema": "test", "sha256": raw_sha, "max_bytes": 1024}},
                        "outputs": {"result": {"schema": "test", "sha256": None, "max_bytes": 1024}},
                        "resources": {}, "containment": {}}
                profiles[scope] = ExecutionProfile(canonical(data))
            bundle = MagicMock()
            with patch.dict(worker.FIXTURES, {raw_sha: "8YOQ"}), self.assertRaisesRegex(M1Error, "SCIENTIFIC_EXECUTION_UNAPPROVED"):
                worker.run_reference(target, bundle=bundle, runtime_root="/runtime", profiles=profiles,
                                     database_prefix="/database", database_verification={}, work="/work", profile_bytes=b"")
            bundle.verify.assert_not_called()
            self.assertNotIn("sklearn", sys.modules)
            self.assertNotIn("joblib", sys.modules)

    @unittest.skipUnless(sys.platform == "linux", "native adapter path checks require Linux")
    def test_mocked_native_dispatch_forwards_verified_assets_and_restores_paths(self):
        """Test API wiring with a stub native call, never execute a scientific tool."""
        from dreamm_m1 import native_m1
        with tempfile.TemporaryDirectory(prefix="dreamm-native-adapter-") as temporary:
            root = Path(temporary)
            source = root / "input.pdb"
            source.write_bytes(pdb_bytes())
            source_sha = digest(pdb_bytes())
            request = root / "request"
            cli.prepare(argparse.Namespace(mode="M2", input=source, sha256=source_sha, format="pdb", output=request,
                                           pdb_id=None, chains=None, additional=None))
            runtime_root, database_root = root / "runtime", root / "database"
            runtime_root.mkdir()
            database_root.mkdir()
            roles, metadata = {}, {}
            for role in runtime.ROLES:
                path = runtime_root / role
                path.write_bytes(("synthetic " + role).encode())
                roles[role] = role
            roles["descriptor_python"] = roles["science_python"]
            for role, original in (("model", "final_classifier.sav"),
                                   ("reference", "dataset_res_depth<2.5_selected.csv"), ("zeroed", "unimport_feats.csv")):
                raw = subprocess.run(["git", "-C", str(ROOT), "-c", "safe.directory=" + str(ROOT),
                                      "show", "HEAD:data/" + original], capture_output=True, timeout=30, check=True).stdout
                (runtime_root / role).write_bytes(raw)
            for path in runtime_root.iterdir():
                metadata[path.name] = {"sha256": digest(path.read_bytes()), "bytes": path.stat().st_size}
            tool_roles = {"preparation_charge": "pdb2pqr", "preparation_structure": "science_python", "features": "hhblits",
                          "descriptors": "protdcal_jar", "classifier_compatibility": "science_python"}
            profiles = {}
            for scope, role in tool_roles.items():
                profiles[scope] = ExecutionProfile(canonical({"schema": "dreamm-execution-profile/1", "version": 1,
                    "profile_id": "synthetic-adapter-" + scope, "scope": scope, "modes": ["M2"],
                    "python": {"interpreter_sha256": metadata["science_python"]["sha256"]},
                    "tool": {"executable_sha256": metadata[role]["sha256"]},
                    "dependencies": {"lock_sha256": metadata["dependency_lock"]["sha256"]},
                    "parameters": {"model_deserialization_authorized": scope == "classifier_compatibility",
                                   "recipe_sha256": worker.recipe_sha256()},
                    "inputs": {"fixture": {"schema": "test", "sha256": source_sha, "max_bytes": 1024}},
                    "outputs": {"receipt": {"schema": "test", "sha256": None, "max_bytes": 1024}},
                    "resources": {"CPU_threads": 15}, "containment": {}}))
            bundle = MagicMock()
            bundle.data = {"roles": roles, "files": metadata}
            names = ("PDB2PQR", "DSSP_TOOL", "SASA_TOOL", "MSMS_TOOL", "HHBLITS", "DATABASE")
            before = {name: getattr(native_m1, name) for name in names}
            device = database_root.stat().st_dev
            verification = {"manifest_sha256": runtime.FINGERPRINT, "mount": str(database_root),
                            "device": "{}:{}".format(os.major(device), os.minor(device))}
            def native_call(raw, **kwargs):
                self.assertEqual(raw, pdb_bytes())
                self.assertEqual(native_m1.HHBLITS, str(runtime_root / "hhblits"))
                self.assertEqual(kwargs["descriptor_runtime"]["python"], str(runtime_root / "science_python"))
                self.assertEqual(digest(kwargs["model"]), runtime.MODEL_SHA256)
                return "prepared-stub", "prediction-stub"
            with patch.dict(worker.FIXTURES, {source_sha: "8YOQ"}), \
                    patch.object(ExecutionProfile, "require_scientific_admission"), \
                    patch("dreamm.worker.os.cpu_count", return_value=16), \
                    patch("dreamm.worker.os.statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)), \
                    patch("dreamm.worker.sys", SimpleNamespace(version_info=(3, 7, 16), executable=str(runtime_root / "science_python"))), \
                    patch.object(native_m1, "run_8yoq", side_effect=native_call) as dispatch:
                result = worker.run_reference(request, bundle=bundle, runtime_root=runtime_root, profiles=profiles,
                    database_prefix=database_root / worker.PREFIX, database_verification=verification,
                    work=root / "work", profile_bytes=cli.compatibility())
                self.assertEqual(result, ("prepared-stub", "prediction-stub"))
                dispatch.assert_called_once()
            self.assertEqual(before, {name: getattr(native_m1, name) for name in names})
            self.assertNotIn("sklearn", sys.modules)
