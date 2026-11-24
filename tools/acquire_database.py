"""Acquire exactly UniClust30 2018_08; verify every byte before use.

External CC-BY-SA database, not included in the DREAMM distribution.
No scientific tool executes. Unknown archive members/types fail closed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import time
import urllib.request

URL='https://wwwuser.gwdguser.de/~compbiol/uniclust/2018_08/uniclust30_2018_08_hhsuite.tar.gz'
# Computed on the official archive whose complete expanded contents matched
# all independently retained database fingerprints. Not a vendor signature.
ARCHIVE_SHA256='33eb3f6bd325a64fe519e7a977a7ae9d798a885ec5a2a111ba0af432f12cbb30'
FINGERPRINT='f72a4125fe124e9dfecbb53bebae5cdbad5eb7371e0b379f07661179ca52c751'
PREFIX='uniclust30_2018_08'
MAX_ARCHIVE=38*1024**3
RESERVE=12*1024**3

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
def fingerprint(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024**2),b''):digest.update(block)
    return digest.hexdigest()

def commit(path, data):
    temporary=path.with_name(path.name+'.pending')
    with temporary.open('wb') as stream:
        stream.write(canonical(data));stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)

def check_space(root, expanded_bytes, download_bytes=0):
    required = expanded_bytes + download_bytes + RESERVE
    free = shutil.disk_usage(root).free
    if free < required:
        raise ValueError(f'UniClust30 needs {required / 1024**3:.2f} GiB free on {root} '
                         f'including the 12 GiB reserve; {free / 1024**3:.2f} GiB available')

def acquire(root, manifest):
    if hashlib.sha256(canonical(manifest['files'])).hexdigest()!=FINGERPRINT or manifest['fingerprint']!=FINGERPRINT:
        raise ValueError('database manifest changed')
    root=Path(root).absolute()
    if root.is_symlink():raise ValueError('data-root alias refused')
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    destination=root/PREFIX
    if os.path.lexists(destination):raise ValueError('existing database is never overwritten; supply its directory with --database')
    expanded_bytes = sum(row['bytes'] for row in manifest['files'] if row['kind'] == 'regular')
    # Probe only our own newly generated files, preserving unrelated content.
    probe=root/'.dreamm-link-target';alias=root/'.dreamm-link-alias'
    if os.path.lexists(alias):raise ValueError('unexpected probe leftovers')
    with probe.open('xb') as stream:stream.write(b'probe')
    try:
        alias.symlink_to(probe.name)
        if alias.read_bytes()!=b'probe':raise ValueError('filesystem aliases unsupported')
    finally:
        if alias.is_symlink():alias.unlink()
        probe.unlink()
    archive=root/(PREFIX+'_hhsuite.tar.gz')
    partial=archive.with_suffix(archive.suffix+'.part')
    for path in (archive, partial):
        if os.path.lexists(path) and (path.is_symlink() or not path.is_file()):
            raise ValueError('expected a regular database archive or partial download')
    started=time.monotonic()
    if not archive.exists():
        offset=partial.stat().st_size if partial.exists() else 0
        headers={'User-Agent':'DREAMM-standalone-database/1'}
        if offset:headers['Range']='bytes='+str(offset)+'-'
        with urllib.request.urlopen(urllib.request.Request(URL,headers=headers),timeout=90) as response:
            if offset and response.status!=206:raise ValueError('publisher did not honour download resume')
            length=int(response.headers.get('Content-Length','0'))+offset
            if not offset < length <= MAX_ARCHIVE:raise ValueError('unexpected database archive size')
            check_space(root, expanded_bytes, length - offset)
            last=0
            with partial.open('ab' if offset else 'xb') as stream:
                downloaded=offset
                while block:=response.read(8*1024**2):
                    if time.monotonic()-started>21600:raise TimeoutError('bounded acquisition deadline')
                    stream.write(block);downloaded+=len(block)
                    if downloaded>length:raise ValueError('archive exceeds declared size')
                    if time.monotonic()-last>15:
                        progress={'phase':'download','bytes':downloaded,'expected_bytes':length,'elapsed_seconds':time.monotonic()-started,'url':URL}
                        commit(root/'acquisition.json',progress);print(json.dumps(progress),flush=True);last=time.monotonic()
                    if shutil.disk_usage(root).free<RESERVE:raise ValueError('data-volume reserve reached')
                stream.flush();os.fsync(stream.fileno())
            if downloaded!=length:raise ValueError('incomplete archive')
        os.rename(partial,archive)
    archive_sha=fingerprint(archive)
    if archive_sha!=ARCHIVE_SHA256:raise ValueError('database archive fingerprint differs')
    if os.path.lexists(destination):raise ValueError('existing database is never overwritten')
    check_space(root, expanded_bytes)
    work=root/('.'+PREFIX+'.extracting')
    work.mkdir(mode=0o700)
    expected={r['name']:r for r in manifest['files']};seen=set();links={};done=[]
    with tarfile.open(archive,mode='r|gz') as source:
        for member in source:
            path=PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name:raise ValueError('unsafe database archive path')
            parts=path.parts
            if member.isdir():
                if parts not in ((PREFIX,),()):raise ValueError('unexpected database directory')
                continue
            name=parts[-1] if len(parts)==1 or (len(parts)==2 and parts[0]==PREFIX) else None
            if name not in expected or name in seen:raise ValueError('unexpected/duplicate database member: '+member.name)
            seen.add(name);row=expected[name]
            if row['kind']=='link':
                if not member.issym() or member.linkname!=row['target']:raise ValueError('database alias differs')
                links[name]=row['target'];continue
            if not member.isfile() or member.size!=row['bytes']:raise ValueError('database member type/size differs')
            digest=hashlib.sha256();count=0
            with source.extractfile(member) as data,(work/name).open('xb') as output:
                while block:=data.read(8*1024**2):
                    count+=len(block);digest.update(block);output.write(block)
                    if count>row['bytes'] or shutil.disk_usage(root).free<RESERVE:raise ValueError('database extraction bound')
                output.flush();os.fsync(output.fileno())
            if count!=row['bytes'] or digest.hexdigest()!=row['sha256']:raise ValueError('database content fingerprint differs: '+name)
            done.append(name)
            progress={'phase':'extract','verified_files':done,'member':name,'elapsed_seconds':time.monotonic()-started}
            commit(root/'acquisition.json',progress);print(json.dumps(progress),flush=True)
    if seen!=expected.keys():raise ValueError('incomplete database archive')
    for name,target in links.items():(work/name).symlink_to(target)
    os.rename(work,destination)
    receipt={'schema':'dreamm-local-database/1','fingerprint':FINGERPRINT,'url':URL,'archive_sha256':archive_sha,
        'database':str(destination),'files':manifest['files'],'elapsed_seconds':time.monotonic()-started,
        'verified_locally':True,'license':'CC-BY-SA-4.0','scientific_execution':False}
    commit(root/'database-verification.json',receipt)
    return receipt

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);parser.add_argument('--manifest',required=True)
    args=parser.parse_args();print(json.dumps(acquire(args.root,json.loads(Path(args.manifest).read_bytes())),indent=2))
