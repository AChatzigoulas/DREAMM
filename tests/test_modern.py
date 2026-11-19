"""Offline installation boundary tests; no scientific import or model load."""
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from dreamm.modern import checked_wheels


class ModernInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wheel(self, name, payload='example/__init__.py', symlink=False):
        filename = name + '-1.0-py3-none-any.whl'
        path = self.root / filename
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(name + '-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: ' + name + '\nVersion: 1.0\n')
            info = zipfile.ZipInfo(payload)
            if symlink:
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b'x = 1\n')
        raw = path.read_bytes()
        return {'name': name, 'version': '1.0', 'filename': filename, 'bytes': len(raw),
                'sha256': hashlib.sha256(raw).hexdigest(), 'url': 'https://files.pythonhosted.org/packages/' + filename}

    def manifest(self, *rows):
        return {'schema': 'dreamm-modern-wheels/1', 'profile': 'synthetic-install-only', 'python': '3.14.10',
                'scientific_release_qualified': False, 'wheels': list(rows)}

    def test_exact_officially_pinned_wheel_and_disjoint_namespaces(self):
        rows = self.manifest(self.wheel('example'), self.wheel('other', 'other/__init__.py'))
        self.assertEqual(len(checked_wheels(rows, self.root)), 2)

    def test_vendored_metadata_is_payload_not_another_root_distribution(self):
        row = self.wheel('example')
        path = self.root / row['filename']
        with zipfile.ZipFile(path, 'a') as archive:
            archive.writestr('example/_vendor/other-1.0.dist-info/METADATA', 'Name: other\nVersion: 1.0\n')
        row.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(len(checked_wheels(self.manifest(row), self.root)), 1)

    def test_resolver_success_cannot_hide_fork_namespace_collision(self):
        rows = self.manifest(self.wheel('pdb2pqr', 'pdb2pqr/__init__.py'), self.wheel('acellera_pdb2pqr', 'pdb2pqr/__init__.py'))
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            checked_wheels(rows, self.root)

    def test_wheel_data_purelib_collisions_are_checked_after_install_path_projection(self):
        rows = self.manifest(self.wheel('first'), self.wheel('second', 'second-1.0.data/purelib/example/__init__.py'))
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            checked_wheels(rows, self.root)

    def test_modified_wheel_cannot_reuse_successful_manifest(self):
        row = self.wheel('example')
        path = self.root / row['filename']
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            checked_wheels(self.manifest(row), self.root)

    def test_paths_and_aliases_do_not_escape_wheel_boundary(self):
        for i, (payload, link) in enumerate([('../outside', False), ('/absolute', False), ('example/link', True)]):
            with self.subTest(payload=payload):
                row = self.wheel('invalid' + str(i), payload, link)
                with self.assertRaisesRegex(ValueError, 'unsafe'):
                    checked_wheels(self.manifest(row), self.root)

    def test_integrity_or_metadata_cannot_self_declare_scientific_qualification(self):
        row = self.wheel('example')
        manifest = self.manifest(row)
        manifest['scientific_release_qualified'] = True
        with self.assertRaisesRegex(ValueError, 'unapproved'):
            checked_wheels(manifest, self.root)
        manifest['scientific_release_qualified'] = False
        row['url'] = 'https://unreviewed.example/packages/example.whl'
        with self.assertRaisesRegex(ValueError, 'official PyPI'):
            checked_wheels(manifest, self.root)


if __name__ == '__main__':
    unittest.main()
