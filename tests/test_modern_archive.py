"""New modern-runtime archive formats; synthetic bytes, no tool execution."""
import io
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
import zipfile

from dreamm.installation import install_archive
from dreamm_m1.identity import digest


class ModernArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.target=self.root/'tree';self.target.mkdir()

    def install(self,raw,kind,expected,links=None):
        path=self.root/'artifact';path.write_bytes(raw)
        item={'format':kind,'destination':'science','sha256':digest(raw)}
        origins={}
        install_archive(path,self.target,item,expected,links or {},origins)
        return origins

    def wheel(self,entries):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            for name,data in entries:archive.writestr(name,data)
        return output.getvalue()

    def tar(self,entries):
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w:gz') as archive:
            for name,data,link in entries:
                row=tarfile.TarInfo(name);row.size=len(data);row.mode=0o700
                if link:row.type=tarfile.SYMTYPE;row.linkname=link;row.size=0
                archive.addfile(row,None if link else io.BytesIO(data))
        return output.getvalue()

    def test_exact_wheel_files_and_data_purelib_projection_without_hooks(self):
        raw=self.wheel([('package/__init__.py',b'raise RuntimeError("must not import")'),
                        ('package-1.0.data/purelib/extra.py',b'x=1\n')])
        expected={name:{'bytes':len(data),'sha256':digest(data)} for name,data in
                  [('science/package/__init__.py',b'raise RuntimeError("must not import")'),('science/extra.py',b'x=1\n')]}
        self.assertEqual(set(self.install(raw,'wheel',expected)),set(expected))

    def test_wheel_traversal_collision_and_script_relocation_fail_closed(self):
        for entries in ([('../escape',b'bad')],[('pkg-1.0.data/scripts/tool',b'exec')],
                        [('x.py',b'one'),('pkg-1.0.data/purelib/x.py',b'two')]):
            raw=self.wheel(entries)
            expected={'science/x.py':{'bytes':3,'sha256':digest(b'one')}}
            with self.subTest(entries=entries),self.assertRaises(ValueError):self.install(raw,'wheel',expected)
        self.assertFalse((self.root/'escape').exists())

    def test_exact_single_prefix_tar_preserves_python_or_java_payload(self):
        raw=self.tar([('jdk-21/bin/java',b'not executed',None)])
        expected={'science/bin/java':{'bytes':12,'sha256':digest(b'not executed')}}
        self.assertEqual(set(self.install(raw,'tar-gz-strip-one',expected)),set(expected))

    def test_multiple_prefix_and_escaping_tar_alias_are_rejected(self):
        for entries in ([('python/bin/x',b'one',None),('other/bin/y',b'two',None)],
                        [('python/bin/x',b'', '../../../escape')]):
            raw=self.tar(entries)
            expected={'science/bin/x':{'bytes':3,'sha256':digest(b'one')}}
            with self.subTest(entries=entries),self.assertRaises(ValueError):self.install(raw,'tar-gz-strip-one',expected)

    @unittest.skipUnless(os.name=='posix','native internal file alias requires Linux')
    def test_tar_alias_requires_the_exact_declared_regular_target(self):
        raw=self.tar([('python/bin/python3.14',b'ELF placeholder',None),('python/bin/python',b'','python3.14')])
        expected={'science/bin/python3.14':{'bytes':15,'sha256':digest(b'ELF placeholder')}}
        links={'science/bin/python':'science/bin/python3.14'}
        self.install(raw,'tar-gz-strip-one',expected,links)
        self.assertEqual((self.target/'science/bin/python').read_bytes(),b'ELF placeholder')
