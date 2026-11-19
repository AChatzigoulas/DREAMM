"""Attempt-local checkpoint storage for the trusted coordinator, never the worker UID."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import tempfile

from .capture import LIMIT, _json, _not_link, _parents, _read
from .identity import canonical, digest, require, sha


SCHEMA = "dreamm-attempt-checkpoint/1"
MAX_STAGE_BYTES = 32 * 1024**2
NAME = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
ID = re.compile(r"[a-f0-9]{32}")


def _check(ok, detail):
    require(ok, "CHECKPOINT_INVALID", detail)


def _sync_dir(path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _private(path, directory):
    info = _not_link(path, directory=directory)
    if os.name == "posix":
        _check(info.st_uid == os.geteuid() and not stat.S_IMODE(info.st_mode) & 0o077,
               "checkpoint storage is not coordinator-private")


def _directory(path):
    try:
        path.mkdir(mode=0o700)
        _sync_dir(path.parent)
    except FileExistsError:
        pass
    _private(path, directory=True)


def _write(path, raw):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _valid_names(names):
    return all(type(n) is str and NAME.fullmatch(n) and n != "manifest.json" for n in names)


class AttemptCheckpoints:
    """Coordinator-side API. The same queue claim must fence both load and commit."""

    def __init__(self, supervisor, claim):
        _check(all(type(key) is str and ID.fullmatch(key) for key in (claim.job_id, claim.token)),
               "invalid queue claim identifiers")
        self._supervisor = supervisor
        self._claim = claim
        self._root = Path(supervisor.queue.root).absolute()
        self._identity = {"job": claim.job_id, "token": claim.token, "attempt": claim.number, "epoch": claim.epoch}

    @contextmanager
    def _fence(self):
        # The queue transaction and lifetime lock exclude both claim transitions and
        # competing coordinators while committing or returning a validated record.
        supervisor = self._supervisor
        with supervisor._mutex, supervisor.queue.connect(write=True) as con:
            supervisor._check(con, self._claim)
            yield

    def _path(self, stage, create=False):
        _check(type(stage) is str and NAME.fullmatch(stage), "invalid stage identifier")
        _parents(self._root)
        _private(self._root, directory=True)
        directory = self._root
        for part in ("checkpoints", self._claim.job_id, self._claim.token):
            directory /= part
            if create:
                _directory(directory)
            else:
                try:
                    _private(directory, directory=True)
                except FileNotFoundError:
                    return None
        return directory / stage

    def _manifest(self, stage, inputs, outputs):
        _check(isinstance(inputs, dict) and 0 < len(inputs) <= 64 and _valid_names(inputs),
               "checkpoint inputs must be named SHA-256 fingerprints")
        for value in inputs.values():
            sha(value)
        return {"schema": SCHEMA, "claim": self._identity, "stage": stage, "complete": True,
                "inputs": inputs, "outputs": outputs}

    def _load(self, path, stage, inputs, names):
        if path is None:
            return None
        try:
            _private(path, directory=True)
        except FileNotFoundError:
            return None
        meta = path / "manifest.json"
        _private(meta, directory=False)
        manifest = _json(_read(meta, 64 * 1024))
        _check(isinstance(manifest, dict) and isinstance(manifest.get("outputs"), dict),
               "invalid completion manifest")
        files = manifest["outputs"]
        _check(canonical(manifest) == canonical(self._manifest(stage, inputs, files))
               and set(files) == set(names) and {p.name for p in path.iterdir()} == set(names) | {"manifest.json"},
               "claim, scientific inputs or output set changed")
        total = 0
        result = {}
        for name, record in files.items():
            _check(isinstance(record, dict) and set(record) == {"bytes", "sha256"} and
                   type(record["bytes"]) is int and 0 < record["bytes"] <= LIMIT,
                   "invalid artifact bounds")
            total += record["bytes"]
            _check(total <= MAX_STAGE_BYTES, "stage byte budget exceeded")
            _private(path / name, directory=False)
            raw = _read(path / name, record["bytes"])
            _check(len(raw) == record["bytes"] and digest(raw) == record["sha256"],
                   "checkpoint artifact size or SHA-256 mismatch")
            result[name] = raw
        return result

    def load(self, stage, inputs, names):
        names = tuple(names)
        _check(0 < len(names) <= 8 and _valid_names(names) and len(names) == len(set(names)), "invalid artifact roles")
        self._manifest(stage, inputs, {})
        with self._fence():
            path = self._path(stage)
            result = self._load(path, stage, inputs, names)
            if result is not None:
                _sync_dir(path.parent)
            return result

    def commit(self, stage, inputs, artifacts):
        _check(isinstance(artifacts, dict) and 0 < len(artifacts) <= 8 and _valid_names(artifacts) and
               all(type(v) is bytes and 0 < len(v) <= LIMIT for v in artifacts.values()) and
               sum(map(len, artifacts.values())) <= MAX_STAGE_BYTES, "invalid artifact roles or byte bounds")
        files = {k: {"bytes": len(v), "sha256": digest(v)} for k, v in artifacts.items()}
        manifest = canonical(self._manifest(stage, inputs, files))
        with self._fence():
            target = self._path(stage, create=True)
            prior = self._load(target, stage, inputs, artifacts)
            if prior is not None:
                _check(prior == artifacts, "completed checkpoint is immutable")
                _sync_dir(target.parent)
                return
            # An interrupted write leaves only a uniquely named staging directory.
            # No reader treats it as complete. Keeping it preserves failure evidence.
            pending = Path(tempfile.mkdtemp(prefix=".partial-", dir=str(target.parent)))
            for name, raw in artifacts.items():
                _write(pending / name, raw)
            _write(pending / "manifest.json", manifest)
            _sync_dir(pending)
            os.rename(pending, target)
            _sync_dir(target.parent)
