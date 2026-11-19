"""DREAMM commands for local science, verification and explicit server tooling."""
import argparse
from collections import Counter
import json
import os
import pkgutil
from pathlib import Path
import re
import sys
import subprocess
import tarfile

from . import __version__
from dreamm_m1.capture import _json, _parents, _read, validate_capture
from dreamm_m1.features import Profile
from dreamm_m1.identity import canonical, digest, require, sha
from dreamm_m1.reference import verify_reference_assets

SOURCE_LIMIT = 8 * 1024**2
SCHEMA = "dreamm-standalone-request/1"
ID_MODES = ("M1", "M3", "M5")


def read_input(path, limit=SOURCE_LIMIT):
    path = Path(path).absolute()
    _parents(path.parent)
    raw = _read(path, limit)
    require(raw, "SOURCE_INVALID", "empty input")
    return raw


def source_bytes(args):
    from dreamm_m1.local_budget import source_bytes as admitted_source_bytes
    raw = read_input(args.input, admitted_source_bytes() if getattr(args, 'trusted_input', False) else SOURCE_LIMIT)
    require(digest(raw) == sha(args.sha256), "SOURCE_INVALID", "input SHA-256 differs")
    return raw


def check_selection(mode, fmt, pdb_id, chains, additional, *, trusted_input=False):
    require(mode in ("M1", "M2", "M3", "M4", "M5"), "REQUEST_INVALID", "unknown mode")
    if trusted_input:
        require(fmt in ("pdb", "mmcif") and (pdb_id is None or (
            fmt == "mmcif" and re.fullmatch(r"[1-9][A-Za-z0-9]{3}", pdb_id, flags=re.ASCII))),
            "REQUEST_INVALID", "invalid structure format or PDB entry identity")
    elif mode in ID_MODES:
        require(fmt == "mmcif" and isinstance(pdb_id, str)
                and re.fullmatch(r"[1-9][A-Za-z0-9]{3}", pdb_id, flags=re.ASCII),
                "REQUEST_INVALID", "ID modes require a PDB ID and frozen full-entry mmCIF")
    else:
        require(fmt == "pdb" and pdb_id is None and chains is None,
                "REQUEST_INVALID", "upload modes accept PDB without an ID or chain selector")
    require(chains is None or (isinstance(chains, list) and chains
            and len(set(chains)) == len(chains) and all(isinstance(c, str)
            and 0 < len(c) <= 256 and not any(ord(char) < 32 for char in c) for c in chains)),
            "REQUEST_INVALID", "unique author chain identifiers required")
    require((mode == "M5" and type(additional) is int and 1 <= additional <= 50)
            or (mode != "M5" and additional is None),
            "REQUEST_INVALID", "M5 requests 1 to 50 additional conformations only")


def request_data(args, raw):
    check_selection(args.mode, args.format, args.pdb_id, args.chains, args.additional,
                    trusted_input=getattr(args, 'trusted_input', False))
    return {"schema": SCHEMA, "scientific_contract": "dreamm-scientific/1.0.0",
            "mode": args.mode, "pdb_id": args.pdb_id.upper() if args.pdb_id else None,
            "source": {"path": "source.cif" if args.format == "mmcif" else "source.pdb",
                       "format": args.format, "sha256": digest(raw), "bytes": len(raw)},
            "chain_selection": None if args.chains is None else
                {"namespace": "auth_asym_id", "ids": args.chains},
            "reference_frame_index": 0, "n_additional": args.additional,
            "analysis_frame_count": args.additional + 1 if args.mode == "M5" else
                (1 if args.mode in ("M1", "M2") else None),
            "state": "prepared_not_executed", "source_parse_status": "pending",
            "scientific_prediction_performed": False}


def write_private(path, raw):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def sync_directory(path):
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def prepare(args, *, raw=None):
    raw = source_bytes(args) if raw is None else raw
    receipt = request_data(args, raw)
    encoded = canonical(receipt)
    require(len(encoded) <= 16384, "RESOURCE_LIMIT", "request manifest exceeds 16 KiB")
    target = Path(args.output).absolute()
    _parents(target.parent)
    target.mkdir(mode=0o700)
    # A failed write preserves an incomplete directory. Only request.json commits it.
    write_private(target / receipt["source"]["path"], raw)
    write_private(target / ".request.pending", encoded)
    os.rename(target / ".request.pending", target / "request.json")
    sync_directory(target)
    sync_directory(target.parent)
    return receipt


def verify_request(args):
    target = Path(args.directory).absolute()
    _parents(target)
    receipt = _json(_read(target / "request.json", 16384))
    require(isinstance(receipt, dict) and receipt.get("schema") == SCHEMA,
            "REQUEST_INVALID", "unsupported request")
    source = receipt["source"]
    require(source["format"] in ("pdb", "mmcif")
            and source["path"] == ("source.cif" if source["format"] == "mmcif" else "source.pdb")
            and set(p.name for p in target.iterdir()) == {source["path"], "request.json"},
            "REQUEST_INVALID", "unexpected input path or incomplete request directory")
    from dreamm_m1.local_budget import source_bytes as admitted_source_bytes
    trusted_input = getattr(args, 'trusted_input', False)
    raw = read_input(target / source["path"], admitted_source_bytes() if trusted_input else SOURCE_LIMIT)
    selection = receipt["chain_selection"]
    require(selection is None or (isinstance(selection, dict)
            and set(selection) == {"namespace", "ids"}
            and selection["namespace"] == "auth_asym_id"), "REQUEST_INVALID", "chain namespace")
    expected = request_data(argparse.Namespace(mode=receipt["mode"], format=source["format"],
        pdb_id=receipt["pdb_id"], chains=selection["ids"] if selection else None,
        additional=receipt["n_additional"], trusted_input=trusted_input), raw)
    require(canonical(receipt) == canonical(expected), "REQUEST_INVALID", "request data or source bytes differ")
    return {"schema": "dreamm-standalone-request-verification/1", "verified": True,
            "request_sha256": digest(canonical(receipt)), "source_sha256": digest(raw),
            "scientific_prediction_performed": False}


def inspect_source(args):
    raw = source_bytes(args)
    if args.format == "pdb":
        from dreamm_m1.pdb_input import read_pdb_source
        source = read_pdb_source(raw, expected_sha256=args.sha256)
    else:
        from dreamm_m1.mmcif_input import read_mmcif_source
        source = read_mmcif_source(raw, expected_sha256=args.sha256)
    models = []
    for index, model in enumerate(source.model_ids):
        atoms = [a for a in source.atoms if a.model_index == index]
        residues = {a.residue.uid: a.residue for a in atoms}
        models.append({"source_model_index": index, "source_model_id": model,
                       "atoms": len(atoms), "residues_including_nonprotein": len(residues),
                       "residues_per_author_chain": dict(Counter(r.auth_chain for r in residues.values())),
                       "residue_identity_sha256": digest(canonical([vars(r) for r in residues.values()])),
                       "reference_selected": index == 0})
    return {"schema": "dreamm-standalone-source-inspection/1", "source_sha256": source.sha256,
            "source_bytes": len(raw), "source_models": models, "reference_frame_index": 0,
            "record_counts": dict(Counter(a.record for a in source.atoms)),
            "scientific_prediction_performed": False}


def compatibility():
    return pkgutil.get_data("dreamm", "data/compatibility-profile.json")


def profiles(args):
    profile = Profile.owner_selected(compatibility())
    return {"version": __version__, "model_sha256": profile.model_sha256,
            "reference_sha256": profile.reference_sha256, "ordered_features": len(profile.names),
            "ordered_names_sha256": profile.names_sha256, "zero_filled_columns": len(profile.zeroed),
            "hhblits": {"version": "3.3.0", "database": "UniClust30 2018_08 HH-suite",
                        "threads": 15, "scientific_arguments": ["-M", "first", "-n", "3", "-v", "0"]},
            "ensemble": {"p2rank": "2.5.1", "production_profile_selection": "pending",
                         "chimera": "1.19", "exprose_weight": "0.4", "n_is_additional": True},
            "native_launcher": "modern_trusted_local",
            "native_scope": "bounded_single_chain_reference",
            "validated_examples": {"M1/M2": "1RLW", "M3/M4": "2KS4", "M5": "1RLW; n=2 science, n=1 generation"}}


def verify_reference(args):
    model = read_input(args.model, 64 * 1024**2)
    profile = Profile.owner_selected(compatibility())
    require(digest(model) == profile.model_sha256, "MODEL_IDENTITY_INVALID", "classifier SHA-256 differs")
    return verify_reference_assets(read_input(args.reference, 16 * 1024**2),
                                   read_input(args.zeroed, 32768), compatibility())


def parser():
    from . import jobs
    from .runtime import doctor, requirements
    from .installation import install
    from .modern import install_components
    from .modern_inference import infer_checkpoint
    result = argparse.ArgumentParser(prog="dreamm", description="DREAMM standalone development CLI")
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("profiles", help="show the pinned scientific contracts").set_defaults(action=profiles)
    prepare_parser = commands.add_parser("prepare", help="freeze a request and source bytes without running science")
    prepare_parser.add_argument("--mode", required=True, choices=("M1", "M2", "M3", "M4", "M5"))
    prepare_parser.add_argument("--pdb-id")
    prepare_parser.add_argument("--chains", nargs="+")
    prepare_parser.add_argument("--additional", type=int)
    prepare_parser.add_argument("--output", required=True)
    prepare_parser.set_defaults(action=prepare)
    inspect_parser = commands.add_parser("inspect", help="inspect all source models with the existing parser boundary")
    inspect_parser.set_defaults(action=inspect_source)
    for subparser in (prepare_parser, inspect_parser):
        subparser.add_argument("--input", required=True)
        subparser.add_argument("--sha256", required=True)
        subparser.add_argument("--format", required=True, choices=("pdb", "mmcif"))
    verify_parser = commands.add_parser("verify-request", help="verify a prepared request and exact source bytes")
    verify_parser.add_argument("directory")
    verify_parser.set_defaults(action=verify_request)
    reference_parser = commands.add_parser("verify-reference", help="verify model/reference/schema without model loading")
    for name in ("model", "reference", "zeroed"):
        reference_parser.add_argument("--" + name, required=True)
    reference_parser.set_defaults(action=verify_reference)
    capture_parser = commands.add_parser("verify-capture", help="validate an existing M1 capture through the original validator")
    capture_parser.add_argument("directory")
    for name in ("run-id", "attempt-id", "source-sha256"):
        capture_parser.add_argument("--" + name, required=True)
    capture_parser.set_defaults(action=lambda a: validate_capture(a.directory, run_id=a.run_id,
        attempt_id=a.attempt_id, source_sha256=a.source_sha256))
    runtime_parser = commands.add_parser("doctor-runtime", help="verify an installed compatibility runtime without executing it")
    runtime_parser.add_argument("--manifest", required=True)
    runtime_parser.add_argument("--sha256", required=True)
    runtime_parser.add_argument("--root", required=True)
    runtime_parser.set_defaults(action=doctor)
    requirements_parser = commands.add_parser("runtime-requirements", help="show modern or reference versions and asset fingerprints")
    requirements_parser.add_argument('--runtime-profile',choices=('modern','reference'),default='modern')
    requirements_parser.set_defaults(action=requirements)
    installation = commands.add_parser('install-runtime', help='offline install from a complete reviewed artifact lock; never run package hooks')
    for name in ('lock', 'sha256', 'artifacts', 'output'):
        installation.add_argument('--' + name, required=True)
    installation.set_defaults(action=install)
    components = commands.add_parser('install-components', help='offline modern component candidate; does not qualify or run M1')
    for name in ('manifest', 'sha256', 'wheelhouse', 'output'):
        components.add_argument('--' + name, required=True)
    components.set_defaults(action=lambda a: install_components(a.manifest, a.sha256, a.wheelhouse, a.output))
    inference = commands.add_parser('infer-checkpoint', help='real modern original-model inference from verified features; no fresh extraction')
    for name in ('checkpoint', 'sha256', 'model', 'reference', 'zeroed', 'output'):
        inference.add_argument('--' + name, required=True)
    inference.set_defaults(action=infer_checkpoint)
    export_parser=commands.add_parser('export',help='verify and export an already completed local M1 attempt as PDB/CSV/PML/ZIP')
    export_parser.add_argument('--attempt',required=True)
    export_parser.add_argument('--output',required=True)
    export_parser.set_defaults(action=export_completed)
    ref=commands.add_parser('export-reference',help='freeze a completed actual M1/M2 reference and Conservation for ensemble reuse')
    ref.add_argument('--attempt',required=True);ref.add_argument('--output',required=True)
    ref.set_defaults(action=export_reference)
    gen=commands.add_parser('export-generation',help='freeze a complete generator stage from a closed attempt, without rerunning it')
    gen.add_argument('--attempt',required=True);gen.add_argument('--output',required=True)
    gen.set_defaults(action=export_generation)
    local = commands.add_parser('run', help='direct local M1–M5 workflow')
    local.add_argument('--runtime-profile', choices=('modern','reference'), default='modern')
    local.add_argument('--mode', default='M1', choices=('M1','M2','M3','M4','M5'))
    local.add_argument('-i', '--source', help='PDB ID or local PDB/mmCIF file using the installed configuration')
    local.add_argument('-c', '--chains', nargs='+', help='author chain IDs, separated by spaces or commas')
    local.add_argument('--config', help='guided installer configuration (default ~/.config/dreamm/config.json)')
    local.add_argument('-p', '--processes', type=int, help='limit CPU affinity; scientific thread settings stay pinned')
    local.add_argument('--memory-mib', type=int, help='optional sampled aggregate RSS ceiling in MiB')
    local.add_argument('--source-mib', type=int, help='optional source byte limit in MiB')
    local.add_argument('--artifact-mib', type=int, help='optional per-file limit in MiB')
    local.add_argument('--scratch-mib', type=int, help='optional sampled scratch limit in MiB')
    local.add_argument('--stage-timeout', action='append', default=[], metavar='STAGE=SECONDS',
                       help='trusted-local deadline override; does not change scientific parameters')
    local.add_argument('--pdb-id')
    local.add_argument('--execution',choices=('trusted-local','bounded-worker'),default='trusted-local',
        help='trusted-local: private local inputs with sampled limits; bounded-worker: delegated cgroup isolation')
    for name in ('input', 'sha256', 'runtime-root', 'runtime-manifest', 'runtime-sha256','output'):
        local.add_argument('--' + name)
    for name in ('database','database-manifest','reference-checkpoint','reference-sha256',
                 'ensemble-runtime','ensemble-sha256','extensions','extensions-sha256',
                 'generation-runtime','generation-sha256','generation-checkpoint','generation-checkpoint-sha256'):
        local.add_argument('--'+name)
    local.add_argument('--ensemble-profile',choices=('default','alphafold'),default='default')
    local.add_argument('--additional',type=int)
    local.add_argument('--chimera', action='store_true', help='use a separately installed M5 Chimera fallback')
    local.add_argument('--seed',type=int,help='optional M5 ExProSE UInt64 seed, recorded in generation provenance')
    for name in ('source-packet','installation','installation-sha256','cgroup-root','launcher','launcher-sha256'):
        local.add_argument('--'+name)
    local.add_argument('--application', help='built zipapp; defaults to the running zipapp')
    local.add_argument('--timeout', type=int, default=18000)
    local.add_argument('--full-database-verify',action='store_true',help='force complete database SHA256 verification instead of unchanged installation receipt reuse')
    local.set_defaults(action=run_local)
    ensemble=commands.add_parser('analyze-ensemble',help='complete downstream science from verified frozen M1/P2Rank outputs; no search or classifier rerun')
    for name in ('checkpoint','sha256','runtime-root','runtime-manifest','runtime-sha256',
                 'extensions','extensions-sha256','output'):
        ensemble.add_argument('--'+name,required=True)
    ensemble.add_argument('--application')
    ensemble.add_argument('--timeout',type=int,default=3600)
    ensemble.set_defaults(action=analyze_ensemble)
    submit_parser = commands.add_parser("submit", help="submit to the existing local scientific coordinator")
    submit_parser.add_argument("--mode", choices=("M1", "M2", "M3", "M4", "M5"), required=True)
    submit_parser.add_argument("--pdb-id")
    submit_parser.add_argument("--chains", nargs="+")
    submit_parser.add_argument("--additional", type=int)
    submit_parser.add_argument("--input")
    submit_parser.add_argument("--sha256")
    submit_parser.add_argument("--spool")
    submit_parser.add_argument("--socket", required=True)
    submit_parser.add_argument("--coordinator-uid", type=int, required=True)
    submit_parser.add_argument("--job-dir", required=True)
    submit_parser.set_defaults(action=jobs.submit)
    for name, action in (("recover-submission", jobs.recover), ("status", jobs.status)):
        subparser = commands.add_parser(name)
        subparser.add_argument("job_dir")
        subparser.set_defaults(action=action)
    return result


def run_local(args):
    if getattr(args, 'source', None):
        from .configured import run
        return run(args)
    require(all(getattr(args, name, None) for name in
            ('input','sha256','runtime_root','runtime_manifest','runtime_sha256','output')),
            'REQUEST_INVALID','provide -i PDB_ID_OR_FILE or the complete expert runtime arguments')
    if args.execution == 'trusted-local':
        from .process import requested_budget
        with requested_budget(args):
            return _run_worker(args)
    return _run_worker(args)


def _run_worker(args):
    require(not getattr(args, 'chimera', False) or args.mode == 'M5', 'REQUEST_INVALID', '--chimera is M5 only')
    if args.mode in ('M3','M4','M5'):
        require(args.execution=='trusted-local' and args.runtime_profile=='modern',
                'LOCAL_MODE_UNQUALIFIED','standalone ensemble mode requires the explicit modern trusted-local boundary')
        require(not any(getattr(args,n,None) for n in ('cgroup_root','installation','installation_sha256','source_packet','launcher','launcher_sha256')),
                'LOCAL_ADMISSION_FAILED','never downgrade bounded-worker admission')
        require(all(getattr(args,n,None) for n in ('reference_checkpoint','reference_sha256','ensemble_runtime',
                'ensemble_sha256','extensions','extensions_sha256')),'LOCAL_ADMISSION_FAILED','verified M1 reference and ensemble dependencies required')
        require(sys.platform=='linux','LOCAL_UNAVAILABLE','standalone ensemble execution requires Linux')
        if args.mode=='M5':
            require(args.generation_runtime and args.generation_sha256 and type(args.additional) is int
                and 1<=args.additional<=50,'GENERATION_RUNTIME_UNQUALIFIED','M5 needs its qualified generation runtime and n=1..50')
            require(args.seed is None or 0<=args.seed<2**64,'REQUEST_INVALID','ExProSE seed must be a UInt64')
        else:
            require(args.additional is None and args.seed is None,'REQUEST_INVALID','generation options are M5 only')
        from .ensemble_local import run
        args.fresh_ensemble=True
        return run(args)
    require(args.database and args.database_manifest,'DATABASE_UNVERIFIED','M1/M2 require the exact local UniClust30 installation')
    require(args.additional is None and args.seed is None,'REQUEST_INVALID','generation options are M5 only')
    if args.execution == 'trusted-local':
        require(sys.platform=='linux','LOCAL_UNAVAILABLE','trusted-local execution requires Linux')
        require(not any(getattr(args,name,None) for name in ('cgroup_root','installation','installation_sha256','source_packet','launcher','launcher_sha256')),
            'LOCAL_ADMISSION_FAILED','bounded-worker arguments require explicit --execution bounded-worker; never downgrade isolation')
        from .trusted import run
    else:
        require(not getattr(args,'chains',None) and not getattr(args,'stage_timeout',None)
                and not any(getattr(args, key, None) is not None for key in
                            ('processes', 'memory_mib', 'source_mib', 'artifact_mib', 'scratch_mib')),
                'LOCAL_ADMISSION_FAILED','trusted-local settings require trusted-local execution')
        require(all(getattr(args,name,None) for name in ('source_packet','installation','installation_sha256',
            'cgroup_root','launcher','launcher_sha256')), 'LOCAL_ADMISSION_FAILED','bounded-worker requires the complete original admission arguments')
        from .local import run
    return run(args)


def stage_timeouts(args, defaults):
    requested = {}
    for entry in getattr(args, 'stage_timeout', []):
        name, sep, seconds = entry.partition('=')
        require(sep and name in defaults and name not in requested and seconds.isascii() and seconds.isdecimal()
                and len(seconds)<=9 and 1<=int(seconds)<=args.timeout,
                'REQUEST_INVALID','stage deadline must name a known stage and fit the job deadline')
        requested[name] = int(seconds)
    return {**defaults,**requested}


def export_completed(args):
    from .exports import export
    return export_attempt(args,export)


def analyze_ensemble(args):
    require(sys.platform=='linux','LOCAL_UNAVAILABLE','standalone ensemble execution requires Linux')
    from .ensemble_local import run
    from .process import requested_budget
    with requested_budget(args):
        return run(args)


def export_reference(args):
    from .reference_export import export
    return export_attempt(args,export)


def export_generation(args):
    from .generation import export_checkpoint
    return export_attempt(args,export_checkpoint)


def export_attempt(args, action):
    attempt=Path(args.attempt).absolute()
    receipt=attempt/'run.json'
    if not receipt.is_file():
        receipt=attempt/'admission.json'
    if not receipt.is_file():
        return action(args)
    _parents(attempt)
    config=_json(_read(receipt,65536))
    if config.get('schema') not in ('dreamm-trusted-admission/1', 'dreamm-local-run/1') or 'artifact_bytes' not in config:
        return action(args)
    from dreamm_m1.local_budget import local_budget
    with local_budget(config['source_bytes'],config['artifact_bytes'],config['scratch']):
        return action(args)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = args.action(args)
    except KeyboardInterrupt:
        print('DREAMM interrupted', file=sys.stderr)
        return 130
    except (ValueError, OSError, KeyError, TypeError, IndexError, RecursionError,
            subprocess.SubprocessError, tarfile.TarError) as exc:
        print(json.dumps({"ok": False, "error": getattr(exc, "code", "INPUT_INVALID"),
                          "detail": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    return 0
