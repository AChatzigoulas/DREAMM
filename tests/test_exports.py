"""Synthetic artifact/export security; never a scientific prediction."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from dreamm.exports import export,pml_for_calls
from dreamm_m1.identity import canonical,digest


class ExportTests(unittest.TestCase):
    def fixture(self,root,*,empty=False):
        attempt=root/'attempt';results=attempt/'results';results.mkdir(parents=True)
        adapter=canonical({'residues':[{'residue_uid':'synthetic-residue',
            'python_locator':{'chain':'A','resnum':'1','insertion':' '},'processed_resname':'ALA'}]})
        membrane=b'chain,resnum,Amino acid,broken_chain\n'+(b'' if empty else b'A,1,A,1\n')
        files={'scientific/extraction/adapter_map.json':adapter,
            'scientific/extraction/prepared.pdb':b'synthetic structural bytes, not executed\n',
            'scientific/prediction/membrane.csv':membrane}
        manifest={'schema':'dreamm-trusted-artifacts/1','attempt':'synthetic-attempt',
            'files':{n:{'bytes':len(v),'sha256':digest(v)} for n,v in files.items()}}
        for name,raw in files.items():
            p=results/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        raw=canonical(manifest);(attempt/'artifacts.json').write_bytes(raw)
        (attempt/'complete.json').write_bytes(canonical({'artifact_manifest_sha256':digest(raw),
            'summary':{'native_complete':True,'attempt':'synthetic-attempt'}}))
        return attempt,files

    def test_positive_export_preserves_bytes_and_verifies_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);attempt,files=self.fixture(root);out=root/'export'
            receipt=export(argparse.Namespace(attempt=attempt,output=out))
            self.assertFalse(receipt['scientific_calculation_performed'])
            self.assertEqual((out/'membrane.csv').read_bytes(),files['scientific/prediction/membrane.csv'])
            self.assertIn(b'(chain A and resi 1)',(out/'view.pml').read_bytes())
            with zipfile.ZipFile(out/'DREAMM-results.zip') as z:
                for n,raw in files.items():self.assertEqual(z.read('evidence/'+n),raw)
            self.assertTrue((out/'complete.json').is_file())

    def test_scientific_empty_export_does_not_invent_residues(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);attempt,_=self.fixture(root,empty=True);out=root/'export'
            export(argparse.Namespace(attempt=attempt,output=out))
            self.assertIn(b'select dreamm_membrane, none\n',(out/'view.pml').read_bytes())

    def test_corrupted_artifact_refuses_before_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);attempt,_=self.fixture(root);out=root/'export'
            (attempt/'results/scientific/prediction/membrane.csv').write_bytes(b'changed')
            with self.assertRaises(ValueError):export(argparse.Namespace(attempt=attempt,output=out))
            self.assertFalse(out.exists())

    def test_changed_terminal_flag_is_rejected_not_corrected(self):
        with tempfile.TemporaryDirectory() as temp:
            _,files=self.fixture(Path(temp))
            with self.assertRaises(ValueError):pml_for_calls(files['scientific/extraction/adapter_map.json'],
                b'chain,resnum,Amino acid,broken_chain\nA,1,A,0\n')

    def test_manifest_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);attempt,_=self.fixture(root);out=root/'export'
            data=json.loads((attempt/'artifacts.json').read_bytes());data['files']={'../outside':{'bytes':1,'sha256':digest(b'x')}}
            raw=canonical(data);(attempt/'artifacts.json').write_bytes(raw)
            complete=json.loads((attempt/'complete.json').read_bytes());complete['artifact_manifest_sha256']=digest(raw)
            (attempt/'complete.json').write_bytes(canonical(complete))
            with self.assertRaises(ValueError):export(argparse.Namespace(attempt=attempt,output=out))
            self.assertFalse(out.exists())
