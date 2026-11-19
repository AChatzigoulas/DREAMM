"""Private byte-preserving receipt storage for the pinned O95425-2 fixture."""
import os
from itertools import islice
from pathlib import Path

from .capture import LIMIT, _json, _parents, _read
from .identity import O95425_SHA256, canonical, digest, require


CHUNKABLE = frozenset(("charge-map.json", "preparation-map.json", "adapter_map.json",
                      "join.json", "unscaled.json", "scaling.json", "msms-surface.face", "msms-surface.vert"))
MAX_TOTAL = 64 * 1024**2
SCHEMA = "dreamm-scientific-receipt-chunks/1"


def _location(path):
    path = Path(path).absolute()
    require(path.name in CHUNKABLE and ".." not in path.parts,
            "ARTIFACT_MISSING_OR_MALFORMED", "unsupported receipt path or role")
    _parents(path.parent)
    return path, path.with_name(path.name + ".chunks")


def _write(path, raw):
    require(0 < len(raw) <= LIMIT, "RESOURCE_LIMIT", "physical receipt file exceeds bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as stream:
        stream.write(raw)


def _part(index):
    return f"part-{index:02d}.bin"


def write_scientific_receipt(path, raw, *, fixture_sha256):
    """Keep ordinary receipts byte-identical. Seal oversize receipts with an index."""
    path, folder = _location(path)
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_TOTAL, "RESOURCE_LIMIT", "logical receipt size")
    require(not os.path.lexists(path) and not os.path.lexists(folder),
            "ARTIFACT_MISSING_OR_MALFORMED", "receipt destination already exists")
    if len(raw) <= LIMIT:
        _write(path, raw)
        return path

    require(fixture_sha256 == O95425_SHA256, "RESOURCE_LIMIT", "chunking requires the pinned O95425-2 fixture")
    folder.mkdir(mode=0o700)
    parts = []
    view = memoryview(raw)
    for index, offset in enumerate(range(0, len(raw), LIMIT)):
        part = view[offset:offset + LIMIT]
        _write(folder / _part(index), part)
        parts.append(digest(part))
    manifest = canonical({"schema": SCHEMA, "fixture_sha256": fixture_sha256, "name": path.name,
                          "bytes": len(raw), "sha256": digest(raw), "parts": parts})
    _write(folder / "manifest.json", manifest)
    return folder / "manifest.json"


def read_scientific_receipt(path, *, fixture_sha256):
    """Require exact order, file set, lengths and hashes before returning bytes."""
    path, folder = _location(path)
    if not os.path.lexists(folder):
        return _read(path)
    require(not os.path.lexists(path) and fixture_sha256 == O95425_SHA256,
            "ARTIFACT_MISSING_OR_MALFORMED", "conflicting receipt or foreign chunk fixture")
    _parents(folder)
    manifest = _json(_read(folder / "manifest.json", 4096))
    require(isinstance(manifest, dict) and set(manifest) == {"schema", "fixture_sha256", "name", "bytes", "sha256", "parts"}
            and manifest["schema"] == SCHEMA and manifest["fixture_sha256"] == fixture_sha256
            and manifest["name"] == path.name and type(manifest["bytes"]) is int and LIMIT < manifest["bytes"] <= MAX_TOTAL,
            "ARTIFACT_MISSING_OR_MALFORMED", "chunk manifest identity or size")
    total = manifest["bytes"]
    count = (total + LIMIT - 1) // LIMIT
    parts = manifest["parts"]
    require(isinstance(parts, list) and len(parts) == count
            and set(entry.name for entry in islice(folder.iterdir(), count + 2)) == {"manifest.json", *(_part(i) for i in range(count))},
            "ARTIFACT_MISSING_OR_MALFORMED", "chunk count or file set")
    out = bytearray()
    for index, expected in enumerate(parts):
        data = _read(folder / _part(index))
        require(len(data) == min(LIMIT, total - len(out)) and digest(data) == expected,
                "ARTIFACT_MISSING_OR_MALFORMED", "chunk length or hash")
        out.extend(data)
    require(len(out) == total and digest(out) == manifest["sha256"],
            "ARTIFACT_MISSING_OR_MALFORMED", "whole receipt length or hash")
    return bytes(out)
