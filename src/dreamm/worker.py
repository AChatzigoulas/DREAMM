"""M1 adapter called only by an already admitted isolated scientific worker.

The coordinator supplies approved stage profiles and owns launch, deadlines,
whole-tree termination, attempt fencing and publication. This module never
registers a profile, launches a supervisor or grants itself execution authority.
"""
from contextlib import contextmanager
import os
import pkgutil
from pathlib import Path
from threading import Lock
import sys

from dreamm_m1.capture import _read
from dreamm_m1.features import Profile
from dreamm_m1.identity import (CIF_3RUK_SHA256, O95425_SHA256, PDB_2KS4_REFERENCE_SHA256,
                                PDB_8YOQ_SHA256, digest, require)
from dreamm_m1.mmcif_input import CIF_1RLW_SHA256
from dreamm_m1.protdcal import _path
from dreamm_m1.reference import verify_reference_assets
from dreamm_m1.rocky_database import FINGERPRINT, PREFIX

SCOPES = {"preparation_charge", "preparation_structure", "features", "descriptors", "classifier_compatibility"}
FIXTURES = {CIF_1RLW_SHA256: "1RLW", CIF_3RUK_SHA256: "3RUK", PDB_8YOQ_SHA256: "8YOQ",
            O95425_SHA256: "O95425-2", PDB_2KS4_REFERENCE_SHA256: "2KS4"}
_BINDING = Lock()


def recipe_sha256():
    return digest(pkgutil.get_data("dreamm", "worker.py"))


def reference_fixture(request):
    require(request.get("mode") in ("M1", "M2"), "SOURCE_INVALID", "this adapter executes reference-only requests")
    fixture = FIXTURES.get(request["source"]["sha256"])
    require(fixture is not None, "SOURCE_INVALID", "input is outside the validated native admission set")
    chains = request.get("chain_selection")
    if fixture == "3RUK":
        require(request["mode"] == "M1" and request.get("pdb_id") == fixture
                and chains == {"namespace": "auth_asym_id", "ids": ["A"]},
                "SOURCE_INVALID", "3RUK requires its explicitly validated chain A selection")
    elif request["mode"] == "M1":
        require(fixture == "1RLW" and request.get("pdb_id") == fixture
                and chains in (None, {"namespace": "auth_asym_id", "ids": ["A"]}),
                "SOURCE_INVALID", "M1 source or chain selection differs from its validated reference")
    else:
        require(fixture in ("8YOQ", "O95425-2", "2KS4") and chains is None,
                "SOURCE_INVALID", "M2 source differs from its validated reference")
    return fixture


@contextmanager
def bind_tools(native, roles, database_prefix):
    """Scoped path relocation, without altering tool arguments or calculations."""
    require(_BINDING.acquire(blocking=False), "SCIENTIFIC_EXECUTION_UNAPPROVED", "one invocation per worker process")
    previous = {}
    changes = {"PDB2PQR": str(roles["pdb2pqr"]), "DSSP_TOOL": roles["dssp"],
               "SASA_TOOL": roles["freesasa"], "MSMS_TOOL": roles["msms"],
               "HHBLITS": str(roles["hhblits"]), "DATABASE": str(database_prefix)}
    try:
        for name, value in changes.items():
            previous[name] = getattr(native, name)
            setattr(native, name, value)
        yield
    finally:
        for name, value in previous.items():
            setattr(native, name, value)
        _BINDING.release()


def run_reference(request_directory, *, bundle, runtime_root, profiles, database_prefix, database_verification,
                  work, profile_bytes, source_packet=None, checkpoint=None):
    """Dispatch unchanged native entry points after external scientific admission.

    Arbitrary-input admission is deliberately outside the tested fixture API.
    Runtime integrity alone cannot make an unapproved profile runnable.
    """
    from .cli import verify_request
    from argparse import Namespace
    from dreamm_m1 import native_m1
    from dreamm_m1.inference import require_classifier_admission
    verify_request(Namespace(directory=request_directory))
    from dreamm_m1.capture import _json
    request = _json(_read(Path(request_directory).absolute() / "request.json", 16384))
    fixture = reference_fixture(request)
    require(set(profiles) == SCOPES, "SCIENTIFIC_EXECUTION_UNAPPROVED", "five admitted M1 profiles required")
    # No tool bindings, runtime imports or model reads before profile admission.
    require_classifier_admission(profiles["classifier_compatibility"])
    for scope, profile in profiles.items():
        require(profile.data["scope"] == scope and profile.data["inputs"].get("fixture", {}).get("sha256")
                == request["source"]["sha256"] and request["mode"] in profile.data["modes"],
                "SCIENTIFIC_EXECUTION_UNAPPROVED", "profile/source/mode mismatch")
        profile.require_scientific_admission()
    require(os.name == "posix" and os.geteuid() != 0, "SCIENTIFIC_EXECUTION_UNAPPROVED", "isolated non-root worker required")
    require(os.cpu_count() == 16 and profiles["features"].data["resources"].get("CPU_threads") == 15,
            "SCIENTIFIC_RUNTIME_MISMATCH", "preserve the validated visible CPU count and 15-thread HHblits request")
    require(os.statvfs(runtime_root).f_flag & os.ST_RDONLY,
            "RUNTIME_INVALID", "scientific runtime must be mounted read-only")
    require(isinstance(database_verification, dict)
            and set(database_verification) == {"manifest_sha256", "mount", "device"}
            and database_verification["manifest_sha256"] == FINGERPRINT,
            "DATABASE_UNVERIFIED", "supervisor database verification required")
    database_root = Path(database_verification["mount"]).absolute()
    device = database_root.stat().st_dev
    require(Path(database_prefix) == database_root / PREFIX
            and database_verification["device"] == "{}:{}".format(os.major(device), os.minor(device))
            and os.statvfs(database_root).f_flag & os.ST_RDONLY,
            "DATABASE_UNVERIFIED", "database mount changed after verification")
    bundle.verify(runtime_root)
    roles = {key: Path(runtime_root).absolute() / value for key, value in bundle.data["roles"].items()}
    require(sys.version_info[:3] == (3, 7, 16)
            and Path(sys.executable).resolve() == roles["science_python"].resolve(),
            "SCIENTIFIC_RUNTIME_MISMATCH", "execute the adapter inside the pinned scientific interpreter")
    for path in (*roles.values(), Path(database_prefix), Path(work)):
        _path(str(path))
    tool_roles = {"preparation_charge": "pdb2pqr", "preparation_structure": "science_python", "features": "hhblits",
                  "descriptors": "protdcal_jar", "classifier_compatibility": "science_python"}
    for scope, role in tool_roles.items():
        item = bundle.data["files"][bundle.data["roles"][role]]
        interpreter_hash = bundle.data["files"][bundle.data["roles"]["science_python"]]["sha256"]
        lock_hash = bundle.data["files"][bundle.data["roles"]["dependency_lock"]]["sha256"]
        require(profiles[scope].data["tool"]["executable_sha256"] == item["sha256"]
                and profiles[scope].data["python"].get("interpreter_sha256") == interpreter_hash
                and profiles[scope].data["dependencies"].get("lock_sha256") == lock_hash
                and profiles[scope].data["parameters"].get("recipe_sha256") == recipe_sha256(),
                "SCIENTIFIC_EXECUTION_UNAPPROVED", "profile tool, interpreter, dependency lock or adapter recipe differs")
    profile = Profile.owner_selected(profile_bytes)
    model, reference, zeroed = (_read(roles[role], limit) for role, limit in
                                (("model", 1024**2), ("reference", 16 * 1024**2), ("zeroed", 32768)))
    require(digest(model) == profile.model_sha256, "SOURCE_INVALID", "published classifier differs")
    verify_reference_assets(reference, zeroed, profile_bytes)
    descriptor_runtime = {"java": str(roles["java"]), "python": str(roles["descriptor_python"]),
                          "jar": str(roles["protdcal_jar"]), "helper": str(roles["helper"]),
                          "kernel": str(roles["kernel"]), "tables": str(roles["weights"]),
                          "schema_json": str(roles["schema_json"]), "schema_tsv": str(roles["schema_tsv"]),
                          "jar_sha256": bundle.data["files"][bundle.data["roles"]["protdcal_jar"]]["sha256"],
                          "helper_sha256": bundle.data["files"][bundle.data["roles"]["helper"]]["sha256"]}
    arguments = dict(work=work, profiles=profiles, template=_read(roles["project"]), descriptor_runtime=descriptor_runtime,
                     profile_bytes=profile_bytes, model=model, reference=reference, zeroed=zeroed, checkpoint=checkpoint)
    with bind_tools(native_m1, roles, database_prefix):
        if fixture == "1RLW":
            require(source_packet is not None, "SOURCE_INVALID", "qualified 1RLW parser packet required")
            return native_m1.run_1rlw(source_packet, **arguments)
        raw = _read(Path(request_directory).absolute() / request["source"]["path"])
        if fixture == "8YOQ":
            return native_m1.run_8yoq(raw, **arguments)
        return native_m1.run_scaling_fixture(raw, fixture=fixture, **arguments)
