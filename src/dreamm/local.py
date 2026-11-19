"""One foreground local attempt: bubblewrap + delegated cgroup v2, no service.

This is not a replacement scheduler. A private output lock admits one attempt;
the namespace contains children and the kernel enforces aggregate resources.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
if os.name == 'posix':
    import fcntl

from .cli import prepare, read_input, sync_directory, write_private
from .runtime import runtime_bundle
from dreamm_m1.capture import _json, _parents, _read
from dreamm_m1.identity import canonical, digest, require
from dreamm_m1.mmcif_input import CIF_1RLW_SHA256
from dreamm_m1.rocky_database import FINGERPRINT
from dreamm_m1.source_packet import load_1rlw_source_packet

MEMORY = 12 * 1024**3
SCRATCH = 512 * 1024**2
OUTPUT = 128 * 1024**2
BOOTSTRAP = '''import ctypes,json,os,sys
if ctypes.CDLL(None,use_errno=True).prctl(38,1,0,0,0) != 0:
    raise SystemExit(71)
print(json.dumps({"bootstrap_pid":os.getpid()}),flush=True)
if sys.stdin.readline(128) != sys.argv[1]+"\\n":
    raise SystemExit(71)
os.execv(sys.argv[2],sys.argv[2:])
'''


def verify_launcher(path, expected):
    require(digest(_read(path, 1024**2)) == expected, 'LOCAL_LAUNCHER_UNVERIFIED', 'trusted bubblewrap executable differs')
    help_result = subprocess.run([str(path), '--help'], capture_output=True, timeout=5, check=False)
    raw = help_result.stdout + help_result.stderr
    require(help_result.returncode == 0 and len(raw) <= 65536
            and all(flag in raw for flag in (b'--size', b'--perms', b'--unshare-all', b'--die-with-parent')),
            'LOCAL_LAUNCHER_UNSUPPORTED', 'bubblewrap must support bounded tmpfs --size; no unbounded fallback')


def delegated_root(path):
    root = Path(path).absolute()
    _parents(root)
    require(root != Path('/sys/fs/cgroup') and Path('/sys/fs/cgroup') in root.parents
            and root.stat().st_uid == os.geteuid() and os.access(root, os.W_OK),
            'LOCAL_CGROUP_UNAVAILABLE', 'supply a private delegated cgroup v2 directory owned by this user')
    mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    require(any(line.split(' - ')[1].split()[0] == 'cgroup2' and line.split(' - ')[0].split()[4] == '/sys/fs/cgroup'
                for line in mounts), 'LOCAL_CGROUP_UNAVAILABLE', 'cgroup v2 mount is unavailable')
    require({'cpu', 'memory', 'pids'} <= set((root / 'cgroup.subtree_control').read_text().split()),
            'LOCAL_CGROUP_UNAVAILABLE', 'cpu, memory and pids controllers must already be delegated/enabled')
    return root


class LocalGroup:
    def __init__(self, parent, attempt):
        require(len(attempt) == 32 and all(c in '0123456789abcdef' for c in attempt), 'LOCAL_INVALID', 'attempt identity')
        # Atomic kernel-directory creation is the one local scientific slot.
        # A leftover slot is never reclaimed on a timer or heartbeat expiry.
        self.path = parent / 'dreamm-local-slot'
        self.created = False

    def create(self):
        self.path.mkdir(mode=0o700)
        self.created = True
        for name, value in {'memory.max': str(MEMORY), 'memory.swap.max': '0',
                            'pids.max': '128', 'cpu.max': '1500000 100000'}.items():
            (self.path / name).write_text(value)
            require((self.path / name).read_text().strip() == value,
                    'LOCAL_CGROUP_UNAVAILABLE', 'effective cgroup limit differs: ' + name)
        require((self.path / 'cgroup.kill').is_file(), 'LOCAL_CGROUP_UNAVAILABLE', 'kernel cgroup.kill is required')

    def join(self, pid):
        (self.path / 'cgroup.procs').write_text(str(pid))
        require(str(pid) in (self.path / 'cgroup.procs').read_text().split(), 'LOCAL_CGROUP_UNAVAILABLE', 'atomic pre-exec placement failed')

    def terminate(self):
        if not self.created or not self.path.exists():
            return
        if (self.path / 'cgroup.procs').read_text().strip():
            (self.path / 'cgroup.kill').write_text('1')
        end = time.monotonic() + 15
        while (self.path / 'cgroup.events').read_text().splitlines().count('populated 0') != 1:
            require(time.monotonic() < end, 'LOCAL_CLEANUP_UNPROVEN', 'cgroup remained populated; no success publication')
            time.sleep(0.05)
        require(not (self.path / 'cgroup.procs').read_text().strip(), 'LOCAL_CLEANUP_UNPROVEN', 'worker tasks remain')


def resource_preflight():
    require(sys.platform == 'linux' and platform.machine() == 'x86_64' and os.geteuid() != 0,
            'LOCAL_UNAVAILABLE', 'non-root Linux x86-64 required')
    require(os.cpu_count() == 16, 'SCIENTIFIC_RUNTIME_MISMATCH', 'unchanged HHblits requires 16 visible CPUs and 15 threads')
    mem = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
           if line.split(':')[0] in ('MemAvailable', 'SwapTotal')}
    require(mem['MemAvailable'] >= MEMORY + 2 * 1024**3,
            'LOCAL_RESOURCES_UNAVAILABLE', 'need at least 14 GiB available RAM; worker swap is independently disabled by memory.swap.max')
    return mem


def launch_argv(launcher, runtime, inputs, app, *, modern=False):
    argv = [str(launcher), '--unshare-all', '--uid', '1000', '--gid', '1000', '--cap-drop', 'ALL',
            '--die-with-parent', '--new-session', '--clearenv', '--ro-bind', str(runtime / 'rootfs'), '/',
            '--ro-bind', str(runtime), '/runtime', '--ro-bind', str(inputs), '/inputs',
            '--ro-bind', str(app), '/code/dreamm.pyz', '--proc', '/proc', '--dev', '/dev',
            '--perms', '1777', '--size', str(SCRATCH), '--tmpfs', '/work', '--remount-ro', '/',
            '--setenv', 'HOME', '/nonexistent', '--setenv', 'TMPDIR', '/work/tmp',
            '--setenv', 'PATH', '/runtime/science/bin:/usr/bin:/bin', '--setenv', 'LC_ALL', 'C',
            '--chdir', '/work', '--', '/runtime/science/bin/'+('python3.14' if modern else 'python3.7'), '-I', '-S', '-B',
            '-c', "import sys;sys.path.insert(0,'/code/dreamm.pyz');from dreamm.local_entry import main;main()"]
    if modern:
        position = argv.index('--chdir')
        argv[position:position] = ['--setenv','OPENBLAS_NUM_THREADS','1','--setenv','MKL_NUM_THREADS','1']
    return argv


def receive(child, output, deadline, oom_baseline):
    count, error_count, low_memory, next_sample = 0, 0, 0, 0
    with selectors.DefaultSelector() as watcher, output.open('xb') as stream:
        watcher.register(child.stdout, selectors.EVENT_READ, 'out')
        watcher.register(child.stderr, selectors.EVENT_READ, 'err')
        with output.with_suffix('.stderr').open('xb') as errors:
            while watcher.get_map():
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'LOCAL_TIMEOUT', 'total local deadline exceeded')
                if time.monotonic() >= next_sample:
                    vm = dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
                    require('oom_kill' in vm and int(vm['oom_kill']) == oom_baseline,
                            'LOCAL_HOST_PRESSURE', 'host OOM counter changed or monitoring became unavailable')
                    available = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                                     if line.startswith('MemAvailable:'))
                    low_memory = low_memory + 1 if available < 2 * 1024**3 else 0
                    require(available >= 1024**3 and low_memory < 2, 'LOCAL_HOST_PRESSURE', 'insufficient host RAM; aborting complete cgroup')
                    next_sample = time.monotonic() + 5
                events = watcher.select(min(remaining, 1))
                for key, _ in events:
                    raw = os.read(key.fileobj.fileno(), 65536)
                    if not raw:
                        watcher.unregister(key.fileobj)
                    elif key.data == 'out':
                        count += len(raw)
                        require(count <= OUTPUT, 'RESOURCE_LIMIT', 'worker output archive bound')
                        stream.write(raw)
                    else:
                        error_count += len(raw)
                        require(error_count <= 2 * 1024**2, 'RESOURCE_LIMIT', 'worker diagnostic bound')
                        errors.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    require(child.wait(timeout=max(1, deadline - time.monotonic())) == 0, 'LOCAL_WORKER_FAILED', 'inspect worker.stderr')


IDENTITY_FIELDS = ('attempt', 'source_sha256', 'runtime_sha256', 'application_sha256')


def unpack_results(path, folder, identity):
    total, seen = 0, set()
    with tarfile.open(path, 'r:') as archive:
        for member in archive:
            parts = Path(member.name).parts
            require(parts and len(parts) <= 12 and len(member.name) <= 512
                    and not any(ord(c) < 32 for c in member.name)
                    and not Path(member.name).is_absolute() and '..' not in parts and '\\' not in member.name
                    and member.name not in seen and len(seen) < 512 and member.isfile()
                    and 0 <= member.size <= 8 * 1024**2, 'LOCAL_OUTPUT_INVALID', 'unexpected output member')
            seen.add(member.name)
            total += member.size
            require(total <= OUTPUT, 'RESOURCE_LIMIT', 'expanded output bound')
            target = folder / member.name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            raw = archive.extractfile(member).read(member.size + 1)
            require(len(raw) == member.size, 'LOCAL_OUTPUT_INVALID', 'truncated output member')
            write_private(target, raw)
        end_offset = archive.offset
    with path.open('rb') as stream:
        stream.seek(end_offset)
        tail = stream.read(1024)
        require(len(tail) == 1024 and not any(tail), 'LOCAL_OUTPUT_INVALID', 'missing tar completion blocks')
        for chunk in iter(lambda: stream.read(65536), b''):
            require(not any(chunk), 'LOCAL_OUTPUT_INVALID', 'unexpected trailing archive data')
    manifest = _json(_read(folder / 'artifacts.json'))
    require(set(manifest) == {'schema', 'files', *IDENTITY_FIELDS} and manifest['schema'] == 'dreamm-local-artifacts/1'
            and all(manifest[key] == identity[key] for key in IDENTITY_FIELDS)
            and set(manifest['files']) == seen - {'artifacts.json'}, 'LOCAL_OUTPUT_INVALID', 'output inventory differs')
    for name, row in manifest['files'].items():
        raw = _read(folder / name)
        require(isinstance(row, dict) and type(row.get('bytes')) is int
                and row == {'bytes': len(raw), 'sha256': digest(raw)}, 'LOCAL_OUTPUT_INVALID', 'output checksum differs')
    return manifest


def validate_completion(results, config):
    from dreamm_m1.inference import residue_rows, verify_membrane_reference
    required = ('extraction/adapter_map.json', 'extraction/unscaled.json', 'extraction/join.json',
                'extraction/timings.json', 'prediction/membrane.csv', 'prediction/labels.json',
                'prediction/scaling.json', 'prediction/timings.json')
    for name in required:
        require((results / 'scientific' / name).is_file(), 'LOCAL_OUTPUT_INVALID', 'mandatory scientific output missing: ' + name)
    if config.get('runtime_profile') == 'modern':
        modern_required = ('charges.pqr','before.pdb','prepared.pdb','prepared.cif','pre-ca.json',
            'preparation.json','preparation-map.json','charge-map.json','dssp.stdout','surface.rsa',
            'msms-surface.face','msms-surface.vert','mesh.json','output.hhm','query.json',
            'python-features.csv','dynamics.json','descriptor-stage.json','descriptors/Outputs/reference/complete_AA.txt')
        for name in modern_required:
            require((results/'scientific/extraction'/name).is_file(),
                    'LOCAL_OUTPUT_INVALID','mandatory modern scientific artifact missing: '+name)
        require((results/'scientific/prediction/bridge.json').is_file(),
                'LOCAL_OUTPUT_INVALID','original classifier bridge receipt missing')
        require((results/'scientific/scientific-profile.json').is_file(),
                'LOCAL_OUTPUT_INVALID','modern scientific profile missing')
        from dreamm_m1.source_packet import _HASHES
        for name,pin in _HASHES.items():
            require(digest(_read(results/'scientific/source-packet'/name)) == pin,
                    'LOCAL_OUTPUT_INVALID','retained immutable source receipt missing/changed')
    summary = _json(_read(results / 'prediction-summary.json', 16384))
    require(summary.get('schema') == 'dreamm-local-prediction/1' and summary.get('native_complete') is True
            and summary.get('source_sha256') == config['source_sha256']
            and summary.get('attempt') == config['attempt'], 'LOCAL_OUTPUT_INVALID', 'scientific completion identity differs')
    residues = residue_rows({'adapter_map': _read(results / 'scientific/extraction/adapter_map.json')})
    calls, _ = verify_membrane_reference(_read(results / 'scientific/prediction/membrane.csv'), residues)
    labels = _json(_read(results / 'scientific/prediction/labels.json'))
    require(isinstance(labels, dict) and set(labels) <= set(residues)
            and all(type(value) is int and value in (0, 1) for value in labels.values())
            and all(type(summary.get(key)) is int for key in ('residues', 'eligible_residues', 'membrane_calls'))
            and summary['residues'] == len(residues) and summary['eligible_residues'] == len(labels)
            and summary['membrane_calls'] == len(calls)
            and summary['outcome'] == ('complete_positive' if calls else 'complete_scientific_empty'),
            'LOCAL_OUTPUT_INVALID', 'scientific summary differs from its complete artifacts')
    return summary


def run(args):
    resource_preflight()
    require(type(args.timeout) is int and 60 <= args.timeout <= 18000, 'LOCAL_INVALID', 'local timeout must be 60..18000 seconds')
    require(args.mode == 'M1' and args.pdb_id == '1RLW', 'LOCAL_MODE_UNQUALIFIED', 'dev3 direct execution initially admits frozen M1 1RLW only')
    root = delegated_root(args.cgroup_root)
    runtime = Path(args.runtime_root).absolute()
    bundle_raw = _read(Path(args.runtime_manifest).absolute())
    bundle = runtime_bundle(bundle_raw, expected_sha256=args.runtime_sha256)
    modern = getattr(args,'runtime_profile','modern') == 'modern'
    require(bundle.schema == ('dreamm-modern-runtime/1' if modern else 'dreamm-standalone-runtime/1'),
            'SCIENTIFIC_RUNTIME_MISMATCH','requested runtime profile and manifest differ; no fallback')
    installation_raw = _read(Path(args.installation).absolute())
    installation = _json(installation_raw)
    require(digest(installation_raw) == args.installation_sha256
            and installation.get('schema') == 'dreamm-runtime-installation/1'
            and installation.get('runtime_manifest_sha256') == args.runtime_sha256
            and installation.get('package_hooks_executed') is False,
            'LOCAL_RUNTIME_INCOMPLETE', 'reviewed clean offline installation receipt required')
    bundle.verify(runtime)
    require((runtime / 'rootfs').is_dir(), 'LOCAL_RUNTIME_INCOMPLETE', 'clean rootfs and native-library closure are missing')
    for directory in ('code', 'inputs', 'runtime', 'proc', 'dev', 'work', 'run', 'run/dreamm-cgroup', 'database'):
        _parents(runtime / 'rootfs' / directory)
    require((runtime / 'rootfs/code/dreamm.pyz').is_file(), 'LOCAL_RUNTIME_INCOMPLETE', 'read-only application mountpoint missing')
    require(bundle.data['roles']['science_python'] == 'science/bin/'+('python3.14' if modern else 'python3.7'),
            'LOCAL_RUNTIME_INCOMPLETE', 'fixed local interpreter layout required')
    source = read_input(args.input)
    require(digest(source) == args.sha256 == CIF_1RLW_SHA256, 'SOURCE_INVALID', 'exact frozen 1RLW mmCIF required')
    packet = Path(args.source_packet).absolute()
    require(load_1rlw_source_packet(packet).sha256 == CIF_1RLW_SHA256, 'SOURCE_INVALID', 'qualified identity packet differs')
    database = Path(args.database).absolute()
    _parents(database)
    database_manifest = _read(Path(args.database_manifest).absolute())
    require(_json(database_manifest).get('fingerprint') == FINGERPRINT, 'DATABASE_UNVERIFIED', 'pinned UniClust30 manifest required')
    launcher = Path(args.launcher).absolute()
    verify_launcher(launcher, args.launcher_sha256)
    app = Path(args.application or getattr(__loader__, 'archive', '')).absolute()
    require(app.is_file() and app.suffix == '.pyz', 'LOCAL_INVALID', 'run the built zipapp or supply its --application path')
    _parents(app.parent)
    app_raw = _read(app, 4 * 1024**2)
    target = Path(args.output).absolute()
    _parents(target.parent)
    require(shutil.disk_usage(target.parent).free >= OUTPUT + 64 * 1024**2,
            'LOCAL_RESOURCES_UNAVAILABLE', 'reserve at least 192 MiB for retained output and diagnostics')
    target.mkdir(mode=0o700)
    attempt = uuid.uuid4().hex
    group = LocalGroup(root, attempt)
    child = None
    old_handlers = {}
    with (target / 'attempt.lock').open('xb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='dreamm-local-input-') as temp:
            inputs = Path(temp)
            prepare(argparse.Namespace(mode='M1', input=Path(args.input), format='mmcif', sha256=args.sha256,
                pdb_id='1RLW', chains=None, additional=None, output=inputs / 'request'))
            (inputs / 'source-packet').mkdir(mode=0o700)
            from dreamm_m1.source_packet import _HASHES
            for name in _HASHES:
                write_private(inputs / 'source-packet' / name, _read(packet / name))
            write_private(inputs / 'runtime.json', bundle_raw)
            write_private(inputs / 'database.json', database_manifest)
            argv = launch_argv(launcher, runtime, inputs, app, modern=modern)
            # Database is a read-only bind. No host credentials, home or socket is mounted.
            position = argv.index('--proc')
            argv[position:position] = ['--ro-bind', str(database), '/database']
            argv[position:position] = ['--ro-bind', str(group.path), '/run/dreamm-cgroup']
            config = {'schema': 'dreamm-local-admission/1', 'attempt': attempt, 'runtime_sha256': args.runtime_sha256,
                      'runtime_profile':'modern' if modern else 'reference',
                      'application_sha256': digest(app_raw), 'launcher_sha256': args.launcher_sha256,
                      'cgroup': str(group.path), 'memory': MEMORY, 'tasks': 128, 'cpu_threads': 15,
                      'deadline_seconds': args.timeout, 'source_sha256': args.sha256,
                      'host_namespaces': {name: os.readlink('/proc/self/ns/' + name)
                                          for name in ('mnt', 'net', 'pid', 'user', 'ipc', 'uts')}}
            write_private(inputs / 'admission.json', canonical(config))
            write_private(target / 'attempt.json', canonical(config))
            try:
                for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    old_handlers[name] = signal.signal(name, lambda number, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
                group.create()
                resource_preflight()  # fresh headroom after potentially lengthy integrity checks
                vm = dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
                require('oom_kill' in vm, 'LOCAL_HOST_MONITOR_UNAVAILABLE', 'host OOM counter is required')
                oom_baseline = int(vm['oom_kill'])
                child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', BOOTSTRAP, attempt, *argv],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                    env={'PATH': os.defpath, 'LC_ALL': 'C'})
                with selectors.DefaultSelector() as wait:
                    wait.register(child.stdout, selectors.EVENT_READ)
                    require(wait.select(10), 'LOCAL_WORKER_FAILED', 'bootstrap did not become ready')
                line = child.stdout.readline(128)
                require(_json(line) == {'bootstrap_pid': child.pid}, 'LOCAL_WORKER_FAILED', 'bootstrap identity differs')
                group.join(child.pid)  # before bubblewrap or scientific code can execute
                child.stdin.write((attempt + '\n').encode('ascii'))
                child.stdin.flush()
                child.stdin.close()
                child.stdin = None
                receive(child, target / 'worker.tar', time.monotonic() + args.timeout, oom_baseline)
                group.terminate()
                require('oom_kill 0' in (group.path / 'memory.events').read_text().splitlines(), 'LOCAL_WORKER_FAILED', 'worker OOM event')
                metrics = {name: (group.path / name).read_text() for name in
                           ('memory.peak', 'memory.events', 'cpu.stat', 'io.stat', 'pids.events')}
                write_private(target / 'resources.json', canonical(metrics))
                group.path.rmdir()
                results = target / 'results'
                results.mkdir(mode=0o700)
                manifest = unpack_results(target / 'worker.tar', results, config)
                summary = validate_completion(results, config)
                complete = {'schema': 'dreamm-local-complete/1', 'attempt': attempt, 'source_sha256': args.sha256,
                            'artifacts_manifest_sha256': digest(canonical(manifest)), 'whole_tree_exit_verified': True,
                            'scientific_summary': summary}
                write_private(target / '.complete.pending', canonical(complete))
                os.rename(target / '.complete.pending', target / 'complete.json')
                sync_directory(target)
                return complete
            finally:
                try:
                    group.terminate()
                    if child is not None and child.poll() is None:
                        child.kill()
                        child.wait(timeout=10)
                    if group.created and group.path.exists():
                        group.path.rmdir()
                finally:
                    for name, handler in old_handlers.items():
                        signal.signal(name, handler)
