"""Durable CLI credentials and submission through the existing coordinator."""
import argparse
import hashlib
import hmac
import os
from pathlib import Path
import secrets

from .cli import check_selection, read_input, sync_directory, write_private
from .coordinator import QueueClient, identifier
from dreamm_m1.capture import _json, _parents, _read
from dreamm_m1.identity import canonical, digest, require, sha
from dreamm_m1.pdb_input import read_pdb_source

SCHEMA = "dreamm-standalone-job/1"


def private_directory(path):
    path = Path(path).absolute()
    _parents(path)
    info = path.stat()
    require(os.name == "posix" and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
            "JOB_CREDENTIALS_INVALID", "private owner-only job or spool directory required")
    return path


def record(path):
    private_directory(path.parent)
    info = path.lstat()
    require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
            "JOB_CREDENTIALS_INVALID", "owner-only credential file required")
    return _json(_read(path, 16384))


def spec_from_args(args):
    upload = args.mode in ("M2", "M4")
    check_selection(args.mode, "pdb" if upload else "mmcif", args.pdb_id, args.chains, args.additional)
    require((upload and args.input is not None and args.sha256 is not None and args.spool is not None)
            or (not upload and args.input is None and args.sha256 is None and args.spool is None),
            "REQUEST_INVALID", "uploads require input, SHA-256 and the configured private spool")
    spec = {"mode": args.mode}
    raw = None
    if upload:
        raw = read_input(args.input)
        require(digest(raw) == sha(args.sha256), "SOURCE_INVALID", "uploaded input hash differs")
        source = read_pdb_source(raw, expected_sha256=args.sha256)
        spec.update(source_model_count=len(source.model_ids), source_sha256=args.sha256)
    else:
        spec.update(structure_id=args.pdb_id.upper(), auth_chains=args.chains or [])
        if args.mode == "M5":
            require(args.additional != 1, "M5_SINGLETON_UNQUALIFIED",
                    "the coordinator submission path does not accept M5 with one additional conformation")
            spec["n_additional"] = args.additional
    return spec, raw


def publish_record(directory, name, data):
    pending = directory / ("." + name + ".pending")
    raw = canonical(data)
    if pending.exists():
        require(_read(pending, 16384) == raw, "JOB_CREDENTIALS_INVALID", "incomplete record requires reconciliation")
    else:
        write_private(pending, raw)
    os.rename(pending, directory / name)
    sync_directory(directory)


def read_pending(directory):
    directory = private_directory(directory)
    pending = record(directory / "submission.json")
    require(isinstance(pending, dict) and set(pending) == {"schema", "spec", "capability", "request", "endpoint", "upload"}
            and pending["schema"] == SCHEMA and identifier(pending["capability"], 64)
            and identifier(pending["request"], 32) and isinstance(pending["spec"], dict)
            and isinstance(pending["endpoint"], dict) and set(pending["endpoint"]) == {"socket", "uid"},
            "JOB_CREDENTIALS_INVALID", "invalid saved submission")
    spec = pending["spec"]
    mode = spec.get("mode")
    upload = mode in ("M2", "M4")
    check_selection(mode, "pdb" if upload else "mmcif", spec.get("structure_id"),
                    None if upload else spec.get("auth_chains") or None, spec.get("n_additional"))
    expected = {"mode", "source_model_count", "source_sha256", "upload_id"} if upload else {"mode", "structure_id", "auth_chains"}
    if mode == "M5":
        expected.add("n_additional")
        require(spec["n_additional"] != 1, "M5_SINGLETON_UNQUALIFIED",
                "the coordinator submission path does not accept M5 with one additional conformation")
    require(set(spec) == expected, "JOB_CREDENTIALS_INVALID", "saved request has unsupported fields")
    if upload:
        require(identifier(spec["upload_id"], 32) and type(spec["source_model_count"]) is int
                and 1 <= spec["source_model_count"] <= 20 and pending["upload"] is not None,
                "JOB_CREDENTIALS_INVALID", "saved upload identity")
        sha(spec["source_sha256"])
    else:
        require(isinstance(spec["auth_chains"], list) and pending["upload"] is None,
                "JOB_CREDENTIALS_INVALID", "saved structure selection")
    return directory, pending


def staged_input(pending):
    upload = pending["upload"]
    if upload is not None:
        require(isinstance(upload, dict) and set(upload) == {"root", "key"}
                and identifier(upload["key"], 32) and pending["spec"].get("upload_id") == upload["key"],
                "JOB_CREDENTIALS_INVALID", "invalid upload reference")
        path = private_directory(upload["root"]) / (upload["key"] + ".pdb")
        require(digest(read_input(path)) == pending["spec"]["source_sha256"],
                "SOURCE_INVALID", "staged input is missing, incomplete or changed")


def submit(args):
    spec, raw = spec_from_args(args)
    client = QueueClient(args.socket, coordinator_uid=args.coordinator_uid)
    spool = private_directory(args.spool) if raw is not None else None
    directory = Path(args.job_dir).absolute()
    _parents(directory.parent)
    capability, request = secrets.token_hex(32), secrets.token_hex(16)
    upload = None
    if raw is not None:
        key = hmac.new(capability.encode("ascii"), spec["source_sha256"].encode("ascii"), hashlib.sha256).hexdigest()[:32]
        spec["upload_id"] = key
        upload = {"root": str(spool), "key": key}
    pending = {"schema": SCHEMA, "spec": spec, "capability": capability, "request": request,
               "endpoint": {"socket": str(client.path), "uid": client.uid}, "upload": upload}
    require(len(canonical(pending)) <= 16384, "RESOURCE_LIMIT", "saved submission exceeds bound")
    directory.mkdir(mode=0o700)
    publish_record(directory, "submission.json", pending)
    sync_directory(directory.parent)
    if raw is not None:
        write_private(spool / (upload["key"] + ".pdb"), raw)
        sync_directory(spool)
    return recover(argparse.Namespace(job_dir=directory))


def recover(args):
    directory, pending = read_pending(args.job_dir)
    complete = directory / "job.json"
    if complete.exists():
        return accepted_job(directory, pending)
    staged_input(pending)
    client = QueueClient(pending["endpoint"]["socket"], coordinator_uid=pending["endpoint"]["uid"])
    job_id = client.submit(pending["spec"], capability=pending["capability"], request=pending["request"])
    publish_record(directory, "job.json", {"schema": SCHEMA, "id": job_id,
                   "submission_sha256": digest(canonical(pending))})
    return {"id": job_id, "submission": "accepted", "job_directory": str(directory)}


def redact(value, capability):
    if isinstance(value, str):
        return value.replace(capability, "[redacted]")
    if isinstance(value, list):
        return [redact(item, capability) for item in value]
    if isinstance(value, dict):
        return {key: redact(item, capability) for key, item in value.items()}
    return value


def status(args):
    directory, pending = read_pending(args.job_dir)
    accepted = accepted_job(directory, pending)
    client = QueueClient(pending["endpoint"]["socket"], coordinator_uid=pending["endpoint"]["uid"])
    job = client.status(accepted["id"], capability=pending["capability"])
    require(job is not None, "JOB_NOT_FOUND", "job is unavailable to these credentials")
    return redact(job, pending["capability"])


def accepted_job(directory, pending):
    # Read only. Missing or interrupted job receipts cannot trigger a submission.
    data = record(directory / "job.json")
    require(isinstance(data, dict) and set(data) == {"schema", "id", "submission_sha256"}
            and data["schema"] == SCHEMA and identifier(data["id"], 32)
            and data["submission_sha256"] == digest(canonical(pending)),
            "JOB_CREDENTIALS_INVALID", "invalid accepted job receipt")
    return {"id": data["id"], "submission": "accepted", "job_directory": str(directory)}
