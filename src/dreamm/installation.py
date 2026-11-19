"""Offline installation of a reviewed, complete Linux runtime closure.

No resolver, package hooks, shell, downloads or model loading are involved.
A lock describes final bytes, not merely unconstrained package version names.
"""
import io
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import tarfile
import zipfile
from urllib.parse import urlsplit

from .cli import sync_directory, write_private
from .runtime import runtime_bundle, relative_path
from dreamm_m1.capture import _json, _parents, _read
from dreamm_m1.identity import canonical, digest, require, sha

PUBLISHERS = {'conda.anaconda.org', 'repo.anaconda.com', 'files.pythonhosted.org',
              'raw.githubusercontent.com', 'github.com'}
MAX_PACKAGES = 256
MAX_EXPANDED = 8 * 1024**3
EXECUTABLE_ROLES = {'science_python', 'descriptor_python', 'java', 'hhblits', 'dssp', 'freesasa', 'msms', 'pdb2pqr'}


def package_lock(raw, expected):
    require(digest(raw) == sha(expected), 'INSTALL_LOCK_INVALID', 'reviewed installation lock checksum differs')
    data = _json(raw)
    require(isinstance(data, dict) and set(data) == {'schema', 'platform', 'runtime', 'artifacts'}
            and data['schema'] == 'dreamm-runtime-install/1' and data['platform'] == 'linux-64',
            'INSTALL_LOCK_INVALID', 'complete Linux x86-64 install lock required')
    bundle_raw = canonical(data['runtime'])
    bundle = runtime_bundle(bundle_raw, expected_sha256=digest(bundle_raw))
    require(isinstance(data['artifacts'], list) and 0 < len(data['artifacts']) <= MAX_PACKAGES,
            'INSTALL_LOCK_INVALID', 'artifact inventory bound')
    names, source_bytes = set(), 0
    for item in data['artifacts']:
        require(isinstance(item, dict) and set(item) == {'name', 'bytes', 'sha256', 'url', 'format', 'destination', 'acquisition', 'executable'},
                'INSTALL_LOCK_INVALID', 'artifact fields')
        require(re.fullmatch(r'[A-Za-z0-9_.+-]{1,160}', item['name']) and item['name'] not in names
                and type(item['bytes']) is int and 0 < item['bytes'] <= 512 * 1024**2,
                'INSTALL_LOCK_INVALID', 'unique artifact name and size')
        names.add(item['name'])
        source_bytes += item['bytes']
        require(source_bytes <= MAX_EXPANDED and type(item['executable']) is bool,
                'INSTALL_LOCK_INVALID', 'source aggregate size or explicit file mode')
        sha(item['sha256'])
        url = urlsplit(item['url'])
        require(url.scheme == 'https' and url.hostname in PUBLISHERS and not url.username
                and not url.password and not url.fragment and not url.query and url.port in (None, 443),
                'INSTALL_LOCK_INVALID', 'official HTTPS acquisition source required; no server snapshots')
        require(item['format'] in ('conda-tar-bz2', 'file', 'wheel', 'tar-gz-strip-one')
                and item['acquisition'] in ('public-upstream', 'separately-licensed-upstream'),
                'INSTALL_LOCK_INVALID', 'explicit artifact format and acquisition required')
        relative_path(item['destination'])
    return data, bundle


def relocate(raw, placeholder, mode, prefix):
    old, new = placeholder.encode('utf-8'), prefix.encode('utf-8')
    require(old and len(old) <= 512 and mode in ('text', 'binary'), 'INSTALL_INVALID', 'Conda prefix record')
    if mode == 'text':
        return raw.replace(old, new)
    require(len(new) <= len(old), 'INSTALL_INVALID', 'binary prefix cannot grow')
    # Preserve the complete suffix of each NUL-terminated path, then pad at its
    # end. Padding immediately after the prefix would corrupt library paths.
    return re.sub(re.escape(old) + br'([^\0]*?)\0',
                  lambda m: new + m.group(1) + b'\0' * (len(old) - len(new) + 1), raw)


def install_conda(archive_path, root, destination, expected_files, expected_links, origin, *, expected_sha256, collect=False):
    total = 0
    archive_bytes = _read(archive_path, 512 * 1024**2)
    require(digest(archive_bytes) == expected_sha256, 'INSTALL_INVALID', 'artifact changed after preflight')
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode='r|bz2') as archive:
        names, payloads, links, prefixes = set(), {}, {}, {}
        for member in archive:
            require(len(names) < 50000, 'INSTALL_INVALID', 'package entry bound')
            name = member.name.rstrip('/')
            relative_path(name)
            require(name not in names, 'INSTALL_INVALID', 'duplicate archive entry')
            names.add(name)
            require(member.isdir() or member.isfile() or member.issym(), 'INSTALL_INVALID', 'special or hardlinked package member')
            if member.isdir():
                continue
            if member.isfile():
                require(0 <= member.size <= 512 * 1024**2, 'INSTALL_INVALID', 'package member size')
                total += member.size
                require(total <= 512 * 1024**2, 'INSTALL_INVALID', 'package expanded bound')
                raw = archive.extractfile(member).read(member.size + 1)
                require(len(raw) == member.size, 'INSTALL_INVALID', 'short archive member')
                if name == 'info/has_prefix':
                    for line in raw.decode('utf-8').splitlines():
                        parts = shlex.split(line)
                        require(len(parts) == 3 and parts[2] not in prefixes, 'INSTALL_INVALID', 'explicit Conda prefix records required')
                        relative_path(parts[2])
                        prefixes[parts[2]] = parts[:2]
                if not name.startswith('info/'):
                    require(not re.search(r'(^|/)\.[^/]+-(pre-link|post-link|pre-unlink)\.', name),
                            'INSTALL_INVALID', 'package hooks require separate review; never run implicitly')
                    payloads[name] = (raw, bool(member.mode & 0o111))
            elif not name.startswith('info/'):
                require(not PurePosixPath(member.linkname).is_absolute() and '\\' not in member.linkname,
                        'INSTALL_INVALID', 'relative internal file alias required')
                parts = list(PurePosixPath(name).parent.parts)
                for part in member.linkname.split('/'):
                    if part == '..':
                        require(parts, 'INSTALL_INVALID', 'escaping package alias')
                        parts.pop()
                    elif part not in ('', '.'):
                        parts.append(part)
                links[name] = '/'.join(parts)
        require(set(prefixes) <= payloads.keys(), 'INSTALL_INVALID', 'prefix metadata references missing file')
        for name, (raw, executable) in payloads.items():
            if name in prefixes:
                placeholder, mode = prefixes[name]
                raw = relocate(raw, placeholder, mode, '/runtime/' + destination)
            target = destination + '/' + name
            put(root, target, raw, executable, expected_files, origin, collect=collect)
        for name, target in links.items():
            path, full_target = destination + '/' + name, destination + '/' + target
            if collect: expected_links[path] = full_target
            require(full_target in expected_files and expected_links.get(path) == full_target,
                    'INSTALL_INVALID', 'alias must target a declared regular file in the complete closure')
            folder = root / path
            folder.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _parents(folder.parent)
            folder.symlink_to(os.path.relpath(root / full_target, folder.parent))


def put(root, name, raw, executable, expected, origins, *, collect=False):
    relative_path(name)
    if collect:
        row = {'bytes':len(raw), 'sha256':digest(raw)}
        require(name not in expected or expected[name] == row, 'INSTALL_INVALID', 'conflicting artifact payload')
        expected[name] = row
    require(name in expected and expected[name] == {'bytes': len(raw), 'sha256': digest(raw)},
            'INSTALL_INVALID', 'installed bytes differ from complete closure: ' + name)
    path = root / name
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _parents(path.parent)
    if path.exists():
        require(not path.is_symlink() and _read(path, 512 * 1024**2) == raw,
                'INSTALL_INVALID', 'conflicting package file')
    else:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        with os.fdopen(os.open(path, flags, 0o700 if executable else 0o600), 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    origins.setdefault(name, []).append(digest(raw))


def install_archive(path, root, item, expected_files, expected_links, origins, *, collect=False):
    """Install exact wheel/JDK/portable-Python payloads; run no package hooks.

    Wheel script/data relocation is intentionally unsupported. DREAMM uses
    interpreter/module entries. Tar archives must have one explicit top folder.
    """
    require(item['format'] in ('wheel','tar-gz-strip-one'),'INSTALL_INVALID','unsupported modern archive format')
    raw = _read(path,512*1024**2)
    require(digest(raw) == item['sha256'],'INSTALL_INVALID','archive changed after preflight')
    destination = relative_path(item['destination'])
    seen, links, expanded = set(), {}, 0
    def payload(name, data, executable):
        nonlocal expanded
        relative_path(name)
        require(name not in seen and len(seen) < 50000,'INSTALL_INVALID','duplicate/archive entry bound')
        seen.add(name);expanded += len(data)
        require(expanded <= 512*1024**2,'INSTALL_INVALID','archive expanded bound')
        put(root,destination+'/'+name,data,executable,expected_files,origins,collect=collect)
    if item['format'] == 'wheel':
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            require(len(archive.infolist()) <= 50000,'INSTALL_INVALID','wheel entry bound')
            for member in archive.infolist():
                relative_path(member.filename.rstrip('/'))
                mode = member.external_attr >> 16
                require(not stat.S_ISLNK(mode) and stat.S_IFMT(mode) in (0,stat.S_IFREG,stat.S_IFDIR),
                        'INSTALL_INVALID','special wheel entry')
                if member.is_dir():continue
                require(member.file_size <= 512*1024**2 and expanded+member.file_size <= 512*1024**2,
                        'INSTALL_INVALID','wheel expanded bound')
                name = member.filename
                parts = PurePosixPath(name).parts
                if parts[0].endswith('.data'):
                    require(len(parts) >= 3 and (parts[1] in ('purelib','platlib')
                            or (collect and parts[1] in ('scripts','data','headers'))),
                            'INSTALL_INVALID','unsupported wheel script/data relocation')
                    # Qualification inventory retains non-import wheel data as
                    # inert evidence. Never create or execute script wrappers.
                    name = '/'.join(parts[2:]) if parts[1] in ('purelib','platlib') else '_wheel_data/'+name
                payload(name,archive.read(member),bool(mode & 0o111))
    else:
        prefix = None
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r|gz') as archive:
            entries = set()
            for member in archive:
                name = relative_path(member.name.rstrip('/'))
                require(name not in entries and len(entries)<50000,'INSTALL_INVALID','duplicate/tar entry bound')
                entries.add(name)
                parts = PurePosixPath(name).parts
                prefix = prefix or parts[0]
                require(parts[0] == prefix and (member.isdir() or member.isfile() or member.issym()),
                        'INSTALL_INVALID','one tar prefix and regular/internal-alias entries required')
                if member.isdir():continue
                require(len(parts)>1,'INSTALL_INVALID','top-level tar payload is unsupported')
                target = '/'.join(parts[1:])
                if member.isfile():
                    require(member.size <= 512*1024**2 and expanded+member.size <= 512*1024**2,
                            'INSTALL_INVALID','tar expanded bound')
                    data = archive.extractfile(member).read(member.size+1)
                    require(len(data)==member.size,'INSTALL_INVALID','truncated tar payload')
                    payload(target,data,bool(member.mode & 0o111))
                else:
                    require(not PurePosixPath(member.linkname).is_absolute() and '\\' not in member.linkname,
                            'INSTALL_INVALID','relative internal tar alias required')
                    resolved = list(parts[:-1])
                    for part in member.linkname.split('/'):
                        if part == '..':
                            require(len(resolved)>1,'INSTALL_INVALID','escaping tar alias')
                            resolved.pop()
                        elif part not in ('','.'):
                            resolved.append(part)
                    full = destination+'/'+('/'.join(resolved[1:]))
                    links[destination+'/'+target] = full
        for name,target in links.items():
            if collect: expected_links[name] = target
            require(target in expected_files and expected_links.get(name)==target,
                    'INSTALL_INVALID','unlisted tar alias')
            path = root/name;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            _parents(path.parent)
            wanted=os.path.relpath(root/target,path.parent)
            if collect and path.is_symlink():
                require(os.readlink(path)==wanted,'INSTALL_INVALID','existing incomplete-install alias differs')
            else:path.symlink_to(wanted)


def install(args):
    require(os.name == 'posix' and os.geteuid() != 0, 'INSTALL_UNAVAILABLE', 'install as a non-root Linux user')
    lock_raw = _read(Path(args.lock).absolute(), 8 * 1024**2)
    lock, bundle = package_lock(lock_raw, args.sha256)
    artifacts, target = Path(args.artifacts).absolute(), Path(args.output).absolute()
    _parents(artifacts)
    _parents(target.parent)
    # Check every artifact before creating even an incomplete installation.
    for row in lock['artifacts']:
        path = artifacts / row['name']
        require(path.lstat().st_size == row['bytes'] and digest(_read(path, 512 * 1024**2)) == row['sha256'],
                'INSTALL_INVALID', 'missing or changed offline artifact: ' + row['name'])
    target.mkdir(mode=0o700)
    tree = target / 'tree'
    tree.mkdir(mode=0o700)
    provenance, file_sources = {}, {}
    for row in lock['artifacts']:
        path = artifacts / row['name']
        per_artifact = {}
        if row['format'] == 'file':
            put(tree, row['destination'], _read(path, 512 * 1024**2), row['executable'], bundle.data['files'], per_artifact)
        elif row['format'] == 'conda-tar-bz2':
            install_conda(path, tree, row['destination'], bundle.data['files'], bundle.data['links'], per_artifact,
                          expected_sha256=row['sha256'])
        else:
            install_archive(path,tree,row,bundle.data['files'],bundle.data['links'],per_artifact)
        provenance.update(per_artifact)
        for name in per_artifact:
            file_sources.setdefault(name, []).append(row['name'])
    result = bundle.verify(tree)
    executable_roles = EXECUTABLE_ROLES & bundle.data['roles'].keys()
    executable_roles |= {'charge_python', 'dynamics_python'} & bundle.data['roles'].keys()
    require(all(os.access(tree / bundle.data['roles'][role], os.X_OK) for role in executable_roles),
            'INSTALL_INVALID', 'required tool role is not executable')
    require(set(provenance) == set(bundle.data['files']), 'INSTALL_INVALID', 'installed file provenance is incomplete')
    write_private(target / 'runtime.json', canonical(bundle.data))
    receipt = {'schema': 'dreamm-runtime-installation/1', 'installation_lock_sha256': digest(lock_raw),
               'runtime_manifest_sha256': digest(canonical(bundle.data)), 'integrity': result,
               'artifact_sources': lock['artifacts'], 'file_artifact_names': file_sources,
               'package_hooks_executed': False,
               'scientific_execution_performed': False, 'native_execution_qualified': False}
    require(len(canonical(receipt)) <= 8 * 1024**2, 'INSTALL_INVALID', 'installation receipt exceeds bound')
    write_private(target / '.installation.pending', canonical(receipt))
    os.rename(target / '.installation.pending', target / 'installation.json')
    sync_directory(target)
    sync_directory(target.parent)
    return receipt
