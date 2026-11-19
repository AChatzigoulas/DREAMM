"""Modern component qualification, separate from the immutable M1 release core.

Installing/importing a component is not approval to change protected chemistry,
scaling or model behavior. No scientific execution profile is enabled here.
"""
import email
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from urllib.parse import urlsplit
import venv
import zipfile


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def checked_wheels(manifest, wheelhouse):
    """Reject namespace collisions even when a dependency resolver accepts them."""
    if set(manifest) != {'schema', 'profile', 'python', 'scientific_release_qualified', 'wheels'} or manifest['schema'] != 'dreamm-modern-wheels/1':
        raise ValueError('unsupported modern component manifest')
    if manifest['scientific_release_qualified'] is not False or manifest['python'] != '3.14.10':
        raise ValueError('qualification candidate must explicitly remain unapproved')
    if not isinstance(manifest['wheels'], list) or not 0 < len(manifest['wheels']) <= 128:
        raise ValueError('wheel inventory bound')
    payloads, distributions, result = {}, set(), []
    total = 0
    for row in manifest['wheels']:
        filename = row['filename']
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+\.whl', filename):
            raise ValueError('invalid wheel filename')
        url = urlsplit(row['url'])
        if url.scheme != 'https' or url.hostname != 'files.pythonhosted.org' or url.username or url.password or url.query or url.fragment:
            raise ValueError('official PyPI file source required')
        path = Path(wheelhouse) / filename
        if path.is_symlink() or not path.is_file() or type(row['bytes']) is not int or not 0 < row['bytes'] <= 256 * 1024**2:
            raise ValueError('wheel file/size differs')
        raw = path.read_bytes()
        if len(raw) != row['bytes'] or sha256(raw) != row['sha256']:
            raise ValueError('wheel fingerprint differs: ' + filename)
        with zipfile.ZipFile(path) as archive:
            members, metadata = archive.infolist(), []
            if len(members) > 50000:
                raise ValueError('wheel member bound')
            for item in members:
                name = PurePosixPath(item.filename)
                mode = item.external_attr >> 16
                if name.is_absolute() or '..' in name.parts or '\\' in item.filename or stat.S_ISLNK(mode):
                    raise ValueError('unsafe wheel member')
                total += item.file_size
                if item.file_size > 256 * 1024**2 or total > 2 * 1024**3:
                    raise ValueError('expanded wheel bound')
                if len(name.parts) == 2 and name.parts[0].endswith('.dist-info') and name.parts[1] == 'METADATA':
                    metadata.append(item.filename)
                if item.is_dir() or name.parts[0].endswith('.dist-info'):
                    continue
                # Normalize wheel .data/purelib and platlib into their installed
                # import path before collision detection.
                parts = name.parts
                if len(parts) >= 3 and parts[0].endswith('.data') and parts[1] in ('purelib', 'platlib'):
                    installed = '/'.join(parts[2:])
                else:
                    installed = item.filename
                if installed in payloads:
                    raise ValueError('overlapping installed path: ' + installed)
                payloads[installed] = filename
            if len(metadata) != 1:
                raise ValueError('one wheel METADATA required')
            info = email.message_from_bytes(archive.read(metadata[0]))
            key = re.sub(r'[-_.]+', '-', info['Name']).lower()
            if key in distributions or key != re.sub(r'[-_.]+', '-', row['name']).lower() or info['Version'] != row['version']:
                raise ValueError('distribution identity differs')
            distributions.add(key)
        result.append(path)
    return tuple(result)


def install_components(manifest_path, expected_sha256, wheelhouse, output):
    """Offline user install; a venv is packaging, never a security sandbox."""
    if sys.platform != 'linux' or os.geteuid() == 0 or sys.version_info[:3] != (3, 14, 10):
        raise ValueError('non-root Linux CPython 3.14.10 required')
    raw = Path(manifest_path).read_bytes()
    if len(raw) > 1024**2 or sha256(raw) != expected_sha256:
        raise ValueError('component manifest checksum differs')
    manifest = json.loads(raw)
    checked_wheels(manifest, wheelhouse)
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    target = output / 'venv'
    venv.EnvBuilder(with_pip=True).create(target)
    requirements = output / 'requirements.lock'
    requirements.write_text(''.join(row['name'] + '==' + row['version'] + ' --hash=sha256:' + row['sha256'] + '\n'
                                  for row in manifest['wheels']), encoding='ascii')
    python = target / 'bin/python'
    argv = [str(python), '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install',
            '--no-index', '--no-deps', '--only-binary=:all:', '--require-hashes', '--no-compile',
            '--find-links=' + str(Path(wheelhouse).absolute()), '-r', str(requirements)]
    with (output / 'installation.log').open('xb') as log:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=240)
    if result.returncode:
        raise ValueError('offline component installation failed; inspect installation.log')
    checked = subprocess.run([str(python), '-I', '-m', 'pip', '--isolated', 'check'],
                             capture_output=True, timeout=30)
    if checked.returncode:
        raise ValueError('installed dependency check failed: ' + checked.stdout.decode('utf-8', 'replace')[:4096])
    receipt = {'schema': 'dreamm-modern-installation/1', 'profile': manifest['profile'],
        'python': sys.version, 'manifest_sha256': expected_sha256, 'packages': len(manifest['wheels']),
        'pip_check': checked.stdout.decode('ascii'), 'scientific_release_qualified': False}
    pending = output / '.installation.pending'
    with pending.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(receipt, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    pending.rename(output / 'installation.json')
    return receipt


def filter_reference(path, *, chain=None):
    """Supported MoleculeKit API for the historical first-frame filter only.

    Does not prepare/protonate a molecule or replace proteinPrepare with
    systemPrepare. Validate the returned atom identities against source evidence.
    """
    from moleculekit.molecule import Molecule
    from .legacy_residues import LEGACY_RESIDUES
    from dreamm_m1.identity import AA
    import numpy as np
    chains = (chain,) if isinstance(chain, str) else tuple(chain or ())
    molecule = Molecule(str(path))
    molecule.dropFrames(keep=0)
    protein = molecule.atomselect('protein and not resname UNK')
    # The selection dictionary no longer recognises all historical peptide
    # components. A mapped component with intact peptide backbone records
    # makes an explicit polymer candidate. A name or CCD parent alone cannot.
    groups = {}
    for index in range(molecule.numAtoms):
        if molecule.resname[index] in LEGACY_RESIDUES:
            key = (molecule.chain[index], molecule.resid[index],
                   molecule.insertion[index], molecule.segid[index], molecule.resname[index])
            groups.setdefault(key, []).append(index)
    for indices in groups.values():
        names = set(molecule.name[indices])
        if {'N', 'CA', 'C', 'O'} <= names:
            protein[indices] = True
        elif all(molecule.record[i] == 'HETATM' for i in indices):
            protein[indices] = False
    if chains:
        protein &= np.isin(molecule.chain, chains)
        if set(molecule.chain[protein]) != set(chains):
            raise ValueError('one or more selected author chains have no admitted protein residues')
    if not protein.any():
        raise ValueError('no protein residues remain after selection')
    molecule.filter(protein)
    names = sorted(set(molecule.resname) - set(AA))
    if any(name not in LEGACY_RESIDUES for name in names):
        raise ValueError('no historical conversion for selected polymer residues: '+', '.join(
            name for name in names if name not in LEGACY_RESIDUES))
    for name in names:
        molecule.mutateResidue(molecule.resname == name, LEGACY_RESIDUES[name], reconstruct=False)
    # A converted HETATM belongs to the selected polymer now. Leave source
    # bytes and record type intact in the correspondence receipt.
    molecule.record[:] = 'ATOM'
    return molecule


def atom_receipt(molecule):
    """Identity and coordinates; atom serials are evidence, never row joins."""
    if molecule.numFrames != 1:
        raise ValueError('reference filter must preserve exactly one frame')
    keys, atoms = set(), []
    for i in range(molecule.numAtoms):
        key = (str(molecule.chain[i]), int(molecule.resid[i]), str(molecule.insertion[i]),
               str(molecule.resname[i]), str(molecule.name[i]), str(molecule.altloc[i]))
        if key in keys:
            raise ValueError('ambiguous filtered atom identity')
        keys.add(key)
        xyz = [float(x) for x in molecule.coords[i, :, 0]]
        if any(not math.isfinite(x) for x in xyz):
            raise ValueError('nonfinite atom coordinates')
        atoms.append({'key': list(key), 'xyz': xyz})
    return atoms


def numeric_comparison(expected, actual):
    """Exact comparison, no invented tolerance or automatic scientific approval."""
    import numpy as np
    left, right = np.asarray(expected, dtype=float), np.asarray(actual, dtype=float)
    if left.shape != right.shape or not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError('numeric shape/finite-value mismatch')
    different = left != right
    return {'shape': list(left.shape), 'cells': int(left.size), 'different': int(different.sum()),
            'max_absolute': float(np.max(np.abs(left - right))) if left.size else 0.0,
            'exact': not bool(different.any())}
