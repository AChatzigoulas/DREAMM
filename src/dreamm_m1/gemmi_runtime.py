"""Fail-closed admission for the approved parser sandbox; never provisions it.

The read-only provisioning receipt is an external review requirement, not a
sandbox. These checks supplement it and cannot establish credential absence.
No Gemmi code is imported until environment and installed-byte checks pass.
"""
import importlib
import io
import json
import os
from pathlib import Path
import platform
import socket
import stat
import sys
import sysconfig
import zipfile

from .capture import _read, _parents
from .identity import M1Error, canonical, digest, require

WHEEL_NAME = "gemmi-0.7.5-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"
WHEEL_SHA256 = "217bb9ac9da7c90704026dacfc0a0652a38f4df1e318225d8f35c75f1f8c7ebf"
APPROVAL = Path("/run/dreamm-parser-approval.json")
SITE = Path("/work/parser-venv/lib/python3.12/site-packages")
_RUNTIME = None


def _admit_environment():
    require(sys.platform == "linux" and platform.python_implementation() == "CPython"
            and sys.version_info[:2] == (3, 12) and platform.machine() == "x86_64",
            "PARSER_ENVIRONMENT_UNAVAILABLE", "requires isolated Linux x86-64 CPython 3.12; no substitution")
    require(os.getuid() > 0 and os.geteuid() > 0, "PARSER_ENVIRONMENT_UNAVAILABLE", "non-root required")
    libc_name, libc_version = platform.libc_ver()
    try:
        libc_tuple = tuple(int(part) for part in libc_version.split(".")[:2])
    except ValueError:
        libc_tuple = ()
    require(libc_name == "glibc" and libc_tuple >= (2, 27)
            and sysconfig.get_config_var("SOABI") == "cpython-312-x86_64-linux-gnu",
            "PARSER_ENVIRONMENT_UNAVAILABLE", "approved wheel requires release CPython 3.12 ABI and glibc >= 2.27")
    require(sys.flags.isolated and sys.flags.no_site, "PARSER_ENVIRONMENT_UNAVAILABLE", "run with -I -S -B")
    require(_RUNTIME is not None or not any(n == "gemmi" or n.startswith("gemmi.") for n in sys.modules),
            "PARSER_ENVIRONMENT_UNAVAILABLE", "parser must not be pre-imported")
    try:
        info = APPROVAL.lstat()
        require(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o222
                and os.statvfs(APPROVAL).f_flag & os.ST_RDONLY,
                "PARSER_ENVIRONMENT_UNAVAILABLE", "read-only-mounted provisioning receipt required")
        approval = json.loads(_read(APPROVAL, 16384))
        require(approval["schema"] == "dreamm-parser-sandbox/1"
                and approval["disposable"] is True and approval["credentials_absent_reviewed"] is True
                and approval["privileged_mounts_absent_reviewed"] is True
                and approval["network_namespace_reviewed"] is True
                and approval["wheel_sha256"] == WHEEL_SHA256,
                "PARSER_ENVIRONMENT_UNAVAILABLE", "sandbox provisioning review incomplete")
        image_hash = approval["rootfs_sha256"]
        require(isinstance(image_hash, str) and len(image_hash) == 64
                and all(c in "0123456789abcdef" for c in image_hash),
                "PARSER_ENVIRONMENT_UNAVAILABLE", "rootfs fingerprint required")
        status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
        require(status.get("NoNewPrivs", "").strip() == "1"
                and all(int(status[name].strip(), 16) == 0 for name in ("CapEff", "CapPrm", "CapAmb")),
                "PARSER_ENVIRONMENT_UNAVAILABLE", "no-new-privileges and zero capabilities required")
        require({name for _, name in socket.if_nameindex()} <= {"lo"},
                "PARSER_ENVIRONMENT_UNAVAILABLE", "network must be disconnected")
        require(os.statvfs("/").f_flag & os.ST_RDONLY,
                "PARSER_ENVIRONMENT_UNAVAILABLE", "read-only sandbox root required")
        for path in ("/code", "/inputs", "/wheelhouse"):
            require(os.statvfs(path).f_flag & os.ST_RDONLY,
                    "PARSER_ENVIRONMENT_UNAVAILABLE", "code, inputs and wheelhouse must be read-only")
        # An operator must review the complete mount inventory, not just these
        # three flags. An extra host bind mount fails this exact fingerprint.
        mounts = Path("/proc/self/mountinfo").read_bytes()
        require(digest(mounts) == approval["mountinfo_sha256"],
                "PARSER_ENVIRONMENT_UNAVAILABLE", "mount inventory differs from reviewed sandbox")
        interpreter = Path(sys.executable).resolve()
        interpreter_hash = digest(_read(interpreter, 64 * 1024**2))
        require(interpreter_hash == approval["interpreter_sha256"],
                "PARSER_ENVIRONMENT_UNAVAILABLE", "interpreter fingerprint differs")
        return {"schema": "dreamm-parser-runtime/1", "python": sys.version,
                "interpreter": str(interpreter), "interpreter_sha256": interpreter_hash,
                "libc": [libc_name, libc_version], "SOABI": sysconfig.get_config_var("SOABI"),
                "platform": platform.platform(), "uid": os.getuid(), "rootfs_sha256": image_hash,
                "provisioning_receipt_sha256": digest(canonical(approval)),
                "mountinfo_sha256": digest(mounts), "wheel_sha256": WHEEL_SHA256,
                "isolation_basis": "external_review_plus_runtime_checks_not_venv_alone"}
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, M1Error):
            raise
        raise M1Error("PARSER_ENVIRONMENT_UNAVAILABLE", "approved sandbox provisioning evidence unavailable") from exc


def verified_gemmi():
    """Return the pinned parser and receipt, only inside the admitted sandbox.

    No fallback, user executable path, site customization, or pip invocation.
    Hash all installed package files against the approved wheel before import.
    """
    global _RUNTIME
    receipt = _admit_environment()
    if _RUNTIME is not None:
        gemmi, previous = _RUNTIME
        require(all(previous[k] == v for k, v in receipt.items()) and sys.modules.get("gemmi") is gemmi,
                "PARSER_ENVIRONMENT_UNAVAILABLE", "parser admission changed in process")
        return gemmi, dict(previous)
    try:
        wheel = _read(Path("/wheelhouse") / WHEEL_NAME, 4 * 1024**2)
        require(digest(wheel) == WHEEL_SHA256, "DEPENDENCY_UNVERIFIED", "approved wheel hash mismatch")
        _parents(SITE)
        checked = {}
        with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
            for member in archive.infolist():
                name = member.filename
                if not name.startswith("gemmi/") or member.is_dir():
                    continue
                require(".." not in name.split("/") and "\\" not in name and member.file_size <= 32 * 1024**2,
                        "DEPENDENCY_UNVERIFIED", "unexpected wheel member")
                path = SITE / name
                _parents(path.parent)
                expected = archive.read(member)
                installed = _read(path, 32 * 1024**2)
                require(installed == expected, "DEPENDENCY_UNVERIFIED", "installed parser bytes differ from wheel")
                checked[name] = digest(installed)
        actual = {p.relative_to(SITE).as_posix() for p in (SITE / "gemmi").rglob("*") if p.is_file()}
        require(checked and actual == set(checked), "DEPENDENCY_UNVERIFIED", "extra/missing installed parser files")
        sys.path.insert(0, str(SITE))
        try:
            gemmi = importlib.import_module("gemmi")
        finally:
            sys.path.remove(str(SITE))
        require(gemmi.__version__ == "0.7.5", "DEPENDENCY_UNVERIFIED", "parser version mismatch")
        receipt.update(parser="Gemmi", parser_version=gemmi.__version__, installed_package_sha256=checked)
        _RUNTIME = gemmi, dict(receipt)
        return gemmi, receipt
    except (OSError, ImportError, ValueError, zipfile.BadZipFile) as exc:
        if isinstance(exc, M1Error):
            raise
        raise M1Error("DEPENDENCY_UNVERIFIED", "approved offline parser installation unavailable") from exc
