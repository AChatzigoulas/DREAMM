"""Focused bond and annotation tests for the M5 polar writer."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dreamm.polar import prepare


def atom(serial, name, element, alt=' '):
    return (f'ATOM  {serial:5d} {name:>4}{alt}ALA A   1    '
            f'{1.0:8.3f}{2.0:8.3f}{3.0:8.3f}{1.0:6.2f}{0.0:6.2f}          {element:>2}  \n').encode()


class PolarTests(unittest.TestCase):
    def test_carbon_hydrogen_is_removed_without_losing_retained_connectivity(self):
        carbon, hydrogen, nitrogen, polar = (
            atom(1,'CA','C','A'), atom(2,'HA','H'), atom(3,'N','N'), atom(4,'H','H'))
        raw = carbon + hydrogen + nitrogen + polar + b'CONECT    1    2    3\nCONECT    2    1\nEND\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path/'ALA.cif.gz').touch()
            patches = path/'patches.xml'
            patches.write_text('<patches/>')
            bonds = {'HA':{('CA','C')}, 'H':{('N','N')}}
            with patch('dreamm.polar.component_bonds', return_value=bonds):
                result, _ = prepare(raw, path, patches)
                self.assertEqual(result, carbon + nitrogen + polar + b'CONECT    1    3\nEND\n')
                with self.assertRaisesRegex(ValueError, 'no unique bonded parent'):
                    prepare(raw.replace(carbon, carbon+atom(5,'CA','C','B')), path, patches)
