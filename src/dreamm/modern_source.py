"""Explicit modern parser profile; reuse the complete, unchanged CIF DOM reader.

The legacy CPython 3.12 parser admission is not forged or weakened. This adapter
runs only after the modern runtime closure has been verified inside the worker.
"""
import importlib.metadata
from threading import Lock
import sys

from dreamm_m1 import mmcif_input
from dreamm_m1.identity import require

_LOCK = Lock()


def verify_entry_id(raw,expected):
    import gemmi
    entry=gemmi.cif.read_string(raw.decode('utf-8')).sole_block().find_value('_entry.id')
    require(entry is not None and entry.upper()==expected.upper(),'SOURCE_INVALID','PDB ID does not match the mmCIF entry')


def read_source(raw, *, expected_sha256, runtime_sha256):
    require(sys.version_info[:3] == (3,14,10) and importlib.metadata.version('gemmi') == '0.7.5',
            'PARSER_ENVIRONMENT_UNAVAILABLE', 'verified modern Gemmi 0.7.5 required')
    import gemmi
    require(gemmi.__version__ == '0.7.5', 'PARSER_ENVIRONMENT_UNAVAILABLE', 'Gemmi API version differs')
    receipt = {'schema':'dreamm-modern-parser/1', 'python':sys.version,
               'parser':'Gemmi', 'version':gemmi.__version__, 'runtime_manifest_sha256':runtime_sha256,
               'qualification':'hash_verified_modern_runtime_in_isolated_worker'}
    require(_LOCK.acquire(blocking=False), 'PARSER_ENVIRONMENT_UNAVAILABLE', 'one source reader per worker')
    previous = mmcif_input.verified_gemmi
    try:
        mmcif_input.verified_gemmi = lambda: (gemmi,receipt)
        return mmcif_input.read_mmcif_source(raw, expected_sha256=expected_sha256)
    finally:
        mmcif_input.verified_gemmi = previous
        _LOCK.release()
