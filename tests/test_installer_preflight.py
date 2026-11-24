"""Exercise installation decisions with tiny local inputs and no scientific tools."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import acquire_database as database


@unittest.skipUnless(sys.platform == 'linux', 'Linux filesystem and installer')
class DatabaseSpaceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.archive = self.root / (database.PREFIX + '_hhsuite.tar.gz')
        self.partial = self.archive.with_name(self.archive.name + '.part')
        data = b'small verified payload'
        member = tarfile.TarInfo('example')
        member.size = len(data)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as tar:
            tar.addfile(member, io.BytesIO(data))
        self.raw = buf.getvalue()
        files = [dict(name='example', kind='regular', bytes=len(data), sha256=hashlib.sha256(data).hexdigest())]
        fingerprint = hashlib.sha256(database.canonical(files)).hexdigest()
        self.manifest = dict(files=files, fingerprint=fingerprint)
        self.expansion = database.RESERVE + len(data)
        pins = patch.multiple(database, FINGERPRINT=fingerprint, ARCHIVE_SHA256=hashlib.sha256(self.raw).hexdigest())
        pins.start()
        self.addCleanup(pins.stop)

    def download(self, offset, free):
        response = io.BytesIO(self.raw[offset:])
        response.status = 206 if offset else 200
        response.headers = {'Content-Length': str(len(self.raw) - offset)}
        def opened(request, timeout):
            self.assertEqual(request.get_header('Range'), f'bytes={offset}-' if offset else None)
            return response
        with patch.object(database.urllib.request, 'urlopen', opened), patch.object(
                database.shutil, 'disk_usage', return_value=SimpleNamespace(free=free)):
            return database.acquire(self.root, self.manifest)

    def test_fresh_requires_archive_expansion_and_reserve_before_writing(self):
        with self.assertRaisesRegex(ValueError, '12 GiB reserve'):
            self.download(0, self.expansion + len(self.raw) - 1)
        self.assertFalse(self.partial.exists())
        self.assertTrue(self.download(0, self.expansion + len(self.raw))['verified_locally'])

    def test_resumed_download_counts_only_outstanding_bytes(self):
        offset = len(self.raw) // 2
        self.partial.write_bytes(self.raw[:offset])
        self.assertTrue(self.download(offset, self.expansion + len(self.raw) - offset)['verified_locally'])

    def test_existing_database_is_referred_to_installer_before_acquisition(self):
        (self.root/database.PREFIX).mkdir()
        with patch.object(database.urllib.request, 'urlopen', side_effect=AssertionError('unexpected download')):
            with self.assertRaisesRegex(ValueError, 'supply its directory with --database'):
                database.acquire(self.root, self.manifest)

    def test_cached_archive_still_requires_verified_hash_and_extraction_space(self):
        self.archive.write_bytes(self.raw)
        with patch.object(database.urllib.request, 'urlopen', side_effect=AssertionError('unexpected download')):
            with patch.object(database.shutil, 'disk_usage', return_value=SimpleNamespace(free=self.expansion - 1)):
                with self.assertRaisesRegex(ValueError, '12 GiB reserve'):
                    database.acquire(self.root, self.manifest)
            with patch.object(database.shutil, 'disk_usage', return_value=SimpleNamespace(free=self.expansion)):
                self.archive.write_bytes(b'corrupt')
                with self.assertRaisesRegex(ValueError, 'fingerprint differs'):
                    database.acquire(self.root, self.manifest)
                self.archive.write_bytes(self.raw)
                self.assertTrue(database.acquire(self.root, self.manifest)['verified_locally'])


@unittest.skipUnless(sys.platform == 'linux', 'non-root Linux installer')
class RuntimePreflightTests(unittest.TestCase):
    def setUp(self):
        import install
        self.installer = install
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.args = SimpleNamespace(prefix=folder.name, config=str(self.root/'config.json'), profile='basic',
                                    msms_archive=None, reuse_downloads=None, asset_repo=None,
                                    database=folder.name, database_volume=None)
        host = patch.object(install, 'host')
        host.start()
        self.addCleanup(host.stop)
        command = patch.object(install, 'command', side_effect=AssertionError('unexpected install or download'))
        command.start()
        self.addCleanup(command.stop)

    def mark(self, relative):
        path = self.root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}')

    def test_completed_basic_and_full_reach_integrity_verification_with_low_free_space(self):
        self.mark('runtime/installation.json')
        self.mark('runtime/runtime.json')
        with patch.object(self.installer.shutil, 'disk_usage', return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(ValueError, 'differs from its installation receipt'):
                self.installer.install(self.args)
            self.args.profile='full'
            with self.assertRaisesRegex(ValueError, '6 GiB'):
                self.installer.install(self.args)
            for receipt in ('p2rank/runtime.json', 'exports-runtime/checkpoint.json', 'generation/runtime.json'):
                self.mark(receipt)
            Path(self.args.config).write_text(json.dumps(dict(prefix=str(self.root),profile='full')))
            self.args.profile='basic'
            with self.assertRaisesRegex(ValueError, 'differs from its installation receipt'):
                self.installer.install(self.args)
            self.assertEqual(self.args.profile,'full')

    def test_vendor_archive_mismatch_fails_before_database_or_other_downloads(self):
        import install_trusted
        vendor = json.loads((self.installer.ROOT/'runtime/trusted-native.json').read_bytes())['msms']
        archive = self.root/vendor['filename']
        self.args.msms_archive = str(archive)
        self.args.database = None
        self.args.database_volume = str(self.root)
        with patch.object(self.installer.shutil,'disk_usage',return_value=SimpleNamespace(free=8*1024**3)):
            for size in [1, *[row['bytes'] for row in vendor['archives']]]:
                archive.write_bytes(b'x'*size)
                with self.assertRaisesRegex(ValueError, 'verified vendor'):
                    self.installer.install(self.args)
                with self.assertRaisesRegex(ValueError, 'verified vendor'):
                    install_trusted.install_msms(archive, vendor, self.fail)

    @unittest.skipUnless(os.getenv('DREAMM_TEST_MSMS_ORIGINAL') and os.getenv('DREAMM_TEST_MSMS_REPACK'),
                         'supply both separately obtained vendor archives for extraction testing')
    def test_original_and_repacked_msms_install_identical_executable_and_preserve_notices(self):
        import install_trusted
        from dreamm.installation import put
        vendor = json.loads((self.installer.ROOT/'runtime/trusted-native.json').read_bytes())['msms']
        self.args.database = str(self.root/'missing-database')
        binaries = []
        for variant, notices in [('ORIGINAL', ['README']), ('REPACK', ['README', 'LICENSE.md', 'THIRD_PARTY_NOTICES.md'])]:
            with self.subTest(variant=variant):
                archive = Path(os.environ['DREAMM_TEST_MSMS_'+variant])
                self.args.msms_archive = str(archive)
                # Reaching database validation proves that the ordinary installer accepted the vendor input.
                with patch.object(self.installer.shutil, 'disk_usage', return_value=SimpleNamespace(free=8*1024**3)):
                    with self.assertRaisesRegex(ValueError, 'Use --database'):
                        self.installer.install(self.args)
                tree = self.root/variant
                tree.mkdir()
                files, origins = {}, {}
                def add(name, raw, executable):
                    put(tree, name, raw, executable, files, origins, collect=True)
                source = install_trusted.install_msms(archive, vendor, add)
                self.assertEqual(source['sha256'], hashlib.sha256(archive.read_bytes()).hexdigest())
                self.assertEqual(source['bytes'], archive.stat().st_size)
                installed = tree/'native/bin/msms'
                self.assertTrue(os.access(installed, os.X_OK))
                binaries.append(installed.read_bytes())
                self.assertEqual(hashlib.sha256(binaries[-1]).hexdigest(),
                                 '59b873672c740a4f30243a22af241659c98d9b206b7028ef8424c62aac4104dd')
                self.assertEqual(set(files), {'native/bin/msms'} | {'native/share/licenses/msms/'+n for n in notices})
                with tarfile.open(archive) as tar:
                    for name in notices:
                        notice = tree/'native/share/licenses/msms'/name
                        self.assertEqual(notice.read_bytes(), tar.extractfile(name).read())
                        self.assertFalse(os.access(notice, os.X_OK))
        self.assertEqual(binaries[0], binaries[1])

    def test_fresh_profile_selection(self):
        for terminal,answer,expected in ((False,None,'basic'),(True,'','basic'),(True,'full','full')):
            with self.subTest(terminal=terminal,answer=answer):
                with patch.object(sys,'argv',['install.py','--prefix',str(self.root),'--config',self.args.config]), \
                     patch.object(sys.stdin,'isatty',return_value=terminal), \
                     patch('builtins.input',return_value=answer) as prompt, \
                     patch.object(self.installer,'install') as called:
                    self.installer.main()
                self.assertEqual(prompt.call_count,int(terminal))
                self.assertEqual(called.call_args.args[0].profile,expected)
