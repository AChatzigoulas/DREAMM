"""Local boundary/negative tests, not native scientific or cgroup enforcement evidence."""
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from dreamm import cli, local
from dreamm.local_entry import LocalProfile, enable_scientific_imports
from dreamm_m1 import execution_profiles as policy
from dreamm_m1.identity import M1Error, canonical, digest

IDENTITY = {'attempt': 'a' * 32, 'source_sha256': '1' * 64,
            'runtime_sha256': '2' * 64, 'application_sha256': '3' * 64}


def profile_data(authority):
    return {'schema': 'dreamm-execution-profile/1', 'profile_id': 'synthetic-local', 'version': 1,
            'scope': 'features', 'python': {'interpreter_sha256': '1' * 64},
            'tool': {'executable_sha256': '2' * 64, 'version': 'synthetic'},
            'dependencies': {'lock_sha256': '3' * 64}, 'parameters': {'recipe_sha256': '4' * 64},
            'inputs': {'fixture': {'schema': 'synthetic', 'sha256': '5' * 64, 'max_bytes': 32}},
            'outputs': {'receipt': {'schema': 'synthetic', 'sha256': None, 'max_bytes': 32}},
            'resources': {'whole_job_memory_enforced': True, 'whole_job_memory_bytes': 1024,
                          'CPU_threads': 1, 'process_limit': 2, 'wall_seconds': 1, 'output_budget_bytes': 32},
            'containment': {'verified_profile_sha256': '6' * 64, 'complete_tree_exit_required': True,
                            'authority': authority, 'fallback': None}, 'modes': ['M1']}


class LocalTests(unittest.TestCase):
    def test_verified_science_site_is_explicit_without_running_pth_startup_hooks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            site = root / 'science/lib/python3.7/site-packages'
            site.mkdir(parents=True)
            (site / 'never-run.pth').write_text('import sys; raise RuntimeError("must not execute")\n')
            original = list(sys.path)
            try:
                self.assertEqual(enable_scientific_imports(root, {'science_python': root / 'science/bin/python3.7'}), site)
                self.assertEqual(sys.path[-1], str(site))
            finally:
                sys.path[:] = original
    @unittest.skipUnless(sys.platform == 'linux', 'Linux no-new-privileges bootstrap')
    def test_real_stdlib_bootstrap_requires_release_and_sets_no_new_privileges(self):
        probe = "from pathlib import Path;print(next(x for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('NoNewPrivs:')))"
        child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', local.BOOTSTRAP,
                                  'fixed-synthetic-token', sys.executable, '-I', '-S', '-B', '-c', probe],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            ready = json.loads(child.stdout.readline(128))
            self.assertEqual(ready, {'bootstrap_pid': child.pid})
            out, errors = child.communicate(b'fixed-synthetic-token\n', timeout=10)
            self.assertEqual(child.returncode, 0, errors)
            self.assertEqual(out.decode().split(), ['NoNewPrivs:', '1'])
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
    def test_local_profile_is_not_a_queue_and_cannot_authorize_itself(self):
        profile = LocalProfile(canonical(profile_data('local_cli_supervisor')))
        self.assertEqual(profile.readiness()['missing_verification'], [])
        self.assertFalse(profile.readiness()['runnable'])
        with self.assertRaises(M1Error):
            profile.require_scientific_admission()
        ordinary = policy.ExecutionProfile(canonical(profile_data('local_cli_supervisor')))
        self.assertIn('containment.authority_or_fallback', ordinary.readiness()['missing_verification'])

    def test_unknown_authorities_and_weakened_limits_stay_blocked(self):
        data = profile_data('unknown')
        profile = LocalProfile(canonical(data))
        self.assertIn('containment.authority_or_fallback', profile.readiness()['missing_verification'])
        data = profile_data('local_cli_supervisor')
        data['resources']['whole_job_memory_enforced'] = False
        profile = LocalProfile(canonical(data))
        self.assertIn('resources.whole_job_memory_enforced', profile.readiness()['missing_verification'])

    def test_fixed_argv_has_no_shell_host_home_network_or_service_socket(self):
        argv = local.launch_argv(Path('/usr/bin/bwrap'), Path('/private/runtime'), Path('/private/input'), Path('/private/app.pyz'))
        for value in ('--unshare-all', '--die-with-parent', '--new-session', '--clearenv', '--remount-ro'):
            self.assertIn(value, argv)
        self.assertNotIn('/bin/sh', argv)
        self.assertNotIn('/bin/bash', argv)
        self.assertNotIn('/var/run/docker.sock', argv)
        self.assertNotIn('/home', argv)
        self.assertIn('/runtime/science/bin/python3.7', argv)

    def test_undelegated_or_non_cgroup_directory_is_never_created_or_used(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(M1Error):
                local.delegated_root(temp)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_local_admission_does_not_require_changing_host_swap_configuration(self):
        with patch.object(local.sys, 'platform', 'linux'), patch.object(local.platform, 'machine', return_value='x86_64'), \
             patch.object(local.os, 'geteuid', return_value=1000, create=True), patch.object(local.os, 'cpu_count', return_value=16), \
             patch.object(local.Path, 'read_text', return_value='MemAvailable: 16777216 kB\nSwapTotal: 4194304 kB\n'):
            report = local.resource_preflight()
        self.assertEqual(report['SwapTotal'], 4 * 1024**3)
        # This tests policy only. Actual worker admission also reads back
        # memory.swap.max=0 inside the cgroup; no host configuration is changed.

    def test_old_launcher_cannot_silently_remove_the_scratch_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / 'bwrap'
            binary.write_bytes(b'synthetic binary, never executed')
            from types import SimpleNamespace
            with patch.object(local.subprocess, 'run', return_value=SimpleNamespace(returncode=0,
                stdout=b'--unshare-all --die-with-parent --perms', stderr=b'')) as call:
                with self.assertRaisesRegex(M1Error, 'bounded tmpfs'):
                    local.verify_launcher(binary, digest(binary.read_bytes()))
                call.assert_called_once()

    def test_cleanup_cannot_be_claimed_while_cgroup_remains_populated(self):
        with tempfile.TemporaryDirectory() as temp:
            group = local.LocalGroup(Path(temp), 'a' * 32)
            group.path.mkdir()
            group.created = True
            (group.path / 'cgroup.procs').write_text('123\n')
            (group.path / 'cgroup.events').write_text('populated 1\n')
            (group.path / 'cgroup.kill').write_text('')
            with patch.object(local.time, 'monotonic', side_effect=[0, 16]), self.assertRaisesRegex(M1Error, 'remained populated'):
                group.terminate()
            self.assertEqual((group.path / 'cgroup.kill').read_text(), '1')

    def test_existing_local_slot_is_never_replaced_or_terminated_by_a_new_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            first = local.LocalGroup(Path(temp), 'a' * 32)
            first.path.mkdir()
            (first.path / 'cgroup.procs').write_text('123\n')
            (first.path / 'cgroup.kill').write_text('untouched')
            second = local.LocalGroup(Path(temp), 'b' * 32)
            with self.assertRaises(FileExistsError):
                second.create()
            second.terminate()
            self.assertFalse(second.created)
            self.assertEqual((first.path / 'cgroup.kill').read_text(), 'untouched')

    def tar(self, destination, entries):
        with tarfile.open(destination, 'w:') as archive:
            for name, data, kind in entries:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                if kind == 'link':
                    member.type, member.linkname = tarfile.SYMTYPE, '../escape'
                archive.addfile(member, io.BytesIO(data) if member.isfile() else None)

    def test_result_inventory_and_checksums_are_verified_without_claiming_a_prediction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'worker.tar'
            data = b'synthetic output, no prediction\n'
            record = {'schema': 'dreamm-local-artifacts/1', **IDENTITY, 'files': {'result.txt': {'bytes': len(data), 'sha256': digest(data)}}}
            self.tar(archive, [('result.txt', data, 'file'), ('artifacts.json', canonical(record), 'file')])
            results = root / 'results'
            results.mkdir()
            self.assertEqual(local.unpack_results(archive, results, IDENTITY), record)
            self.assertFalse((root / 'complete.json').exists())

    def test_unsafe_result_paths_aliases_and_missing_manifest_are_rejected(self):
        for name, kind in (('../escape', 'file'), ('alias', 'link'), ('orphan.txt', 'file')):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                archive = root / 'bad.tar'
                self.tar(archive, [(name, b'bad', kind)])
                results = root / 'results'
                results.mkdir()
                with self.assertRaises((M1Error, OSError)):
                    local.unpack_results(archive, results, IDENTITY)

    def test_stale_attempt_artifacts_are_rejected_before_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'worker.tar'
            record = {'schema': 'dreamm-local-artifacts/1', **IDENTITY, 'files': {}}
            self.tar(archive, [('artifacts.json', canonical(record), 'file')])
            results = root / 'results'
            results.mkdir()
            with self.assertRaisesRegex(M1Error, 'inventory differs'):
                local.unpack_results(archive, results, {**IDENTITY, 'attempt': 'b' * 32})

    def test_an_artifact_archive_without_scientific_outputs_is_never_a_scientific_empty_result(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(M1Error, 'mandatory scientific output'):
            local.validate_completion(Path(temp), IDENTITY)

    def test_direct_cli_does_not_require_a_coordinator_or_socket(self):
        args = cli.parser().parse_args(['run', '--mode', 'M1', '--pdb-id', '1RLW',
            *[value for name in ('input', 'sha256', 'source-packet', 'runtime-root', 'runtime-manifest', 'runtime-sha256',
                'installation', 'installation-sha256', 'database', 'database-manifest', 'cgroup-root', 'launcher', 'launcher-sha256', 'output')
              for value in ('--' + name, 'fixture')]])
        self.assertEqual(args.action, cli.run_local)
        self.assertEqual(args.execution, 'trusted-local')
        self.assertFalse(hasattr(args, 'socket'))
        self.assertFalse(hasattr(args, 'coordinator_uid'))
