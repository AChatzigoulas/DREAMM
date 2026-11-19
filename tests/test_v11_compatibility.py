"""Focused checks for the v1.1 compatibility adapters. No native tool runs."""
import argparse
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dreamm import cli, configured
from dreamm.ensemble_source import prepare_source
from dreamm.legacy_residues import LEGACY_RESIDUES
from dreamm.residue_mapping import match_preparation
from dreamm_m1.identity import canonical, descriptor_adapter, digest
from dreamm_m1.inference import membrane_csv, verify_membrane_reference
from dreamm_m1.local_budget import local_budget, MIB
from dreamm_m1.pdb_input import read_pdb_source
from dreamm_m1.preparation import charge_correspondence, read_pqr


def atom(serial, name, residue, chain, number, *, x=0, record='ATOM', alt=' ', icode=' '):
    element=name[0]
    return (f'{record:6}{serial:5} {name:>4}{alt}{residue:3} {chain}{number:4}{icode}'
            f'   {x:8.3f}{0:8.3f}{0:8.3f}{1:6.2f}{20:6.2f}          {element:>2}\n').encode()


def parsed(raw):
    return read_pdb_source(raw, expected_sha256=digest(raw))


class CompatibilityTests(unittest.TestCase):
    def test_legacy_conversions_verbatim(self):
        self.assertEqual(len(LEGACY_RESIDUES),165)
        self.assertEqual(hashlib.sha256(canonical(LEGACY_RESIDUES)).hexdigest(),
                         '8e499e3fa786da7b0ceb6f470f81df03732b3f0aa28f57ead5cf6d83e98c5740')
        self.assertEqual([LEGACY_RESIDUES[name] for name in ('MSE','SEP','CSD','TPQ','HIP')],
                         ['MET','SER','ALA','ALA','HIS'])

    def test_modified_peptide_hetatm_and_removed_sidechains(self):
        source, before, after = [], [], []
        for i, name in enumerate(('MSE','SEP','CSD','TPQ','HIP'),10):
            for j, n in enumerate(('N','CA','C','O','CB'),1):
                source.append(atom(i*10+j,n,name,'A',i,record='HETATM',x=j))
                after.append(atom(i*10+j,n,LEGACY_RESIDUES[name],'A',i,x=j))
                if n!='CB':
                    before.append(after[-1])
        source.append(atom(99,'C1','HIP','A',89,record='HETATM'))
        pdb, pre, prep = map(lambda rows: parsed(b''.join(rows)+b'END\n'), (source,before,after))
        bindings, receipt=match_preparation(pdb,pre,prep)
        self.assertEqual([r.source_resname for r in bindings.values()],['MSE','SEP','CSD','TPQ','HIP'])
        self.assertEqual(len(receipt['retained_atoms']),20)
        self.assertEqual(len(receipt['removed_before_preparation']),6)
        self.assertEqual(len(receipt['prepared_only_atoms']),5)
        self.assertEqual(next(iter(bindings.values())).uid,pdb.atoms[0].residue.uid)
        with self.assertRaisesRegex(ValueError,'dropped'):
            match_preparation(pdb,parsed(b''.join(before[4:])),prep)

    def test_duplicate_numbers_charges_and_terminal_flags(self):
        pdb=parsed(atom(1,'CA','ALA','A',1,x=1)+atom(2,'CA','ALA','B',1,x=5))
        bindings={a.locator:a.residue for a in pdb.atoms}
        pqr=b'ATOM 1 CA ALA 1 5.000 0.000 0.000 0.2000 1.2\nTER\nATOM 2 CA ALA 1 1.000 0.000 0.000 0.1000 1.2\nTER\n'
        with local_budget(64*MIB,128*MIB):
            raw=read_pqr(pqr)
            match=charge_correspondence(pdb,raw,bindings)
            self.assertEqual([r['derived_locator']['chain'] for r in match['rows']],['B','A'])
            self.assertFalse(match['missing_input_atom_uids'])
            ambiguous=read_pqr(pqr.replace(b'5.000',b'1.000'))
            with self.assertRaises(ValueError):charge_correspondence(pdb,ambiguous,bindings)
        residues={'a':('A',4,'A'),'b':('B',4,'A'),
                  **{f'A_{n}':('A',n,'A') for n in (1,2,3,5,6,7)}}
        calls=membrane_csv(residues,('a','b'))
        self.assertEqual(verify_membrane_reference(calls,residues)[0],{'a':0,'b':1})

    def test_insertion_label_and_protdcal_ter_are_reversible(self):
        raw=(atom(1,'CA','ALA','A',4,icode='A')+
             b'TER       2      ALA A   4A\n'+atom(3,'CA','ALA','B',4)+b'END\n')
        pdb=parsed(raw)
        bindings={a.locator:a.residue for a in pdb.atoms}
        result=descriptor_adapter(raw,bindings,source_mapping_evidence_ref='test')
        self.assertEqual(result.data.splitlines()[1][21:27],b'A   0 ')
        self.assertEqual(result.links[0].locator.residue_label,'4A')
        residue={'a':('A','4A','A')}
        self.assertEqual(verify_membrane_reference(membrane_csv(residue,residue),residue)[0],{'a':1})

    def test_local_budgets_do_not_change_fixed_intake(self):
        atomrow=atom(1,'CA','ALA','A',1)
        raw=b''.join(f'MODEL     {i:4}\n'.encode()+atomrow+b'ENDMDL\n' for i in range(1,24))
        with self.assertRaises(ValueError):parsed(raw)
        with local_budget(64*MIB,128*MIB):
            frames, receipt=prepare_source(raw,expected_sha256=digest(raw),mode='M4')
            self.assertEqual(len(frames),23)
            self.assertTrue(all(frame==atomrow+b'END\n' for frame in frames))
            self.assertEqual(receipt['models'][-1]['source_model_id'],'23')
            self.assertEqual(len(parsed(b'REMARK '+b'x'*88+b'\n'+atomrow).atoms),1)
            large = b'REMARK '+b'x'*88+b'\n'
            large = large*((8*MIB)//len(large)+1)+atomrow
            self.assertGreater(len(large),8*MIB)
            self.assertEqual(len(parsed(large).atoms),1)
        with self.assertRaises(ValueError):parsed(large)

    def test_legacy_chain_lists_and_default_all_with_synthetic_transport(self):
        for mode, source_name, extra, chain_opt in (
            ('M1','1RLW',[],['-c','A,B']),('M2','in.pdb',[],[]),('M3','2KS4',[],[]),
            ('M4','in.cif',[],['-c','A','B']),('M5','1RLW',['--additional','1'],[])):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                args=cli.parser().parse_args(['run','--mode',mode,'-i',source_name,'--output',temp+'/out',*chain_opt,*extra])
                fmt='pdb' if mode=='M2' else 'mmcif'
                cfg={'runtime_sha256':'a'*64}
                with patch.object(configured,'get_input',return_value=(b'science input',fmt,None,{})), \
                     patch.object(configured,'settings',return_value=cfg), \
                     patch('dreamm.cli._run_worker',return_value={'complete':True}) as worker, \
                     patch('dreamm.reference_export.export',return_value={'output':'ref','sha256':'b'*64}) as ref, \
                     patch('dreamm.exports.export',return_value={'zip':'yes'}):
                    receipt=configured.run(args)
                    self.assertTrue(receipt['completed'])
                    self.assertEqual(ref.call_count,int(mode in ('M3','M4','M5')))
                    self.assertEqual(worker.call_count,2 if ref.called else 1)
                    self.assertEqual(worker.call_args.args[0].chains,['A','B'] if chain_opt else None)
                    self.assertEqual(worker.call_args.args[0].mode,mode)

    def test_acquisition_requires_official_entry_and_rejects_redirects(self):
        self.assertTrue(configured.official_url('https://files.rcsb.org/download/1RLW.cif'))
        self.assertFalse(configured.official_url('https://files.rcsb.org@elsewhere/download/1RLW.cif'))
        from urllib.request import Request
        with self.assertRaises(ValueError):
            configured.PdbRedirect().redirect_request(Request('https://files.rcsb.org/download/1RLW.cif'),
                None,302,'',{},'https://files.rcsb.org/download/2KS4.cif')


if __name__=='__main__':
    unittest.main()
