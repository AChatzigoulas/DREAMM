"""Capture chain identities from the original PDB2PQR serializer.

The fixed --whitespace PQR omits chain fields and TER. Capture the identities
at serialization, preserving the invocation and returned PQR bytes.
"""
import logging
from pathlib import Path
import sys

from dreamm_m1.identity import canonical, require
from dreamm_m1.preparation import PDB2PQR_OPTIONS
from .modern_runtime import enable_imports, verify_python_versions


def main():
    require(len(sys.argv)==4,'CHARGE_MAPPING_UNPROVEN','fixed charge stage paths required')
    source, pqr, receipt = map(Path,sys.argv[1:])
    enable_imports('/runtime',stage='charge')
    verify_python_versions('charge')
    from pdb2pqr import io
    from pdb2pqr.main import main as charge
    serializer=io.print_biomolecule_atoms
    captured=[]

    def capture(atomlist, chainflag=False, pdbfile=False):
        value=serializer(atomlist,chainflag=chainflag,pdbfile=pdbfile)
        if not pdbfile:
            require(not captured and not chainflag,'CHARGE_MAPPING_UNPROVEN','unexpected PQR serialization')
            captured.extend({'serial':str(atom.serial),'chain':str(atom.chain_id),
                             'name':str(atom.name),'resnum':str(atom.res_seq),'insertion':str(atom.ins_code)}
                            for atom in atomlist)
        return value

    logging.captureWarnings(True)
    io.print_biomolecule_atoms=capture
    try:
        sys.argv=['pdb2pqr',*PDB2PQR_OPTIONS,str(source),str(pqr)]
        charge()
    finally:
        io.print_biomolecule_atoms=serializer
    require(captured and pqr.is_file(),'CHARGE_MAPPING_UNPROVEN','missing actual PQR serializer capture')
    with receipt.open('xb') as stream:
        stream.write(canonical(captured))
