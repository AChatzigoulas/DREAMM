"""Trusted-local verification reuse; never weakens the public worker verifier.

Full hashes establish the receipt. A private receipt is reused only while file,
alias, directory, runtime and installation metadata are unchanged. This is not
protection against privileged tampering/bit rot; --full-database-verify remains
available. An altered/missing receipt triggers full verification.
"""
import hashlib
import os
from pathlib import Path
import pkgutil
import stat
import tempfile

from dreamm_m1.capture import _read,_json,_parents
from dreamm_m1.identity import canonical,digest,require
from dreamm_m1.rocky_database import FINGERPRINT, PREFIX

PREVIOUS_VERIFIER = '7d7d0cfad77a40b33d6948cc8b0fadb7a07dad5fdd377aea1ba667db4ef47acf'


def verify_database(root, manifest, *, hash_contents=True):
    root=Path(root).absolute();_parents(root)
    rows=manifest['files']
    require(manifest.get('fingerprint') == digest(canonical(rows)) == FINGERPRINT
            and manifest.get('release') == '2018_08' and manifest.get('prefix') == PREFIX,
            'DATABASE_UNVERIFIED','the exact original database manifest is required')
    require({p.name for p in root.iterdir()} == {r['name'] for r in rows},
            'DATABASE_UNVERIFIED','database member inventory differs')
    identities={}
    for row in rows:
        path=root/row['name']
        if row['kind']=='link':
            require(path.is_symlink() and os.readlink(path)==row['target'], 'DATABASE_UNVERIFIED','database alias differs')
            continue
        before=path.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_size==row['bytes'], 'DATABASE_UNVERIFIED','database size/type differs')
        if hash_contents:
            checksum=hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(8*1024**2),b''):checksum.update(block)
            require(checksum.hexdigest()==row['sha256'], 'DATABASE_UNVERIFIED','database bytes differ: '+path.name)
        after=path.lstat()
        require((before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),
                'DATABASE_UNVERIFIED','database changed during verification')
        identities[path.name]={'inode':after.st_ino,'bytes':after.st_size,'mtime_ns':after.st_mtime_ns}
    return {'manifest_sha256':FINGERPRINT,'mount':'/database','fully_hashed':hash_contents,'file_identities':identities}


def snapshot(root):
    root=Path(root).absolute();_parents(root)
    def row(path):
        s=path.lstat()
        require(stat.S_ISREG(s.st_mode) or stat.S_ISDIR(s.st_mode) or stat.S_ISLNK(s.st_mode),
                'DATABASE_UNVERIFIED','special database member')
        return {'device':s.st_dev,'inode':s.st_ino,'bytes':s.st_size,'mtime_ns':s.st_mtime_ns,
            'ctime_ns':s.st_ctime_ns,'mode':s.st_mode,'uid':s.st_uid,
            'target':os.readlink(path) if path.is_symlink() else None}
    return {'root':str(root),'directory':row(root),'files':{p.name:row(p) for p in root.iterdir()}}


def binding(runtime_sha256,installation):
    return {'runtime_sha256':runtime_sha256,'installation_sha256':digest(_read(Path(installation),8*1024**2)),
        'verifier_sha256':digest(pkgutil.get_data('dreamm','database_receipt.py')),'manifest_sha256':FINGERPRINT}


def save(path,data):
    path=Path(path).absolute();_parents(path.parent)
    require(path.parent.stat().st_uid==os.getuid() and not path.parent.stat().st_mode&0o077,
            'DATABASE_UNVERIFIED','verification receipt needs a private owner-only directory')
    fd,pending=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as out:
            out.write(canonical(data))
            out.flush()
            os.fsync(out.fileno())
        os.replace(pending,path)
    finally:
        Path(pending).unlink(missing_ok=True)


def verify(root,manifest,*,runtime_sha256,installation,state,force=False):
    context=binding(runtime_sha256,installation);now=snapshot(root)
    previous={**context,'verifier_sha256':PREVIOUS_VERIFIER}
    if not force and Path(state).exists():
        try:
            info=Path(state).lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077,
                    'DATABASE_UNVERIFIED','verification receipt permissions')
            prior=_json(_read(Path(state)))
            if (prior.get('schema')=='dreamm-verified-database/1' and prior.get('binding') in (context,previous)
                    and prior.get('snapshot')==now and prior['verification']['fully_hashed'] is True):
                checked=verify_database(root,manifest,hash_contents=False)
                require(checked['file_identities']==prior['verification']['file_identities'],
                        'DATABASE_UNVERIFIED','database changed during receipt reuse')
                return {**prior['verification'],'verification_method':'unchanged_verified_receipt',
                    'receipt_sha256':digest(canonical(prior))}
        except (ValueError,OSError,KeyError,TypeError):pass
    verified=verify_database(root,manifest,hash_contents=True)
    require(snapshot(root)==now,'DATABASE_UNVERIFIED','database changed during full verification')
    record={'schema':'dreamm-verified-database/1','binding':context,'snapshot':now,'verification':verified}
    save(state,record)
    return {**verified,'verification_method':'full_sha256','receipt_sha256':digest(canonical(record))}


def seed_from_admission(root,manifest,*,runtime_sha256,installation,state,admission,expected_sha256):
    """One-time migration of our retained full-hash admission, explicitly pinned.

    Requires unchanged inode/size/mtime and a change time no later than that
    successful verification. A filesystem without reliable change times must
    instead perform a full verification. No hash/provenance assertion is guessed.
    """
    path=Path(admission);raw=_read(path,16384)
    require(digest(raw)==expected_sha256,'DATABASE_UNVERIFIED','prior admission checksum differs')
    prior=_json(raw);verified=prior['database_verification']
    require(prior['runtime_sha256']==runtime_sha256 and verified['fully_hashed'] is True
            and verified['manifest_sha256']==FINGERPRINT,'DATABASE_UNVERIFIED','prior full verification/profile missing')
    checked=verify_database(root,manifest,hash_contents=False);now=snapshot(root)
    require(checked['file_identities']==verified['file_identities'],'DATABASE_UNVERIFIED','prior database identity changed')
    completed=path.stat().st_mtime_ns
    require(all(0<r['ctime_ns']<=completed for r in now['files'].values()),
            'DATABASE_UNVERIFIED','database change time is newer than full verification')
    record={'schema':'dreamm-verified-database/1','binding':binding(runtime_sha256,installation),
        'snapshot':now,'verification':verified,'prior_admission_sha256':expected_sha256}
    save(state,record);return record
