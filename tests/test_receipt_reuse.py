"""Targeted cache invalidation and exact writer-adapter checks, no science."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dreamm.writer_identity import project,PINS
from dreamm_m1.pdb_input import PDB_1RLW_SHA256
from dreamm_m1.identity import digest


class WriterTests(unittest.TestCase):
    def test_unrelated_source_is_untouched(self):
        self.assertEqual(project(b'unmodified', '0'*64,'before')[0],b'unmodified')
    def test_unreviewed_output_is_not_normalized(self):
        with self.assertRaises(ValueError):project(b'ATOM synthetic 0',PDB_1RLW_SHA256,'before')


@unittest.skipUnless(os.name=='posix','private POSIX receipt')
class ReceiptTests(unittest.TestCase):
    def test_reuse_and_file_alias_installation_invalidation(self):
        from dreamm.database_receipt import verify
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);root.chmod(0o700);db=root/'db';db.mkdir();data=db/'part';data.write_bytes(b'abc')
            (db/'alias').symlink_to('part');installation=root/'installation.json';installation.write_bytes(b'installed')
            state=root/'state.json';calls=[]
            def check(*a,hash_contents):
                calls.append(hash_contents)
                return {'fully_hashed':hash_contents,'file_identities':{'part':{'bytes':data.stat().st_size}}}
            with patch('dreamm.database_receipt.verify_database',side_effect=check):
                run=lambda:verify(db,{},runtime_sha256='1'*64,installation=installation,state=state)
                self.assertEqual(run()['verification_method'],'full_sha256')
                self.assertEqual(run()['verification_method'],'unchanged_verified_receipt')
                data.write_bytes(b'changed');self.assertEqual(run()['verification_method'],'full_sha256')
                installation.write_bytes(b'new installation');self.assertEqual(run()['verification_method'],'full_sha256')
                (db/'alias').unlink();(db/'alias').symlink_to('different')
                self.assertEqual(run()['verification_method'],'full_sha256')
                self.assertEqual(calls,[True,False,True,True,True])
