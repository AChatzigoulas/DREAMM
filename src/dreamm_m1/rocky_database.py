"""Verify the exact immutable UniClust30 data volume during staging."""
import hashlib
import os
from pathlib import Path
import stat

from .capture import _parents
from .identity import canonical, digest, require
from .rocky_admission import DATABASE_BYTES


FINGERPRINT = "f72a4125fe124e9dfecbb53bebae5cdbad5eb7371e0b379f07661179ca52c751"
PREFIX = "uniclust30_2018_08"


def verify_database(root, manifest):
    """Verify once on the mounted immutable database, not on each job."""
    root = Path(root).absolute()
    _parents(root)
    files = manifest["files"]
    require(manifest["fingerprint"] == digest(canonical(files)) == FINGERPRINT
            and manifest["release"] == "2018_08" and manifest["prefix"] == PREFIX
            and sum(row["bytes"] for row in files if row["kind"] == "regular") == DATABASE_BYTES,
            "DATABASE_UNVERIFIED", "manifest differs from the retained database")
    disk = root.stat().st_dev
    device = "{}:{}".format(os.major(disk), os.minor(disk))
    with Path("/proc/self/mountinfo").open(encoding="ascii") as mounts:
        local = any(left.split()[2] == device and right.split()[0] in ("xfs", "ext4")
                    for left, right in (line.split(" - ", 1) for line in mounts))
    require(local and os.statvfs(root).f_flag & os.ST_RDONLY,
            "DATABASE_UNVERIFIED", "the database must be on read-only local block storage")
    require({p.name for p in root.iterdir()} == {row["name"] for row in files},
            "DATABASE_UNVERIFIED", "unexpected or missing database members")
    for row in files:
        path = root / row["name"]
        if row["kind"] == "link":
            require(path.is_symlink() and os.readlink(path) == row["target"],
                    "DATABASE_UNVERIFIED", "database alias changed")
            continue
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_dev == disk and info.st_size == row["bytes"],
                    "DATABASE_UNVERIFIED", "wrong type, device or database file size")
            checksum = hashlib.sha256()
            for chunk in iter(lambda: stream.read(2 * 1024**2), b""):
                checksum.update(chunk)
            require(checksum.hexdigest() == row["sha256"],
                    "DATABASE_UNVERIFIED", "database file hash mismatch")
    return {"manifest_sha256": FINGERPRINT, "device": device, "mount": str(root)}
