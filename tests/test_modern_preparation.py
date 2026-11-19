"""Preparation evidence guards on synthetic structures; no chemistry runs."""
from pathlib import Path
import tempfile
import unittest

from dreamm.modern_preparation import comparison, identities, prepare_reference, reference_segments, SETTINGS
from dreamm_m1.preparation import PDB2PQR_OPTIONS


def atom(serial=1, name='CA', x=1.0, num=1):
    return (f'ATOM  {serial:5d} {name:^4} ALA A{num:4d}    '
            f'{x:8.3f}{2.0:8.3f}{3.0:8.3f}{1.0:6.2f}{10.0:6.2f}          C \n').encode()


class PreparationEvidenceTests(unittest.TestCase):
    def test_exact_names_choose_identity_not_coordinate_proximity(self):
        source = atom() + atom(2, 'CB', 2.0)
        target = atom(1, 'CA', 2.0) + atom(2, 'CG', 1.0)
        result = comparison(source, target)
        self.assertEqual(result['identity_matches'], 1)
        self.assertEqual(len(result['source_only']), 1)
        self.assertEqual(len(result['prepared_only']), 1)
        self.assertEqual(len(result['changed_coordinates']), 1)
        self.assertIn('ancestry unproven', result['mapping'])

    def test_identical_structure_has_no_added_removed_or_changed_atoms(self):
        raw = atom() + atom(2, 'CB')
        result = comparison(raw, raw)
        self.assertEqual((result['identity_matches'], result['source_only'], result['prepared_only'],
                          result['changed_coordinates']), (2, [], [], []))

    def test_ambiguous_duplicate_atoms_fail(self):
        with self.assertRaises(ValueError):
            identities(atom() + atom(2))

    def test_input_hash_failure_does_not_start_preparation_or_create_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder)/'source.pdb', Path(folder)/'work'
            source.write_bytes(atom())
            with self.assertRaisesRegex(ValueError, 'input differs'):
                prepare_reference(source, expected_sha256='0'*64, charge_python='/unexecuted', work=target)
            self.assertFalse(target.exists())

    def test_scientific_parameters_remain_explicit_and_no_terminal_flags_restored(self):
        self.assertEqual(SETTINGS['pH'], 7.0)
        self.assertTrue(SETTINGS['titration'])
        self.assertEqual(tuple(PDB2PQR_OPTIONS), ('--with-ph=7.0', '--ff=PARSE', '--ffout=AMBER',
            '--whitespace', '--titration-state-method', 'propka'))
        self.assertNotIn('--neutraln', PDB2PQR_OPTIONS)
        self.assertNotIn('--neutralc', PDB2PQR_OPTIONS)
        self.assertNotIn('--keep-chain', PDB2PQR_OPTIONS)

    def test_segment_projection_is_not_a_generic_input_rewrite(self):
        raw = atom()
        self.assertEqual(reference_segments(raw, '1'*64), raw)
        from dreamm_m1.identity import PDB_8YOQ_SHA256, O95425_SHA256
        for pin in (PDB_8YOQ_SHA256, O95425_SHA256):
            with self.assertRaises(ValueError):
                reference_segments(raw, pin)
