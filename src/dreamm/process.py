"""Ordinary Linux subprocess management for researcher-owned local runs."""
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from dreamm_m1.capture import _json, _read
from dreamm_m1.identity import canonical, require
from dreamm_m1.local_budget import MIB, UNLIMITED, artifact_bytes, local_budget, scratch_bytes, source_bytes

from .cli import write_private


def python_command(python, application, module, entry):
    return [str(python), '-I', '-S', '-B', '-c',
            f'import sys;sys.path.insert(0,sys.argv.pop(1));from {module} import {entry};{entry}()',
            str(application)]


def requested_budget(args):
    budget = [getattr(args, name, None) for name in ('source_mib', 'artifact_mib', 'scratch_mib')]
    return local_budget(*(UNLIMITED if value is None else value*MIB for value in budget))


def group_processes(group):
    members = {}
    for directory in Path('/proc').iterdir():
        if directory.name.isdecimal():
            try:
                fields = (directory/'stat').read_text().rsplit(')', 1)[1].split()
                if int(fields[2]) == group and fields[0] not in ('Z', 'X'):
                    members[int(directory.name)] = int(fields[21]) * os.sysconf('SC_PAGE_SIZE')
            except (OSError, IndexError, ValueError):
                continue  # process exited during sampling
    return members


def scratch_size(work):
    size = 0
    for directory, _, names in os.walk(work):
        for name in names:
            try:
                size += (Path(directory)/name).lstat().st_size
            except FileNotFoundError:
                pass
    return size


def stop_group(child):
    def send(signum):
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    send(signal.SIGTERM)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        child.poll()
        if not group_processes(child.pid):
            break
        time.sleep(0.05)
    if group_processes(child.pid):
        send(signal.SIGKILL)
        deadline = time.monotonic() + 3
        while group_processes(child.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
    child.wait(timeout=3)
    return sorted(group_processes(child.pid))


def launch(args, output, work, argv, config, *, threads):
    """Run one scientific subprocess group and keep partial output on failure."""
    require(sys.platform == 'linux' and os.geteuid() != 0, 'LOCAL_UNAVAILABLE',
            'use an ordinary, non-root Linux account')
    cpus = sorted(os.sched_getaffinity(0))
    count = getattr(args, 'processes', None)
    memory = getattr(args, 'memory_mib', None)
    require(count is None or 1 <= count <= len(cpus), 'RESOURCE_LIMIT',
            f'--processes must fit the {len(cpus)} available CPUs')
    require(args.timeout > 0 and (memory is None or memory > 0), 'RESOURCE_LIMIT',
            'memory and timeout must be positive')
    config.update(schema='dreamm-local-run/1', work=str(work), source_bytes=source_bytes(),
                  artifact_bytes=artifact_bytes(), scratch=scratch_bytes(), affinity=cpus[:count] if count else None)
    controller = output/'run.json'
    write_private(controller, canonical(config))
    home, tmp = output/'home', output/'tmp'
    home.mkdir(mode=0o700)
    tmp.mkdir(mode=0o700)
    env = {key: os.environ[key] for key in ('SYSTEMROOT', 'LANG') if key in os.environ}
    env.update(HOME=str(home), TMPDIR=str(tmp), XDG_CACHE_HOME=str(tmp), LC_ALL='C', PATH=os.defpath,
               OPENBLAS_NUM_THREADS=str(threads), OMP_NUM_THREADS=str(threads),
               DREAMM_LOCAL_BUDGET=f'{source_bytes()}:{artifact_bytes()}:{scratch_bytes()}')
    started = time.monotonic()
    peak = 0
    failure = None
    remaining = []

    def interrupted(signum, _frame):
        raise InterruptedError(f'local execution interrupted by signal {signum}')

    handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGHUP)}
    handlers[signal.SIGINT] = signal.getsignal(signal.SIGINT)
    try:
        with (output/'worker.stdout').open('xb') as stdout, (output/'worker.stderr').open('xb') as stderr:
            child = subprocess.Popen([*argv, str(controller)], cwd=work, env=env, start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            try:
                while child.poll() is None:
                    rss = sum(group_processes(child.pid).values())
                    peak = max(peak, rss)
                    require(memory is None or rss <= memory*MIB, 'RESOURCE_LIMIT', 'local RSS preference exceeded')
                    require(not getattr(args, 'scratch_mib', None) or scratch_size(work)+scratch_size(tmp) <= scratch_bytes(),
                            'RESOURCE_LIMIT', 'local scratch preference exceeded')
                    wait = args.timeout - (time.monotonic()-started)
                    require(wait > 0, 'RESOURCE_LIMIT', 'local execution deadline reached')
                    try:
                        child.wait(timeout=min(wait, 1))
                    except subprocess.TimeoutExpired:
                        continue
                require(child.returncode == 0, 'LOCAL_WORKER_FAILED',
                        f'scientific process exited {child.returncode}; see {output/"worker.stderr"}')
                require(not group_processes(child.pid), 'LOCAL_CLEANUP_FAILED', 'scientific process left descendants')
            except BaseException as exc:
                failure = str(exc) or type(exc).__name__
                raise
            finally:
                # A second catchable signal should not interrupt this cleanup.
                for sig in handlers:
                    signal.signal(sig, signal.SIG_IGN)
                remaining = stop_group(child)
                receipt = {'schema':'dreamm-local-process/1','process_group_cleanup_verified':not remaining,
                           'process_tree_remaining':remaining,'wall_seconds':time.monotonic()-started,
                           'peak_sampled_rss_sum_bytes':peak,'returncode':child.returncode,'failure':failure}
                write_private(output/'supervision.json', canonical(receipt))
    finally:
        for sig, previous in handlers.items():
            signal.signal(sig, previous)
    require(not remaining, 'LOCAL_CLEANUP_FAILED', 'local process group did not exit')
    return receipt


@contextmanager
def read_run():
    import resource
    config = _json(_read(Path(sys.argv[1]), 65536))
    require(config['schema'] == 'dreamm-local-run/1', 'LOCAL_INVALID', 'expected a local scientific run')
    if config['affinity']:
        os.sched_setaffinity(0, config['affinity'])
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with local_budget(config['source_bytes'], config['artifact_bytes'], config['scratch']):
        if artifact_bytes() < UNLIMITED:
            _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
            limit = artifact_bytes() if hard == resource.RLIM_INFINITY else min(hard, artifact_bytes())
            resource.setrlimit(resource.RLIMIT_FSIZE, (limit, hard))
        yield config
