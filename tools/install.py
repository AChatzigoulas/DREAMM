"""Install a private, pinned DREAMM v1.1 runtime and the dreamm CLI on Linux."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dreamm.configured import config_path
from dreamm.cli import write_private, sync_directory
from dreamm_m1.capture import _parents


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def command(python, script, *args, site=False):
    print(f'DREAMM install · {Path(script).stem}', flush=True)
    flags=['-I','-B'] if site else ['-I','-S','-B']
    subprocess.run([str(python),*flags,str(ROOT/script),*map(str,args)],
                   cwd=ROOT,stdin=subprocess.DEVNULL,check=True)


def lock_options(name):
    lock = ROOT/'runtime'/name
    return ('--manifest',lock,'--sha256',sha(lock))


def atomic(path, data, executable=False):
    pending = path.with_name(path.name+'.pending')
    write_private(pending,data)
    if executable:
        pending.chmod(0o700)
    os.replace(pending,path)
    sync_directory(path.parent)


def host():
    if sys.platform != 'linux' or platform.machine() != 'x86_64' or os.geteuid() == 0:
        raise ValueError('Install as a normal user on x86-64 Linux')
    if sys.version_info < (3,12):
        raise ValueError('Python 3.12 or later is required to run the installer')
    libc, version = os.confstr('CS_GNU_LIBC_VERSION').split()
    if libc != 'glibc' or tuple(map(int,version.split('.'))) < (2,35):
        raise ValueError('The pinned native build requires glibc 2.35 or newer. Ubuntu 22.04 is the qualified baseline.')
    missing = [name for name in ('git','gcc','g++','make','bwrap') if not shutil.which(name)]
    if missing:
        raise ValueError('Install the host prerequisites first: '+', '.join(missing))
    test = subprocess.run([shutil.which('bwrap'),'--unshare-all','--ro-bind','/usr','/usr',
                           '--ro-bind','/lib64','/lib64','--ro-bind','/lib','/lib',
                           '--die-with-parent','--','/usr/bin/true'],
                          capture_output=True,timeout=15)
    if test.returncode:
        raise ValueError('Unprivileged bubblewrap namespaces are unavailable: '+test.stderr.decode(errors='replace').strip()[:500])


def portable_python(root,cache):
    row=json.loads((ROOT/'runtime/modern-interpreters.json').read_bytes())['artifacts'][0]
    path=cache/row['filename']
    if path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']:
        raise ValueError('Portable Python archive differs from the verified lock')
    tree=root/'build-python'
    python=tree/'python/bin/python3.14'
    marker=tree/'archive.sha256'
    if tree.exists():
        receipt=marker.read_text(encoding='ascii').split()
        if receipt!=[row['sha256'],sha(python)]:
            raise ValueError('Existing build Python receipt differs. Select a fresh installation prefix.')
        return python
    tree.mkdir(mode=0o700)
    with tarfile.open(path,'r:gz') as archive:
        members=archive.getmembers()
        if (len(members)>15000 or sum(item.size for item in members)>512*1024**2
                or any(item.name.split('/')[0]!='python' for item in members)):
            raise ValueError('Portable Python archive layout differs')
        archive.extractall(tree,members=members,filter='data')
    write_private(marker,(row['sha256']+' '+sha(python)+'\n').encode('ascii'))
    return python


def install(args):
    from dreamm.database_receipt import verify as verify_database
    from dreamm_m1.rocky_database import PREFIX
    from dreamm.runtime import runtime_bundle
    from dreamm.ensemble_local import inventory
    from dreamm.ensemble_run import verify_p2rank
    from dreamm.generation import verify_runtime
    from dreamm_m1.capture import _json,_read
    host()
    root=Path(args.prefix).absolute()
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    _parents(root)
    if root.stat().st_uid!=os.getuid() or root.stat().st_mode&0o077:
        raise ValueError('Installation prefix must be private and owned by the current user')
    if shutil.disk_usage(root).free<6*1024**3:
        raise ValueError('Reserve at least 6 GiB on the runtime volume for installation')
    cfg=Path(args.config or config_path()).absolute()
    cfg.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    _parents(cfg.parent)
    if cfg.exists():
        old=_json(_read(cfg,32768))
        if old.get('prefix')!=str(root):
            raise ValueError('Config belongs to another installation. Use a distinct --config.')
        if old.get('profile')=='full':
            args.profile='full'
    runtime=root/'runtime'
    def ready(path,receipt):
        return (path/receipt).is_file()
    if not ready(runtime,'installation.json'):
        if not args.asset_repo or not args.msms_archive:
            raise ValueError('A fresh runtime needs --asset-repo and the separately licensed --msms-archive')
    generation=root/'generation'
    if args.profile=='full' and not ready(generation,'runtime.json') and not args.chimera_installer:
        raise ValueError('Full installation requires the separately licensed --chimera-installer')
    db=Path(args.database).absolute() if args.database else None
    db_lock=ROOT/'runtime/uniclust30-2018_08.json'
    if args.database_volume:
        db=Path(args.database_volume).absolute()/PREFIX
        if not db.is_dir():
            command(sys.executable,'tools/acquire_database.py','--root',db.parent,'--manifest',db_lock)
    if db is None or not db.is_dir():
        raise ValueError('Select a local database with --database or supply 140 GiB on --database-volume')
    caches=root/'cache';caches.mkdir(mode=0o700,exist_ok=True)
    p2rank=root/'p2rank'
    extras=root/'exports-runtime'
    full_missing=args.profile=='full' and any(not ready(path,receipt) for path,receipt in (
        (p2rank,'runtime.json'),(extras,'checkpoint.json'),(generation,'runtime.json')))
    if not ready(runtime,'installation.json') or full_missing:
        command(sys.executable,'tools/acquire_runtime.py','--output',caches,*(['--ensemble'] if full_missing else []))
    if not ready(runtime,'installation.json'):
        python=portable_python(root,caches)
        env=root/'build-env'
        if not ready(env,'installation.json'):
            command(python,'tools/install_modern.py',*lock_options('modern-prody-build-cp314.json'),
                    '--wheelhouse',caches,'--output',env)
        prody=root/'prody-build'
        if not ready(prody,'build.json'):
            command(env/'venv/bin/python','tools/build_prody.py','--source',caches/'prody-2.6.1.tar.gz','--output',prody,site=True)
        msms=Path(args.msms_archive).absolute()
        if msms.name!='msms_i86_64Linux2_2.6.1.tar.gz':
            raise ValueError('Supply the official MSMS archive with its original filename')
        command(sys.executable,'tools/install_trusted.py','--cache',caches,'--cache',prody/'wheels',
               '--cache',msms.parent,'--output',runtime,'--asset-repo',Path(args.asset_repo).absolute(),
               '--prody-build-receipt',prody/'build.json','--prody-build-sha256',sha(prody/'build.json'),
               *(['--resume-incomplete'] if runtime.exists() else []))
    pin=sha(runtime/'runtime.json')
    installed=_json(_read(runtime/'installation.json'))
    if pin!=installed.get('runtime_sha256'):
        raise ValueError('Installed runtime manifest differs from its installation receipt')
    runtime_bundle(_read(runtime/'runtime.json'),expected_sha256=pin).verify(runtime/'tree')
    verify_database(db,_json(_read(db_lock)),runtime_sha256=pin,installation=runtime/'installation.json',
                    state=runtime/'database-verification.json')
    python=runtime/'tree/science/bin/python3.14'
    configuration={'schema':'dreamm-standalone-install/1','profile':args.profile,'prefix':str(root),
        'application':str(root/'dreamm-v1.1.pyz'),'runtime_root':str(runtime/'tree'),
        'runtime_manifest':str(runtime/'runtime.json'),'runtime_sha256':pin,
        'database':str(db),'database_manifest':str(root/'uniclust30-2018_08.json')}
    atomic(Path(configuration['database_manifest']),db_lock.read_bytes())
    if args.profile=='full':
        if not ready(p2rank,'runtime.json'):
            command(sys.executable,'tools/install_p2rank.py',*lock_options('p2rank251-pdb-only.json'),
                    '--cache',caches,'--output',p2rank)
        if not ready(extras,'checkpoint.json'):
            command(python,'tools/install_ensemble_extras.py',*lock_options('ensemble-exports-cp314.json'),
                    '--cache',caches,'--output',extras)
        if not ready(generation,'runtime.json'):
            if not ready(generation,'installation.json'):
                command(sys.executable,'tools/install_generation.py','--chimera-installer',Path(args.chimera_installer).absolute(),
                        '--julia-archive',caches/'julia-1.10.13-linux-x86_64.tar.gz','--output',generation)
            if not (root/'julia-install/installation-process.json').is_file():
                command(sys.executable,'tools/qualify_generation.py','--root',generation,'--evidence',root/'julia-install')
            qualify=_json(_read(root/'julia-install/installation-process.json'))
            if qualify.get('returncode')!=0:
                raise ValueError('Julia installation did not complete. See the julia-install receipt.')
            command(sys.executable,'tools/finalize_generation.py','--root',generation)
        verify_p2rank(p2rank,sha(p2rank/'runtime.json'))
        inventory(extras,_json(_read(extras/'checkpoint.json')),limit=256*1024**2)
        verify_runtime(generation,sha(generation/'runtime.json'))
        configuration.update(ensemble_runtime=str(p2rank),ensemble_sha256=sha(p2rank/'runtime.json'),
            extensions=str(extras),extensions_sha256=sha(extras/'checkpoint.json'),
            generation_runtime=str(generation),generation_sha256=sha(generation/'runtime.json'))
    if (root/'dreamm-v1.1.pyz.pending').exists():
        raise ValueError('An interrupted CLI build exists. Preserve it and use a fresh installation prefix.')
    command(sys.executable,'build.py','--output',root/'dreamm-v1.1.pyz.pending')
    os.replace(root/'dreamm-v1.1.pyz.pending',root/'dreamm-v1.1.pyz')
    bin_dir=root/'bin';bin_dir.mkdir(mode=0o700,exist_ok=True)
    q=shlex.quote
    launch=f'#!/bin/sh\nexec {q(str(python))} -I -S -B {q(configuration["application"])} "$@"\n'
    # Config is discovered automatically by the normal CLI. A nondefault
    # config remains available through --config for independent installs.
    atomic(bin_dir/'dreamm',launch.encode('utf-8'),executable=True)
    atomic(cfg,(json.dumps(configuration,sort_keys=True,indent=2)+'\n').encode('utf-8'))
    print('Installed DREAMM v1.1. Add '+str(bin_dir)+' to PATH, then run dreamm --help.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=('basic','full'),default='basic')
    parser.add_argument('--prefix',default=str(Path.home()/'.local/share/dreamm'))
    parser.add_argument('--config')
    database=parser.add_mutually_exclusive_group(required=True)
    database.add_argument('--database',help='already installed exact UniClust30 2018_08 directory')
    database.add_argument('--database-volume',help='download and verify UniClust30 onto this local volume')
    parser.add_argument('--msms-archive',help='original vendor download acquired under its licence')
    parser.add_argument('--chimera-installer',help='original UCSF Linux headless installer for full profile')
    parser.add_argument('--asset-repo',help='local, lawfully acquired original DREAMM scientific asset checkout')
    args=parser.parse_args()
    root=Path(args.prefix).absolute()
    try:
        root.mkdir(mode=0o700,parents=True,exist_ok=True)
        _parents(root)
        if root.stat().st_uid!=os.getuid() or root.stat().st_mode&0o077:
            raise ValueError('Installation prefix must be private and owned by the current user')
        fd=os.open(root/'.install.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            install(args)
    except (OSError,ValueError,KeyError,subprocess.SubprocessError,tarfile.TarError) as error:
        parser.exit(2, f'DREAMM installation stopped: {error}\n')


if __name__=='__main__':
    main()
