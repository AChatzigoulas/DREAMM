"""Read-only identity checks for an installed scientific runtime, without importing it."""
import hashlib
import os
import pkgutil
from pathlib import Path, PurePosixPath
import re
import stat

from dreamm_m1.capture import _json, _parents, _read
from dreamm_m1.features import REFERENCE_SHA256
from dreamm_m1.identity import MODEL_SHA256, canonical, digest, require, sha
from dreamm_m1.protdcal import HELPER_SHA256, JAR_SHA256
from dreamm_m1.reference import ZERO_FILE_SHA256
from dreamm_m1.rocky_database import FINGERPRINT
from dreamm_m1.wclq import PROJECT_SHA256, SCHEMA_SHA256, TABLE_SHA256

SCHEMA = "dreamm-standalone-runtime/1"
ROLES = frozenset(("science_python", "descriptor_python", "java", "hhblits", "dssp", "freesasa",
                   "msms", "pdb2pqr", "model", "reference", "zeroed", "project", "parameter",
                   "protdcal_jar", "groups", "indices", "helper", "kernel", "weights", "schema_json", "schema_tsv", "dependency_lock"))
VERSIONS = {"CPython": "3.7.16", "MoleculeKit": "1.3.4", "biopython": "1.78", "MDAnalysis": "2.1.0",
            "ProDy": "2.2.0", "pandas": "1.1.3", "numpy": "1.21.6", "scipy": "1.7.3",
            "pdb2pqr": "3.5.2", "propka": "3.4.0", "scikit-learn": "0.22.1", "joblib": "1.1.0",
            "mlxtend": "0.18.0", "lightgbm": "2.3.0", "HH-suite": "3.3.0", "Java_major": "8"}
PINNED = {"model": MODEL_SHA256, "reference": REFERENCE_SHA256, "zeroed": ZERO_FILE_SHA256,
          "protdcal_jar": JAR_SHA256, "helper": HELPER_SHA256, "project": PROJECT_SHA256,
          "weights": TABLE_SHA256,
          "schema_json": "c0ff9f03d726766ad665256b907c8337abf141bf226e367537fe13dd55e88cb1",
          "schema_tsv": "3e58e613135995692a910aefca5abdbb3d4f9f5ebce042bc9ecd795cfab5fdbd",
          "parameter": "e3feb1f8e970dd5850d46d2ae4b42d6bd486cceadda07fe54ad1684f9da91f2a",
          "groups": "39c53839f8b888702c12d94b7a914774a5584551574f314a35ba4d034b904e97",
          "indices": "8cf2336667dcf29f8de8297f0ae7dd2e82cdb03e3a1c608f2935cf4ab0c09f46",
          "kernel": digest(pkgutil.get_data("dreamm_m1", "wclq.py")),
          "hhblits": "76ef2b7a7bdb161c9604181aabfa2820ec06f8c27cdcdf66404f9f229f9df7e9"}
MAX_FILES, MAX_TOTAL, MAX_FILE = 50000, 8 * 1024**3, 512 * 1024**2


def relative_path(value):
    require(isinstance(value, str) and 0 < len(value) <= 256 and "\\" not in value
            and re.fullmatch(r"[A-Za-z0-9_./+ ()-]+", value) and not PurePosixPath(value).is_absolute()
            and all(part not in ("", ".", "..") for part in value.split("/")),
            "RUNTIME_INVALID", "canonical relative runtime path required")
    return value


class RuntimeBundle:
    schema = SCHEMA
    versions = VERSIONS
    roles = ROLES
    pinned = PINNED

    def __init__(self, raw, *, expected_sha256):
        require(isinstance(raw, bytes) and 0 < len(raw) <= 8 * 1024**2
                and digest(raw) == sha(expected_sha256), "RUNTIME_INVALID", "runtime manifest hash or size")
        data = _json(raw)
        require(isinstance(data, dict) and set(data) == {"schema", "runtime_id", "versions", "roles", "files",
                "links", "database_fingerprint", "hhblits_threads"} and data["schema"] == self.schema
                and isinstance(data["runtime_id"], str) and re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,63}", data["runtime_id"]),
                "RUNTIME_INVALID", "runtime manifest schema")
        require(data["versions"] == self.versions and type(data["hhblits_threads"]) is int
                and data["hhblits_threads"] == 15 and data["database_fingerprint"] == FINGERPRINT,
                "RUNTIME_INVALID", "scientific compatibility versions or database changed")
        require(isinstance(data["files"], dict) and 0 < len(data["files"]) <= MAX_FILES
                and isinstance(data["links"], dict) and len(data["links"]) <= MAX_FILES
                and not data["files"].keys() & data["links"].keys()
                and isinstance(data["roles"], dict) and set(data["roles"]) == self.roles,
                "RUNTIME_INVALID", "runtime roles and file inventory")
        total = 0
        for name, row in data["files"].items():
            relative_path(name)
            require(isinstance(row, dict) and set(row) == {"bytes", "sha256"}
                    and type(row["bytes"]) is int and 0 <= row["bytes"] <= MAX_FILE,
                    "RUNTIME_INVALID", "runtime member size")
            sha(row["sha256"])
            total += row["bytes"]
        require(total <= MAX_TOTAL, "RUNTIME_INVALID", "runtime aggregate size")
        for name, target in data["links"].items():
            relative_path(name)
            relative_path(target)
            require(target in data["files"], "RUNTIME_INVALID", "alias target must be a listed regular file")
        for role, name in data["roles"].items():
            relative_path(name)
            require(name in data["files"], "RUNTIME_INVALID", "execution roles require regular files")
            require(data["files"][name]["bytes"] > 0, "RUNTIME_INVALID", "empty runtime role")
            if role in self.pinned:
                require(data["files"][name]["sha256"] == self.pinned[role],
                        "RUNTIME_INVALID", "protected scientific asset differs: " + role)
        require(data["roles"]["science_python"] == data["roles"]["descriptor_python"],
                "RUNTIME_INVALID", "the validated descriptor interpreter must be preserved")
        parent = PurePosixPath(data["roles"]["protdcal_jar"]).parent
        for role, name in (("parameter", "parameter.cfg"), ("groups", "groups.gdm"), ("indices", "PropertyIndices.idx")):
            require(data["roles"][role] == str(parent / name), "RUNTIME_INVALID", "ProtDCal companion layout differs")
        self._raw = canonical(data)
        self.sha256 = expected_sha256

    @property
    def data(self):
        return _json(self._raw)

    def verify(self, root):
        """Hash the complete installed closure. This does not qualify execution or containment."""
        root = Path(root).absolute()
        _parents(root)
        data = self.data
        expected = set(data["files"]) | set(data["links"])
        seen = set()
        errors = []
        for directory, folders, names in os.walk(root, followlinks=False, onerror=errors.append):
            for name in folders:
                path = Path(directory) / name
                require(stat.S_ISDIR(path.lstat().st_mode) and not path.is_symlink()
                        and not getattr(path.lstat(), "st_file_attributes", 0) & 0x400,
                        "RUNTIME_INVALID", "linked or special runtime directory")
            seen.update((Path(directory) / name).relative_to(root).as_posix() for name in names)
            require(len(seen) <= MAX_FILES * 2, "RUNTIME_INVALID", "runtime inventory exceeds bound")
        require(not errors, "RUNTIME_INVALID", "unreadable runtime directory")
        require(seen == expected, "RUNTIME_INVALID", "installed runtime has missing or unlisted files")
        for name, row in data["files"].items():
            path = root / name
            _parents(path.parent)
            before = path.lstat()
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == row["bytes"]
                    and not getattr(before, "st_file_attributes", 0) & 0x400,
                    "RUNTIME_INVALID", "runtime file identity or size differs")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            checksum = hashlib.sha256()
            with os.fdopen(os.open(path, flags), "rb") as stream:
                opened = os.fstat(stream.fileno())
                require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                        "RUNTIME_INVALID", "runtime file replaced")
                for chunk in iter(lambda: stream.read(1024**2), b""):
                    checksum.update(chunk)
                after = os.fstat(stream.fileno())
            require(checksum.hexdigest() == row["sha256"]
                    and (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns),
                    "RUNTIME_INVALID", "runtime bytes changed")
        for name, target in data["links"].items():
            path = root / name
            # The manifest uses root-relative targets. The installed link is relative to its parent.
            wanted = os.path.relpath(root / target, path.parent)
            require(path.is_symlink() and os.readlink(path) == wanted,
                    "RUNTIME_INVALID", "runtime alias differs")
        schema = _json(_read(root / data["roles"]["schema_json"], 128 * 1024))
        require(isinstance(schema, list) and len(schema) == 499
                and digest(canonical(schema)) == SCHEMA_SHA256,
                "RUNTIME_INVALID", "canonical descriptor ordering differs")
        require(_read(root / data["roles"]["schema_tsv"], 128 * 1024)
                == ("\t".join(["AA"] + schema) + "\n").encode("ascii"),
                "RUNTIME_INVALID", "compiled descriptor schema differs")
        return {"schema": "dreamm-runtime-integrity/1", "runtime_id": data["runtime_id"],
                "manifest_sha256": self.sha256, "verified_files": len(data["files"]),
                "verified_aliases": len(data["links"]), "bytes": sum(r["bytes"] for r in data["files"].values()),
                "integrity_verified": True, "native_execution_qualified": False,
                "remaining_gate": "independent_runtime_provenance_and_supervisor_admission",
                "executables_started": False, "model_loaded": False}


def doctor(args):
    bundle = runtime_bundle(_read(Path(args.manifest).absolute()), expected_sha256=args.sha256)
    return bundle.verify(args.root)


def runtime_bundle(raw, *, expected_sha256):
    """Explicit schema dispatch; an unknown profile never falls back to legacy."""
    require(isinstance(raw,bytes) and 0<len(raw)<=8*1024**2 and digest(raw)==sha(expected_sha256),
            'RUNTIME_INVALID','runtime manifest hash or size')
    data = _json(raw)
    require(isinstance(data,dict),'RUNTIME_INVALID','runtime manifest must be an object')
    schema = data.get('schema')
    if schema == SCHEMA:
        return RuntimeBundle(raw, expected_sha256=expected_sha256)
    if schema == 'dreamm-modern-runtime/1':
        from .modern_runtime import ModernRuntimeBundle
        return ModernRuntimeBundle(raw, expected_sha256=expected_sha256)
    require(False, 'RUNTIME_INVALID', 'unsupported scientific runtime schema')


def requirements(args):
    if getattr(args,'runtime_profile','reference') == 'modern':
        from .modern_runtime import requirements as modern_requirements
        return modern_requirements()
    return {"schema": SCHEMA, "versions": VERSIONS, "required_roles": sorted(ROLES),
            "protected_sha256": PINNED, "database_fingerprint": FINGERPRINT,
            "hhblits_threads": 15, "validated_visible_cpus": 16, "installation_or_execution_performed": False,
            "qualification": "independent_provenance_and_existing_supervisor_required"}
