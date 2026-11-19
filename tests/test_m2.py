"""Real public PDB intake plus model-independent M2 admission tests."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest

from dreamm import cli
from dreamm_m1.identity import digest
from dreamm_m1.pdb_input import read_pdb_source,PDB_1RLW_SHA256

ROOT=Path(__file__).resolve().parents[1]


class M2Tests(unittest.TestCase):
    def test_public_pdb_preserves_original_author_residues_and_all_atoms(self):
        raw=(ROOT/'examples/1rlw/source.pdb').read_bytes()
        source=read_pdb_source(raw,expected_sha256=PDB_1RLW_SHA256)
        self.assertEqual(source.original,raw);self.assertEqual(len(source.atoms),1156)
        self.assertEqual(len({a.residue.uid for a in source.atoms if a.record=='ATOM'}),126)
        self.assertEqual({a.locator.resnum for a in source.atoms if a.record=='ATOM'},set(map(str,range(16,142))))

    def test_multiple_models_are_retained_with_only_reference_zero_selected(self):
        raw=(ROOT/'examples/1rlw/source.pdb').read_bytes()
        atoms=b''.join(row+b'\n' for row in raw.splitlines() if row.startswith((b'ATOM  ',b'HETATM')))
        ensemble=b'MODEL        7\n'+atoms+b'ENDMDL\nMODEL        9\n'+atoms+b'ENDMDL\nEND\n'
        source=read_pdb_source(ensemble,expected_sha256=digest(ensemble))
        self.assertEqual(source.model_ids,('7','9'));self.assertEqual(len(source.atoms),2312)
        self.assertEqual(len(source.reference_atoms),1156)
        self.assertEqual({a.model_index for a in source.reference_atoms},{0})
        self.assertNotEqual(source.atoms[0].residue.uid,source.atoms[1156].residue.uid)
        self.assertEqual(source.original,ensemble)

    def test_cli_m2_does_not_require_a_structure_id(self):
        argv=['run','--mode','M2']
        for n in ('input','sha256','runtime-root','runtime-manifest','runtime-sha256','database','database-manifest','output'):
            argv+=['--'+n,'placeholder']
        args=cli.parser().parse_args(argv)
        self.assertIsNone(args.pdb_id);self.assertEqual(args.execution,'trusted-local')

    @unittest.skipUnless(sys.platform=='linux','trusted-local Linux admission')
    def test_mmcif_bytes_cannot_be_misdeclared_as_qualified_pdb(self):
        from dreamm.trusted import run
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'input.pdb';path.write_bytes((ROOT/'examples/1rlw/source.cif').read_bytes())
            args=argparse.Namespace(runtime_profile='modern',mode='M2',pdb_id=None,timeout=30,
                input=path,sha256=digest(path.read_bytes()),output=root/'output')
            with self.assertRaises(ValueError):run(args)
            self.assertFalse((root/'output').exists())

    def test_non_fixture_chain_and_ambiguous_reference(self):
        from dreamm.modern_pipeline import admit_reference
        raw=(ROOT/'examples/1rlw/source.pdb').read_bytes()
        shifted=b''.join(row[:21]+b'B'+row[22:] if row.startswith(b'ATOM  ') else row
                         for row in raw.splitlines(keepends=True))
        self.assertNotEqual(digest(shifted),PDB_1RLW_SHA256)
        self.assertEqual(admit_reference(shifted),126)
        with self.assertRaisesRegex(ValueError,'exactly one protein chain'):
            admit_reference(shifted.replace(b' B  16',b' C  16',1))
