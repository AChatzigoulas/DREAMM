"""Small molecular-input and checkpoint-boundary tests; no scientific tools."""
from pathlib import Path
import os,tempfile,unittest
from dreamm.ensemble_source import prepare_source
from dreamm_m1.identity import digest


class SourceTests(unittest.TestCase):
    def test_all_models_and_atom_records_are_preserved(self):
        raw=(Path(__file__).resolve().parents[1]/'examples/1rlw/source.pdb').read_bytes()
        atom=b''.join(l+b'\n' for l in raw.splitlines() if l.startswith((b'ATOM  ',b'HETATM',b'TER   ')))
        ensemble=b''.join(f'MODEL     {i:4}\n'.encode()+atom+b'ENDMDL\n' for i in (7,9))+b'END\n'
        frames,r=prepare_source(ensemble,expected_sha256=digest(ensemble),mode='M4')
        self.assertEqual(len(frames),2);self.assertEqual(frames[0],frames[1])
        self.assertEqual([m['source_model_id'] for m in r['models']],['7','9'])
        self.assertIn(b'HETATM',frames[0]);self.assertIn(b'TER ',frames[0])
    def test_alternate_ca_is_not_silently_selected(self):
        atom=b'ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 20.00           C  \n'
        raw=atom+atom[:16]+b'A'+atom[17:]+b'END\n'
        with self.assertRaises(ValueError):prepare_source(raw,expected_sha256=digest(raw),mode='M4')

    def test_mmcif_two_character_component_preserves_fixed_width_pdb_identity(self):
        from types import SimpleNamespace as Object
        from dreamm_ensemble.frames import project_mmcif

        fields = {'auth_asym_id':'A','auth_seq_id':'200','auth_comp_id':'CD','auth_atom_id':'CD',
                  'type_symbol':'CD','label_alt_id':None,'pdbx_PDB_ins_code':None,
                  'Cartn_x':'29.940','Cartn_y':'38.682','Cartn_z':'15.647','occupancy':'1.00',
                  'B_iso_or_equiv':'52.12'}
        residue = Object(auth_chain='A', auth_seq='200', source_resname='CD')
        atom = Object(model_index=0, record='HETATM', residue=residue, fields=fields, uid='cd',
                      field=lambda key:Object(value=fields[key]))
        projected, crosswalk = project_mmcif(Object(model_ids=('1',), atoms=(atom,)))
        self.assertEqual(len(crosswalk), 1)
        self.assertEqual(projected.atoms[0].residue.source_resname, ' CD')
        self.assertEqual(projected.atoms[0].xyz_raw, ('29.940','38.682','15.647'))


@unittest.skipUnless(os.name=='posix','POSIX checkpoint boundary')
class CheckpointTests(unittest.TestCase):
    def test_scratch_sampling_tolerates_transient_file_disappearance(self):
        from unittest.mock import patch
        from dreamm.process import scratch_size
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            with patch('dreamm.process.os.walk',return_value=[(t,[],['already-deleted'])]):
                self.assertEqual(scratch_size(root),0)
            (root/'oversized').write_bytes(b'x'*(8*1024**2+1))
            self.assertEqual(scratch_size(root), 8*1024**2+1)
    def test_hash_and_unlisted_symlink_rejected(self):
        from dreamm.ensemble_local import inventory
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);raw=b'scientific';(root/'data').write_bytes(raw);(root/'checkpoint.json').write_bytes(b'{}')
            m={'files':{'data':{'bytes':len(raw),'sha256':digest(raw)}}}
            inventory(root,m)
            (root/'link').symlink_to(root,target_is_directory=True)
            with self.assertRaises(ValueError):inventory(root,m)
            (root/'link').unlink();(root/'data').write_bytes(b'corrupt')
            with self.assertRaises(ValueError):inventory(root,m)
