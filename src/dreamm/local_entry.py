"""Fixed local child entry; run only by dreamm.local inside its admitted namespace."""
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import time

from dreamm_m1.capture import _json, _parents, _read
from dreamm_m1.identity import canonical, digest, require
from dreamm_m1 import execution_profiles as policy


class LocalProfile(policy.ExecutionProfile):
    """Name the one-shot parent truthfully while preserving all other gates."""
    def readiness(self):
        result = super().readiness()
        if self.data['containment'].get('authority') == 'local_cli_supervisor' and self.data['containment'].get('fallback') is None:
            result['missing_verification'] = [x for x in result['missing_verification'] if x != 'containment.authority_or_fallback']
            result['runnable'] = not result['missing_verification'] and result['scientific_execution_approved']
        return result


def enable_scientific_imports(root, roles, *, modern=False):
    """After closure verification, expose only its fixed site directory.

    -I -S remains in force: neither .pth hooks nor user sitecustomize execute.
    """
    version = '3.14' if modern else '3.7'
    require(roles['science_python'] == root / ('science/bin/python'+version),
            'LOCAL_RUNTIME_INCOMPLETE', 'scientific interpreter layout differs')
    site = root / ('science/lib/python'+version+'/site-packages')
    _parents(site)
    sys.path.append(str(site))
    return site


def environment(config):
    import resource
    runtime_profile = config.get('runtime_profile','reference')
    require(runtime_profile in ('modern','reference'), 'LOCAL_ADMISSION_FAILED', 'unknown runtime profile')
    expected = (3,14,10) if runtime_profile == 'modern' else (3,7,16)
    require(sys.platform == 'linux' and sys.version_info[:3] == expected and os.geteuid() == 1000,
            'LOCAL_ADMISSION_FAILED', 'non-root compatibility interpreter required')
    require(config['schema'] == 'dreamm-local-admission/1' and config['memory'] == 12884901888
            and config['tasks'] == 128 and config['cpu_threads'] == 15,
            'LOCAL_ADMISSION_FAILED', 'parent admission differs')
    require(set(config['host_namespaces']) == {'mnt', 'net', 'pid', 'user', 'ipc', 'uts'}
            and all(os.readlink('/proc/self/ns/' + name) != previous
                    for name, previous in config['host_namespaces'].items()),
            'LOCAL_ADMISSION_FAILED', 'a required process/filesystem/network namespace was not isolated')
    require(digest(_read(Path('/code/dreamm.pyz'))) == config['application_sha256'], 'LOCAL_ADMISSION_FAILED', 'application changed')
    flags = os.ST_RDONLY
    require(all(os.statvfs(path).f_flag & flags for path in ('/', '/runtime', '/inputs', '/database', '/run/dreamm-cgroup'))
            and not os.statvfs('/work').f_flag & flags,
            'LOCAL_ADMISSION_FAILED', 'read-only mounts or bounded work mount differ')
    require(os.statvfs('/work').f_blocks * os.statvfs('/work').f_frsize <= 512 * 1024**2,
            'LOCAL_ADMISSION_FAILED', 'scratch bound differs')
    import socket
    require({name for _, name in socket.if_nameindex()} <= {'lo'}, 'LOCAL_ADMISSION_FAILED', 'network isolation failed')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    require(status['NoNewPrivs'].strip() == '1' and all(int(status[k], 16) == 0 for k in ('CapEff', 'CapPrm', 'CapAmb')),
            'LOCAL_ADMISSION_FAILED', 'capability boundary differs')
    group = Path('/run/dreamm-cgroup')
    for name, value in {'memory.max': '12884901888', 'memory.swap.max': '0', 'pids.max': '128',
                        'cpu.max': '1500000 100000'}.items():
        require((group / name).read_text().strip() == value, 'LOCAL_ADMISSION_FAILED', 'effective resource mismatch')
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024**2, 8 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    config = _json(_read(Path('/inputs/admission.json'), 16384))
    environment(config)
    from dreamm.runtime import runtime_bundle
    modern = config.get('runtime_profile') == 'modern'
    if modern:
        from dreamm.modern_pipeline import run_reference, recipe_sha256
    else:
        from dreamm.worker import run_reference, recipe_sha256
    from dreamm.cli import compatibility
    from dreamm_m1.rocky_database import verify_database, PREFIX
    root = Path('/runtime')
    raw = _read(Path('/inputs/runtime.json'))
    bundle = runtime_bundle(raw, expected_sha256=config['runtime_sha256'])
    require(bundle.schema == ('dreamm-modern-runtime/1' if modern else 'dreamm-standalone-runtime/1'),
            'LOCAL_ADMISSION_FAILED', 'parent/child scientific runtime mismatch')
    bundle.verify(root)
    database = verify_database(Path('/database'), _json(_read(Path('/inputs/database.json'))))
    roles = {key: root / value for key, value in bundle.data['roles'].items()}
    enable_scientific_imports(root, roles, modern=modern)
    if modern:
        from dreamm.modern_runtime import verify_python_versions
        verify_python_versions('science')
        import subprocess
        charge_check = "import sys;from importlib.metadata import version;assert sys.version_info[:3]==(3,14,10);assert version('pdb2pqr')=='3.8.0';assert version('propka')=='3.5.1'"
        checked = subprocess.run([str(roles['charge_python']),'-I','-c',charge_check],
            stdin=subprocess.DEVNULL,capture_output=True,timeout=10)
        require(checked.returncode == 0,'SCIENTIFIC_RUNTIME_MISMATCH','separate modern charge environment differs')
        checked = subprocess.run([str(roles['java']),'-version'],stdin=subprocess.DEVNULL,capture_output=True,timeout=10)
        require(checked.returncode == 0 and len(checked.stdout)+len(checked.stderr) <= 16384
                and b'version "21.' in checked.stdout+checked.stderr,
                'SCIENTIFIC_RUNTIME_MISMATCH','reviewed Java21 runtime required')
    tools = {'preparation_charge': 'charge_python' if modern else 'pdb2pqr', 'preparation_structure': 'science_python', 'features': 'hhblits',
             'descriptors': 'protdcal_jar', 'classifier_compatibility': 'science_python'}
    profiles = {}
    deadlines = {'preparation_charge':180,'preparation_structure':360,'features':14400,
                 'descriptors':7200,'classifier_compatibility':180} if modern else {}
    for scope, role in tools.items():
        profiles[scope] = LocalProfile(canonical({
            'schema': 'dreamm-execution-profile/1', 'version': 1, 'profile_id': 'local-1rlw-' + scope,
            'scope': scope, 'modes': ['M1'], 'python': {'interpreter_sha256': digest(_read(roles['science_python'], 64 * 1024**2))},
            'tool': {'path': str(roles[role]), 'executable_sha256': digest(_read(roles[role], 64 * 1024**2)), 'version': 'pinned clean install'},
            'dependencies': {'lock_sha256': digest(_read(roles['dependency_lock']))},
            'parameters': {'recipe_sha256': recipe_sha256(), 'model_deserialization_authorized': scope == 'classifier_compatibility'},
            'inputs': {'fixture': {'schema': 'frozen-1RLW', 'sha256': config['source_sha256'], 'max_bytes': 8 * 1024**2}},
            'outputs': {'receipt': {'schema': 'dreamm-local-scientific-output/1', 'sha256': None, 'max_bytes': 8 * 1024**2}},
            'resources': {'whole_job_memory_enforced': True, 'whole_job_memory_bytes': config['memory'], 'CPU_threads': 15,
                          'process_limit': 128, 'wall_seconds': deadlines.get(scope,14400 if scope == 'features' else 90), 'output_budget_bytes': 512 * 1024**2},
            'containment': {'verified_profile_sha256': digest(canonical(config)), 'complete_tree_exit_required': True,
                            'authority': 'local_cli_supervisor', 'fallback': None}}))
    policy.APPROVED_SCIENTIFIC_PROFILES = frozenset(p.sha256 for p in profiles.values())
    Path('/work/tmp').mkdir(mode=0o700)
    # Native libraries cannot interleave stdout with the binary artifact stream.
    saved = os.dup(1)
    try:
        with Path('/work/scientific.stdout').open('wb') as log:
            os.dup2(log.fileno(), 1)
            prepared, prediction = run_reference('/inputs/request', bundle=bundle, runtime_root=root,
                profiles=profiles, database_prefix=Path('/database') / PREFIX, database_verification=database,
                work=Path('/work/scientific'), profile_bytes=compatibility(), source_packet=Path('/inputs/source-packet'))
            sys.stdout.flush()
        os.dup2(saved, 1)
    finally:
        os.dup2(saved, 1)
        os.close(saved)
    from dreamm_m1.inference import residue_rows, verify_membrane_reference
    residues = residue_rows(prepared.artifacts)
    calls, _ = verify_membrane_reference(prediction['membrane.csv'], residues)
    summary = {'schema': 'dreamm-local-prediction/1', 'native_complete': True, 'attempt': config['attempt'],
               'runtime_profile':'modern' if modern else 'reference','fresh_feature_extraction':True,
               'source_sha256': config['source_sha256'], 'residues': len(residues),
               'eligible_residues': len(_json(prediction['labels.json'])), 'membrane_calls': len(calls),
               'outcome': 'complete_positive' if calls else 'complete_scientific_empty'}
    Path('/work/prediction-summary.json').write_bytes(canonical(summary))
    files, total = {}, 0
    for path in sorted(Path('/work').rglob('*')):
        require(not path.is_symlink(), 'LOCAL_OUTPUT_INVALID', 'scientific output alias')
        if path.is_dir():
            continue
        raw = _read(path)
        total += len(raw)
        require(len(files) < 511 and total <= 120 * 1024**2, 'LOCAL_OUTPUT_INVALID', 'output aggregate bound')
        files[path.relative_to('/work').as_posix()] = raw
    manifest = {'schema': 'dreamm-local-artifacts/1', **{key: config[key] for key in
                ('attempt', 'source_sha256', 'runtime_sha256', 'application_sha256')}, 'files': {
        name: {'bytes': len(raw), 'sha256': digest(raw)} for name, raw in files.items()}}
    files['artifacts.json'] = canonical(manifest)
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
        for name, raw in files.items():
            item = tarfile.TarInfo(name)
            item.size, item.mode, item.mtime = len(raw), 0o600, 0
            archive.addfile(item, io.BytesIO(raw))
