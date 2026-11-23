"""The accepted modern M1 profile, sharing the complete closure verifier."""
from pathlib import Path
import sys
import importlib.metadata

from dreamm_m1.capture import _parents, _json, _read
from dreamm_m1.identity import require
from .runtime import RuntimeBundle, ROLES, PINNED, FINGERPRINT

VERSIONS = {'CPython': '3.14.10', 'MoleculeKit': '1.17.19', 'biopython': '1.88', 'MDAnalysis': '2.11.0',
    'ProDy': '2.6.1', 'pandas': '3.1.1', 'numpy': '2.5.5', 'scipy': '1.18.1',
    'pdb2pqr': '3.8.0', 'propka': '3.5.1', 'acellera-pdb2pqr': '3.7.1.post3',
    'acellera-propka': '3.5.1.post4', 'scikit-learn': '1.9.1', 'joblib': '1.6.0',
    'mlxtend': '0.25.0', 'lightgbm': '4.7.0', 'HH-suite': '3.3.0', 'Java_major': '21',
    'DSSP': '3.0.0', 'FreeSASA': '2.0.3', 'MSMS': '2.6.1'}
LAYOUT = {'science_python': 'science/bin/python3.14', 'descriptor_python': 'science/bin/python3.14',
          'charge_python': 'charge/bin/python3.14', 'dynamics_python': 'dynamics/bin/python3.14'}


class ModernRuntimeBundle(RuntimeBundle):
    schema = 'dreamm-modern-runtime/1'
    versions = VERSIONS
    roles = (ROLES - {'pdb2pqr'}) | {'charge_python', 'dynamics_python'}
    pinned = {**PINNED, **{name:'32b6777d62aba66726091c65e4a4631073a49b069e4267cc83d4a5df14009f91'
                         for name in LAYOUT}}

    def __init__(self, raw, *, expected_sha256):
        super().__init__(raw, expected_sha256=expected_sha256)
        require(all(self.data['roles'][key] == value for key, value in LAYOUT.items()),
                'RUNTIME_INVALID', 'fixed separate modern Python layouts required')

    def verify(self, root):
        result = super().verify(root)
        root = Path(root)
        for name in ('science', 'charge', 'dynamics'):
            _parents(root / name / 'lib/python3.14/site-packages')
        lock = _json(_read(root / self.data['roles']['dependency_lock']))
        require(lock.get('schema') == 'dreamm-modern-closure/1' and
                lock.get('structure_manifest_sha256') == '429e14b30034e65cc0785d99d9f6aceda5b6ed6f0ff3e2a29aba5250112af4aa' and
                lock.get('charge_manifest_sha256') == 'f613e0785ac17a157f5fb7de16de13d6170c70b8d945ca25efbe5a38b8f70bec' and
                lock.get('prody_source_version') == '2.6.1' and lock.get('old_vm_files_used') is False,
                'RUNTIME_INVALID', 'reviewed modern dependency/acquisition closure required')
        return result


def enable_imports(root, *, stage='science'):
    require(sys.version_info[:3] == (3, 14, 10) and stage in ('science', 'charge', 'dynamics'),
            'SCIENTIFIC_RUNTIME_MISMATCH', 'qualified modern interpreter/stage required')
    site = Path(root) / stage / 'lib/python3.14/site-packages'
    _parents(site)
    sys.path.append(str(site))  # -I -S: do not execute .pth/user startup hooks
    return site


def verify_python_versions(stage):
    if stage == 'science':
        names = ('MoleculeKit', 'biopython', 'MDAnalysis', 'pandas', 'numpy', 'scipy',
                 'acellera-pdb2pqr', 'acellera-propka', 'scikit-learn', 'joblib', 'mlxtend', 'lightgbm')
    elif stage == 'charge':
        names = ('pdb2pqr', 'propka', 'numpy')
    else:
        require(stage == 'dynamics', 'SCIENTIFIC_RUNTIME_MISMATCH', 'unknown numeric stage')
        names = ('ProDy', 'numpy', 'scipy')
    require(all(importlib.metadata.version(name) == VERSIONS[name] for name in names),
            'SCIENTIFIC_RUNTIME_MISMATCH', 'installed modern scientific versions differ')


def requirements():
    return {'schema':ModernRuntimeBundle.schema,'versions':VERSIONS,'required_roles':sorted(ModernRuntimeBundle.roles),
        'python_layout':LAYOUT,'protected_sha256':ModernRuntimeBundle.pinned,'hhblits_threads':15,
        'database_fingerprint':FINGERPRINT,
        'installation_or_execution_performed':False,
        'qualification':'requirements_only',
        'trusted_local':{'exact_visible_CPU_count_required':False,'HHblits_threads':15,
              'memory_policy':'optional_sampled_RSS_limit','minimum_available_ram_bytes':None,
              'namespaces_required':False,'delegated_cgroup_required':False}}
