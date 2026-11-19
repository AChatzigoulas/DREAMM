"""Bounded client for the existing PHP/Python coordinator contract. No scheduler."""
import os
from pathlib import Path
import re
import socket
import stat
import struct
import time

from dreamm_m1.capture import _json, _parents
from dreamm_m1.identity import canonical, require

SCHEMA = "dreamm-web-queue/1"
MAX_REPLY = 262144


def identifier(value, length):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{" + str(length) + r"}", value) is not None


class QueueClient:
    def __init__(self, path, *, coordinator_uid):
        require(os.name == "posix" and hasattr(socket, "SO_PEERCRED")
                and type(coordinator_uid) is int and coordinator_uid >= 0,
                "COORDINATOR_UNAVAILABLE", "Linux peer credentials and explicit coordinator UID required")
        self.path = Path(path).absolute()
        self.uid = coordinator_uid

    def call(self, operation, **fields):
        require(operation in ("submit", "status"), "COORDINATOR_INVALID", "unknown operation")
        _parents(self.path.parent)
        require(stat.S_ISSOCK(self.path.lstat().st_mode), "COORDINATOR_UNAVAILABLE", "expected a Unix socket")
        raw = canonical({"schema": SCHEMA, "operation": operation, **fields}) + b"\n"
        require(len(raw) <= 16384, "RESOURCE_LIMIT", "coordinator request exceeds bound")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(self.path))
            _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            require(uid == self.uid, "COORDINATOR_UNAVAILABLE", "unexpected coordinator UID")
            deadline = time.monotonic() + 3
            connection.settimeout(3)
            connection.sendall(raw)
            received = bytearray()
            while b"\n" not in received:
                remaining = deadline - time.monotonic()
                require(remaining > 0, "COORDINATOR_UNAVAILABLE", "coordinator response deadline")
                connection.settimeout(remaining)
                chunk = connection.recv(min(16384, MAX_REPLY + 1 - len(received)))
                require(chunk, "COORDINATOR_UNAVAILABLE", "truncated coordinator reply")
                received.extend(chunk)
                require(len(received) <= MAX_REPLY, "COORDINATOR_UNAVAILABLE", "oversized coordinator reply")
            require(received[-1:] == b"\n" and b"\n" not in received[:-1],
                    "COORDINATOR_UNAVAILABLE", "unexpected trailing coordinator data")
        reply = _json(bytes(received))
        require(isinstance(reply, dict) and reply.get("schema") == SCHEMA,
                "COORDINATOR_UNAVAILABLE", "coordinator protocol differs")
        return reply

    def submit(self, spec, *, capability, request):
        require(isinstance(spec, dict) and identifier(capability, 64) and identifier(request, 32),
                "COORDINATOR_INVALID", "submission credentials")
        reply = self.call("submit", spec=spec, capability=capability, request=request)
        if reply.get("error") == "conflict":
            raise ValueError("COORDINATOR_CONFLICT: idempotency key is already bound to another request")
        require(set(reply) == {"schema", "id"} and identifier(reply["id"], 32),
                "COORDINATOR_UNAVAILABLE", "invalid submitted job identity")
        return reply["id"]

    def status(self, job_id, *, capability):
        require(identifier(job_id, 32) and identifier(capability, 64), "COORDINATOR_INVALID", "job credentials")
        reply = self.call("status", id=job_id, capability=capability)
        if reply == {"schema": SCHEMA, "error": "not_found"}:
            return None
        job = reply.get("job")
        require(set(reply) == {"schema", "job"} and isinstance(job, dict) and job.get("id") == job_id
                and job.get("mode") in ("M1", "M2", "M3", "M4", "M5")
                and job.get("state") in ("queued", "running", "succeeded", "failed"),
                "COORDINATOR_UNAVAILABLE", "invalid or foreign job status")
        # Never echo a credential even if a buggy peer includes one.
        public = {"id", "mode", "state", "queue_position", "stage", "elapsed_seconds", "warnings", "error", "outcome"}
        return {key: value for key, value in job.items() if key in public}
