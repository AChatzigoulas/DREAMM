"""Offline ProDy 2.6.1 candidate build in the separately pinned build environment.

Run in an isolated build workspace. This does not approve its numerical outputs,
install the built wheel, or replace any protected M1 execution profile.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

SOURCE_SHA256 = '911a8ca3ea7c5f8cd1a9c632e7d7f6e09ea1937db84d147030d08ff8c44b200b'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='official prody-2.6.1.tar.gz, acquired separately')
    parser.add_argument('--output', required=True, help='new private build/evidence directory')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() == 0 or sys.version_info[:3] != (3, 14, 10):
        raise ValueError('non-root Linux CPython 3.14.10 build environment required')
    for name, version in [('numpy', '2.5.5'), ('setuptools', '84.2.1'), ('wheel', '0.48.0')]:
        if importlib.metadata.version(name) != version:
            raise ValueError('use the pinned ProDy build profile: ' + name)
    source = Path(args.source)
    if source.is_symlink() or not 0 < source.stat().st_size < 40 * 1024**2:
        raise ValueError('source archive bound')
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError('official ProDy source fingerprint differs')
    output = Path(args.output).absolute()
    output.mkdir(mode=0o700)
    wheels = output / 'wheels'
    wheels.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix='prody-source-', dir=output) as temporary:
        folder = Path(temporary)
        with tarfile.open(source) as archive:
            members = archive.getmembers()
            if len(members) > 20000 or sum(row.size for row in members) > 512 * 1024**2:
                raise ValueError('source expansion bound')
            archive.extractall(folder, filter='data')
        environment = dict(os.environ, CC='gcc', CXX='g++', PIP_NO_INDEX='1', PIP_CONFIG_FILE=os.devnull)
        argv = [sys.executable, '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'wheel',
                '--no-index', '--no-deps', '--no-build-isolation', str(folder / 'prody-2.6.1'),
                '--wheel-dir', str(wheels)]
        with (output / 'build.log').open('xb') as log:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    env=environment, timeout=300)
        if result.returncode:
            raise ValueError('source build failed; inspect build.log')
    built = list(wheels.glob('prody-2.6.1-cp314-cp314-*.whl'))
    if len(built) != 1:
        raise ValueError('unexpected build artifact')
    report = {'schema': 'dreamm-prody-build/1', 'source_sha256': SOURCE_SHA256, 'python': sys.version,
              'wheel': built[0].name, 'wheel_sha256': hashlib.sha256(built[0].read_bytes()).hexdigest(),
              'bytes': built[0].stat().st_size, 'scientific_release_qualified': False,
              'portability': 'native host build; must be built/qualified for the target Linux glibc ABI'}
    pending = output / '.build.pending'
    pending.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    pending.rename(output / 'build.json')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
