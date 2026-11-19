"""Bounded, lossless native mesh transport for the frozen O95425-2 retry."""
from contextlib import ExitStack
import io
import math
import os
import selectors
import stat
import subprocess
import time

from .capture import _parents
from .identity import M1Error, O95425_SHA256, canonical, digest, require
from .receipt_chunks import read_scientific_receipt, write_scientific_receipt


MIB = 1024 ** 2
FACE_LIMIT = 16 * MIB
VERTEX_LIMIT = 32 * MIB
MESH_PROFILE = "o95425-2-native-msms-fifo-v1"


def _unlink_pipe(path, expected):
    current = path.lstat()
    require(stat.S_ISFIFO(current.st_mode) and (current.st_dev, current.st_ino) == expected,
            "ARTIFACT_MISSING_OR_MALFORMED", "MSMS output pipe replaced")
    path.unlink()


def _capture_pipes(argv, prefix, seconds):
    """Read both pipes without deadlock, keeping the inherited file limit."""
    work = prefix.parent
    _parents(work)
    info = work.stat()
    require(info.st_uid == os.geteuid() and not info.st_mode & 0o077,
            "EXECUTION_PROFILE_INVALID", "MSMS work directory must be private")
    storage = os.statvfs(work)
    require(storage.f_bavail * storage.f_frsize >= FACE_LIMIT + VERTEX_LIMIT + 32 * MIB,
            "RESOURCE_LIMIT", "insufficient work tmpfs for the full mesh plus reserve")
    limits = {".face": FACE_LIMIT, ".vert": VERTEX_LIMIT}
    output = {suffix: bytearray() for suffix in limits}
    deadline = time.monotonic() + seconds
    with ExitStack() as cleanup, ExitStack() as writers, selectors.DefaultSelector() as selector:
        for suffix in limits:
            path = prefix.with_suffix(suffix)
            os.mkfifo(path, 0o600)
            entry = path.lstat()
            cleanup.callback(_unlink_pipe, path, (entry.st_dev, entry.st_ino))
            read_fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            cleanup.callback(os.close, read_fd)
            opened = os.fstat(read_fd)
            require(stat.S_ISFIFO(opened.st_mode) and (opened.st_dev, opened.st_ino) == (entry.st_dev, entry.st_ino),
                    "ARTIFACT_MISSING_OR_MALFORMED", "MSMS output pipe replaced at open")
            selector.register(read_fd, selectors.EVENT_READ, suffix)
            # Keep EOF distinct from the interval before MSMS opens its outputs.
            writers.callback(os.close, os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW))
        stdout = cleanup.enter_context((work / "msms.stdout").open("xb"))
        stderr = cleanup.enter_context((work / "msms.stderr").open("xb"))
        child = subprocess.Popen(argv, cwd=work, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                 close_fds=True)
        try:
            while selector.get_map():
                if child.poll() is not None:
                    writers.close()
                remaining = deadline - time.monotonic()
                require(remaining > 0, "TOOL_TIMEOUT", "MSMS mesh capture exceeded its deadline")
                for key, _ in selector.select(min(remaining, 0.1)):
                    block = os.read(key.fd, 65536)
                    if not block:
                        selector.unregister(key.fd)
                        continue
                    data = output[key.data]
                    require(len(data) + len(block) <= limits[key.data],
                            "RESOURCE_LIMIT", "MSMS " + key.data + " exceeded its byte bound")
                    data.extend(block)
            require(child.wait(timeout=max(0, deadline - time.monotonic())) == 0,
                    "TOOL_EXIT", "MSMS failed; inspect attempt logs")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
    return bytes(output[".face"]), bytes(output[".vert"])


def _header(stream, role, xyzr, atom_count):
    first, second, count = (stream.readline(257) for _ in range(3))
    singular = b"#vertex" if role == "vertices" else b"#faces"
    expected = ("# MSMS solvent excluded surface " + role + " for " + str(xyzr) + "\n").encode("ascii")
    require(first == expected and second.split() == [singular, b"#sphere", b"density", b"probe_r"]
            and second.endswith(b"\n") and count.endswith(b"\n"),
            "ARTIFACT_MISSING_OR_MALFORMED", "unexpected MSMS mesh header")
    tokens = count.split()
    require(len(tokens) == 4 and tokens[0].isdigit() and len(tokens[0]) <= 7 and
            tokens[1].isdigit() and int(tokens[1]) == atom_count and tokens[2:] == [b"1.00", b"1.50"],
            "ARTIFACT_MISSING_OR_MALFORMED", "unexpected MSMS mesh dimensions or parameters")
    rows = int(tokens[0])
    require(rows > 0, "ARTIFACT_MISSING_OR_MALFORMED", "empty MSMS mesh")
    return rows


def _rows(stream, count, columns):
    for _ in range(count):
        line = stream.readline(257)
        fields = line.split()
        require(0 < len(line) <= 256 and line.endswith(b"\n") and len(fields) == columns,
                "ARTIFACT_MISSING_OR_MALFORMED", "truncated or malformed MSMS mesh row")
        yield fields
    require(not stream.read(1), "ARTIFACT_MISSING_OR_MALFORMED", "extra MSMS mesh rows")


def validate_mesh(faces, vertices, *, xyzr, atom_count):
    from .local_budget import trusted_local, artifact_bytes
    limit = artifact_bytes() if trusted_local() else None
    require(0 < len(faces) <= (limit or FACE_LIMIT) and 0 < len(vertices) <= (limit or VERTEX_LIMIT),
            "RESOURCE_LIMIT", "MSMS mesh byte bounds")
    with io.BytesIO(faces) as face_file, io.BytesIO(vertices) as vertex_file:
        face_count = _header(face_file, "faces", xyzr, atom_count)
        vertex_count = _header(vertex_file, "vertices", xyzr, atom_count)
        try:
            for row in _rows(vertex_file, vertex_count, 9):
                require(all(math.isfinite(float(v)) for v in row[:6]),
                        "ARTIFACT_MISSING_OR_MALFORMED", "nonfinite MSMS vertex")
                tuple(map(int, row[6:]))
            for row in _rows(face_file, face_count, 5):
                indices = tuple(map(int, row))
                require(all(1 <= index <= vertex_count for index in indices[:3]),
                        "ARTIFACT_MISSING_OR_MALFORMED", "MSMS face references a missing vertex")
        except M1Error:
            raise
        except (ValueError, OverflowError) as exc:
            require(False, "ARTIFACT_MISSING_OR_MALFORMED", str(exc))
    return face_count, vertex_count


def legacy_vertex_array(module, path, raw):
    """Supply exact validated bytes to the unchanged, pinned Bio.PDB reader."""
    require("open" not in vars(module), "SCIENTIFIC_RUNTIME_MISMATCH", "unexpected MSMS reader override")

    def open_vertex(request, mode="r"):
        require(request == str(path) and mode in ("r", "rt"),
                "SCIENTIFIC_RUNTIME_MISMATCH", "unexpected MSMS vertex reader access")
        return io.TextIOWrapper(io.BytesIO(raw), encoding="ascii")

    module.open = open_vertex
    try:
        return module._read_vertex_array(str(path))
    finally:
        del module.open


def capture_o95425_mesh(tool, xyzr, prefix, seconds, *, atom_count, fixture_sha256, reader):
    import resource
    require(fixture_sha256 == O95425_SHA256 and atom_count == 28036 and
            xyzr == prefix.parent / "surface.xyzr" and prefix.name == "msms-surface",
            "SCIENTIFIC_EXECUTION_UNAPPROVED", "MSMS mesh transport requires the pinned O95425-2 fixture")
    require(resource.getrlimit(resource.RLIMIT_FSIZE) == (8 * MIB, 8 * MIB),
            "SCIENTIFIC_EXECUTION_UNAPPROVED", "inherited 8 MiB regular-file limit must remain in force")
    argv = (str(tool), "-probe_radius", "1.5", "-if", str(xyzr), "-of", str(prefix))
    faces, vertices = _capture_pipes(argv, prefix, seconds)
    face_count, vertex_count = validate_mesh(faces, vertices, xyzr=xyzr, atom_count=atom_count)
    receipt = {}
    for suffix, raw, rows in ((".face", faces, face_count), (".vert", vertices, vertex_count)):
        path = prefix.with_suffix(suffix)
        write_scientific_receipt(path, raw, fixture_sha256=fixture_sha256)
        require(read_scientific_receipt(path, fixture_sha256=fixture_sha256) == raw,
                "ARTIFACT_MISSING_OR_MALFORMED", "MSMS mesh changed during receipt round trip")
        receipt[suffix[1:]] = {"bytes": len(raw), "rows": rows, "sha256": digest(raw)}
    (prefix.parent / "msms-mesh.json").write_bytes(canonical({"atoms": atom_count, "argv": list(argv), **receipt}))
    surface = legacy_vertex_array(reader, prefix.with_suffix(".vert"), vertices)
    require(len(surface) == vertex_count, "ARTIFACT_MISSING_OR_MALFORMED", "Bio.PDB vertex count differs")
    return surface
