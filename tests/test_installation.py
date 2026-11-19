"""Installer mechanics only: synthetic payloads are never scientific runtimes."""
import argparse
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from dreamm import installation, runtime
from dreamm_m1.identity import M1Error, canonical, digest
import test_runtime as fixtures


def archive_bytes(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:bz2') as tar:
        for name, raw, kind, link in entries:
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            if kind == 'link':
                member.type, member.linkname, member.size = tarfile.SYMTYPE, link, 0
            tar.addfile(member, io.BytesIO(raw) if member.isfile() else None)
    return stream.getvalue()


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_binary_prefix_padding_preserves_suffix_and_total_length(self):
        before = b'header\0/placeholder/long/lib/libpython.so\0tail'
        after = installation.relocate(before, '/placeholder/long', 'binary', '/runtime/sci')
        self.assertEqual(len(before), len(after))
        self.assertIn(b'/runtime/sci/lib/libpython.so\0', after)
        self.assertNotIn(b'/runtime/sci\0/lib', after)
        with self.assertRaises(M1Error):
            installation.relocate(before, '/x', 'binary', '/runtime/too-long')

    def test_conda_prefixes_and_declared_regular_file_are_installed_without_hooks(self):
        raw = archive_bytes([('info/has_prefix', b'/old/long/placeholder text bin/tool\n', 'file', ''),
                             ('bin/tool', b'#!/old/long/placeholder/bin/python\n', 'file', '')])
        package = self.root / 'package.tar.bz2'
        package.write_bytes(raw)
        output = self.root / 'tree'
        output.mkdir()
        wanted = b'#!/runtime/science/bin/python\n'
        expected = {'science/bin/tool': {'bytes': len(wanted), 'sha256': digest(wanted)}}
        provenance = {}
        installation.install_conda(package, output, 'science', expected, {}, provenance, expected_sha256=digest(raw))
        self.assertEqual((output / 'science/bin/tool').read_bytes(), wanted)
        self.assertEqual(set(provenance), set(expected))

    def test_traversal_duplicate_and_hook_payloads_are_rejected(self):
        for entries in ([('../escape', b'bad', 'file', '')], [('x', b'a', 'file', ''), ('x', b'b', 'file', '')],
                        [('bin/.pkg-post-link.sh', b'touch /tmp/no', 'file', '')],
                        [('bin/alias', b'', 'link', '../../../escape')]):
            with self.subTest(entries=entries):
                raw = archive_bytes(entries)
                package = self.root / 'bad.tar.bz2'
                package.write_bytes(raw)
                with self.assertRaises(M1Error):
                    installation.install_conda(package, self.root, 'science', {}, {}, {}, expected_sha256=digest(raw))
        self.assertFalse((self.root.parent / 'escape').exists())

    def test_modified_artifact_fails_before_archive_processing(self):
        package = self.root / 'bad'
        package.write_bytes(b'not an archive')
        with self.assertRaisesRegex(M1Error, 'artifact changed'):
            installation.install_conda(package, self.root, 'science', {}, {}, {}, expected_sha256='0' * 64)

    @unittest.skipUnless(os.name == 'posix', 'real aliases require Linux')
    def test_cross_package_alias_is_preserved_only_when_its_regular_target_is_declared(self):
        raw = archive_bytes([('compiler_compat/ld', b'', 'link', '../bin/linker')])
        package = self.root / 'python.tar.bz2'
        package.write_bytes(raw)
        target = b'new verified dependency\n'
        files = {'science/bin/linker': {'bytes': len(target), 'sha256': digest(target)}}
        links = {'science/compiler_compat/ld': 'science/bin/linker'}
        installation.install_conda(package, self.root, 'science', files, links, {}, expected_sha256=digest(raw))
        installation.put(self.root, 'science/bin/linker', target, True, files, {})
        self.assertEqual((self.root / 'science/compiler_compat/ld').read_bytes(), target)

    @unittest.skipUnless(os.name == 'posix', 'complete installer uses Linux file and alias semantics')
    def test_complete_synthetic_install_is_verified_but_never_authorizes_execution(self):
        fixture = fixtures.RuntimeTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        artifacts = self.root / 'artifacts'
        artifacts.mkdir()
        rows = []
        for index, (name, raw) in enumerate(fixture.content.items()):
            artifact_name = 'synthetic-%d.bin' % index
            (artifacts / artifact_name).write_bytes(raw)
            rows.append({'name': artifact_name, 'bytes': len(raw), 'sha256': digest(raw),
                         'url': 'https://github.com/AChatzigoulas/DREAMM/' + artifact_name,
                         'format': 'file', 'destination': name, 'acquisition': 'public-upstream',
                         'executable': name in {fixture.roles[role] for role in installation.EXECUTABLE_ROLES}})
        raw = canonical({'schema': 'dreamm-runtime-install/1', 'platform': 'linux-64',
                         'runtime': fixture.data, 'artifacts': rows})
        lock = self.root / 'install.json'
        lock.write_bytes(raw)
        output = self.root / 'installed'
        with patch.dict(runtime.PINNED, {}, clear=True):
            report = installation.install(argparse.Namespace(lock=lock, sha256=digest(raw), artifacts=artifacts, output=output))
        self.assertTrue((output / 'installation.json').is_file())
        self.assertFalse(report['scientific_execution_performed'])
        self.assertFalse(report['native_execution_qualified'])
        self.assertFalse(report['package_hooks_executed'])
        self.assertTrue(report['integrity']['integrity_verified'])

    def test_incomplete_lock_never_creates_an_installation(self):
        raw = canonical({'schema': 'dreamm-runtime-install/1', 'platform': 'linux-64', 'runtime': {}, 'artifacts': []})
        with self.assertRaises(M1Error):
            installation.package_lock(raw, digest(raw))
        self.assertFalse((self.root / 'installation.json').exists())
