"""Build the source-only review archive from explicit, bounded project paths."""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ('README.md', 'INSTALL.md', 'LICENSE', 'install.sh', 'build.py', 'core-sources.json',
              'DEVELOPERS.md','THIRD_PARTY_NOTICES.md','RELEASE_NOTES_v1.1.md',
              '.gitattributes', '.gitignore')
AREAS = {'src': {'.py', '.json', '.tsv', '.jl'}, 'tools': {'.py'}, 'tests': {'.py'},
         'runtime': {'.json', '.toml'}, 'java/src': {'.java'}, 'examples/1rlw': {'.pdb', '.cif', '.json'}}
DEVELOPMENT_ONLY = {
    'runtime/components-linux-64.json', 'runtime/protdcal-java21-candidate.json',
    'tools/stage_1rlw.py', 'tools/sync_cores.py', 'tests/test_reference_seed.py',
}


def build(target):
    files = [ROOT / name for name in ROOT_FILES]
    for directory, suffixes in AREAS.items():
        files.extend(p for p in (ROOT / directory).rglob('*') if p.suffix in suffixes)
    entries = {}
    for path in sorted(files):
        rel = path.relative_to(ROOT)
        if rel.as_posix() in DEVELOPMENT_ONLY:
            continue
        if (any(part.startswith('.') for part in rel.parts[1:]) or not path.is_file() or
                any(p.is_symlink() for p in [path, *path.parents] if p != ROOT.parent) or
                not path.resolve().is_relative_to(ROOT.resolve()) or path.stat().st_size > 1024**2):
            raise ValueError(f'Unexpected source archive member: {rel}')
        raw = path.read_bytes()
        if b'\0' in raw:
            raise ValueError(f'Unexpected binary source archive member: {rel}')
        entries[rel.as_posix()] = raw
    entries['SHA256SUMS'] = ''.join(f'{hashlib.sha256(raw).hexdigest()}  {n}\n'
                                   for n, raw in entries.items()).encode('ascii')
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, raw in sorted(entries.items()):
            item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.external_attr = (0o100755 if name == 'install.sh' else 0o100644) << 16
            archive.writestr(item, raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(target) as archive:
        if set(archive.namelist()) != entries.keys() or any(archive.read(n) != raw for n, raw in entries.items()):
            raise ValueError('Archive verification failed')
    print(f'{len(entries)} files, SHA-256 {hashlib.sha256(target.read_bytes()).hexdigest()}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)
