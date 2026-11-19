import argparse
from contextlib import contextmanager
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dreamm import cli, jobs
from dreamm.coordinator import QueueClient, SCHEMA
from dreamm_m1.identity import digest, M1Error
from test_cli import pdb_bytes, build

JOB_ID = "d" * 32
TOKEN = "c" * 64
REQUEST = "b" * 32


@contextmanager
def peer(responses):
    with tempfile.TemporaryDirectory(prefix="dreamm-uds-") as temporary, socket.socket(socket.AF_UNIX) as server:
        path = str(Path(temporary) / "queue.sock")
        server.bind(path)
        server.listen()
        server.settimeout(5)
        seen, errors = [], []
        def serve():
            try:
                for reply in responses:
                    connection, _ = server.accept()
                    with connection, connection.makefile("rb") as stream:
                        connection.settimeout(4)
                        raw = stream.readline(16385)
                        seen.append(json.loads(raw) if raw else None)
                        if reply is not None:
                            value = reply if isinstance(reply, bytes) else json.dumps({"schema": SCHEMA, **reply}).encode() + b"\n"
                            try:
                                connection.sendall(value)
                            except (BrokenPipeError, ConnectionResetError):
                                pass
            except BaseException as error:
                errors.append(error)
        thread = Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield path, seen
        finally:
            thread.join(timeout=6)
            server.close()
            if thread.is_alive() or errors:
                raise AssertionError("fake coordinator did not close cleanly: " + repr(errors))


@unittest.skipUnless(sys.platform == "linux" and hasattr(socket, "SO_PEERCRED"), "Linux Unix peer credentials required")
class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dreamm-client-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "input.pdb"
        self.source.write_bytes(pdb_bytes())
        self.spool = self.root / "uploads"
        self.spool.mkdir(mode=0o700)

    def args(self, path, mode="M1", suffix="job"):
        return argparse.Namespace(mode=mode, input=self.source if mode in ("M2", "M4") else None,
            sha256=digest(pdb_bytes()) if mode in ("M2", "M4") else None,
            pdb_id="1rlw" if mode in ("M1", "M3", "M5") else None,
            chains=["A"] if mode in ("M1", "M3", "M5") else None,
            additional=2 if mode == "M5" else None, spool=self.spool if mode in ("M2", "M4") else None,
            socket=path, coordinator_uid=os.getuid(), job_dir=self.root / suffix)

    def test_all_five_submissions_use_the_existing_web_contract(self):
        with peer([{"id": JOB_ID}] * 5) as (path, seen):
            for mode in ("M1", "M2", "M3", "M4", "M5"):
                args = self.args(path, mode, mode)
                result = jobs.submit(args)
                stored = json.loads((args.job_dir / "submission.json").read_bytes())
                self.assertEqual(result["id"], JOB_ID)
                self.assertNotIn(stored["capability"], json.dumps(result))
                self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in args.job_dir.iterdir()))
                if mode in ("M2", "M4"):
                    key = hmac.new(stored["capability"].encode(), stored["spec"]["source_sha256"].encode(), hashlib.sha256).hexdigest()[:32]
                    self.assertEqual(stored["spec"]["upload_id"], key)
                    self.assertEqual((self.spool / (key + ".pdb")).read_bytes(), pdb_bytes())
                    self.assertEqual(stored["spec"]["source_model_count"], 2)
            self.assertEqual([row["spec"]["mode"] for row in seen], ["M1", "M2", "M3", "M4", "M5"])
            self.assertTrue(all(set(row) == {"schema", "operation", "spec", "capability", "request"}
                                and row["schema"] == SCHEMA and row["operation"] == "submit" for row in seen))

    def test_packaged_cli_submission_status_and_completed_recovery(self):
        app = self.root / "dreamm.pyz"
        build.build(app)
        directory = self.root / "cli-job"
        with peer([{"id": JOB_ID}, {"job": {"id": JOB_ID, "mode": "M5", "state": "queued", "queue_position": 1}}]) as (path, seen):
            base = [sys.executable, "-I", "-S", "-B", str(app)]
            submitted = subprocess.run(base + ["submit", "--mode", "M5", "--pdb-id", "1RLW", "--additional", "2",
                "--socket", path, "--coordinator-uid", str(os.getuid()), "--job-dir", str(directory)],
                capture_output=True, timeout=15, check=True)
            status = subprocess.run(base + ["status", str(directory)], capture_output=True, timeout=15, check=True)
            recovered = subprocess.run(base + ["recover-submission", str(directory)], capture_output=True, timeout=15, check=True)
            self.assertEqual(json.loads(submitted.stdout)["id"], JOB_ID)
            self.assertEqual(json.loads(status.stdout)["state"], "queued")
            self.assertEqual(json.loads(recovered.stdout)["id"], JOB_ID)
            capability = json.loads((directory / "submission.json").read_bytes())["capability"].encode()
            for process in (submitted, status, recovered):
                self.assertNotIn(capability, process.stdout + process.stderr)
        self.assertEqual([row["operation"] for row in seen], ["submit", "status"])

    def test_lost_reply_recovers_using_identical_idempotency_and_capability(self):
        with peer([None, {"id": JOB_ID}]) as (path, seen):
            args = self.args(path)
            with self.assertRaises(M1Error):
                jobs.submit(args)
            self.assertFalse((args.job_dir / "job.json").exists())
            result = jobs.recover(argparse.Namespace(job_dir=args.job_dir))
            self.assertEqual(result["id"], JOB_ID)
            self.assertEqual(seen[0], seen[1])
            self.assertEqual(jobs.recover(argparse.Namespace(job_dir=args.job_dir)), result)

    def test_status_only_reads_and_redacts_returned_credentials(self):
        with peer([{"id": JOB_ID}, {"job": {"id": JOB_ID, "mode": "M1", "state": "running", "stage": "hhblits"}}]) as (path, seen):
            args = self.args(path)
            jobs.submit(args)
            before = {p.name: p.read_bytes() for p in args.job_dir.iterdir()}
            result = jobs.status(argparse.Namespace(job_dir=args.job_dir))
            self.assertEqual(result["stage"], "hhblits")
            self.assertEqual(seen[-1]["operation"], "status")
            self.assertEqual(before, {p.name: p.read_bytes() for p in args.job_dir.iterdir()})
        with patch.object(QueueClient, "status", return_value={"id": JOB_ID, "warnings": [TOKEN]}):
            with patch.object(jobs, "read_pending", return_value=(args.job_dir, {"capability": TOKEN,
                 "endpoint": {"socket": path, "uid": os.getuid()}})), patch.object(jobs, "accepted_job", return_value={"id": JOB_ID}):
                self.assertEqual(jobs.status(argparse.Namespace(job_dir=args.job_dir))["warnings"], ["[redacted]"])

    def test_pending_status_cannot_submit_or_recover_a_job(self):
        with peer([None]) as (path, seen):
            args = self.args(path)
            with self.assertRaises(M1Error):
                jobs.submit(args)
        with patch.object(QueueClient, "submit") as submit, patch.object(QueueClient, "status") as status:
            with self.assertRaises(OSError):
                jobs.status(argparse.Namespace(job_dir=args.job_dir))
            submit.assert_not_called()
            status.assert_not_called()

    def test_unexpected_peer_uid_is_rejected_before_credentials_are_sent(self):
        with peer([None]) as (path, seen):
            with self.assertRaisesRegex(M1Error, "unexpected coordinator UID"):
                QueueClient(path, coordinator_uid=os.getuid() + 10000).submit({"mode": "M1"}, capability=TOKEN, request=REQUEST)
        self.assertEqual(seen, [None])

    def test_conflict_denied_and_foreign_replies_fail_closed(self):
        for response in ({"error": "conflict"}, {"error": "not_found"}, {"id": "bad"}):
            with self.subTest(response=response), peer([response]) as (path, seen):
                with self.assertRaises(ValueError):
                    QueueClient(path, coordinator_uid=os.getuid()).submit({"mode": "M1"}, capability=TOKEN, request=REQUEST)
        with peer([{"error": "not_found"}, {"job": {"id": "e" * 32, "mode": "M1", "state": "succeeded"}}]) as (path, seen):
            client = QueueClient(path, coordinator_uid=os.getuid())
            self.assertIsNone(client.status(JOB_ID, capability=TOKEN))
            with self.assertRaises(M1Error):
                client.status(JOB_ID, capability=TOKEN)

    def test_malformed_duplicate_oversized_and_extra_messages_are_rejected(self):
        replies = [b'{}', b'{"schema":1,"schema":2}\n', b'x' * 262145 + b'\n',
                   (json.dumps({"schema": SCHEMA, "id": JOB_ID}) + '\n{}\n').encode()]
        for response in replies:
            with self.subTest(size=len(response)), peer([response]) as (path, seen), self.assertRaises(M1Error):
                QueueClient(path, coordinator_uid=os.getuid()).submit({"mode": "M1"}, capability=TOKEN, request=REQUEST)

    def test_changed_upload_and_public_credentials_are_rejected_on_recovery(self):
        with peer([None]) as (path, seen):
            args = self.args(path, "M4")
            with self.assertRaises(M1Error):
                jobs.submit(args)
        stored = json.loads((args.job_dir / "submission.json").read_bytes())
        (self.spool / (stored["spec"]["upload_id"] + ".pdb")).write_bytes(b"changed")
        with self.assertRaises(M1Error):
            jobs.recover(argparse.Namespace(job_dir=args.job_dir))
        (args.job_dir / "submission.json").chmod(0o644)
        with self.assertRaisesRegex(M1Error, "owner-only credential"):
            jobs.recover(argparse.Namespace(job_dir=args.job_dir))

    def test_n1_and_unrequested_fields_do_not_create_a_job(self):
        args = self.args("/missing.sock", "M5")
        args.additional = 1
        with self.assertRaisesRegex(M1Error, "M5_SINGLETON_UNQUALIFIED"):
            jobs.submit(args)
        self.assertFalse(args.job_dir.exists())
        args = self.args("/missing.sock", "M1")
        args.input = self.source
        with self.assertRaises(M1Error):
            jobs.submit(args)
        self.assertFalse(args.job_dir.exists())
