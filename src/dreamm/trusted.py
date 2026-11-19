"""One trusted local Linux attempt, independent of web/systemd/cgroup services.

This is deliberately NOT the public upload worker. A private PID/network/mount
namespace confines descendants and filesystem access. Local aggregate memory,
task and scratch bounds are sampled abort policies, not kernel cgroup quotas.
The first model must pass the bounded single-chain reference admission.
"""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dreamm_m1.local_budget import artifact_bytes, artifact_count, scratch_bytes, source_bytes

from .cli import prepare, read_input, write_private, sync_directory
from .runtime import runtime_bundle
from dreamm_m1.capture import _json, _read, _parents
from dreamm_m1.identity import canonical, digest, require
from dreamm_m1.mmcif_input import CIF_1RLW_SHA256
from dreamm_m1.rocky_database import FINGERPRINT, PREFIX

MEMORY, SCRATCH, RESERVE = 12*1024**3, 512*1024**2, 2*1024**3
NAMESPACES = ('mnt','pid','net','user','ipc','uts')


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


def available_memory():
    values=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    return int(values['MemAvailable'].split()[0])*1024


def processes():
    rows={}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:
            raw=(path/'stat').read_text();fields=raw[raw.rfind(')')+2:].split()
            status=dict(x.split(':',1) for x in (path/'status').read_text().splitlines() if ':' in x)
            rows[int(path.name)]={'ppid':int(fields[1]),'start':int(fields[19]),
                'rss':int(status.get('VmRSS','0 kB').split()[0])*1024,
                'threads':int(status.get('Threads','1')),'state':fields[0]}
        except (OSError,ValueError,KeyError):continue  # raced process exit
    return rows


def descendants(rows, parent, known):
    found={pid for pid,start in known.items() if pid in rows and rows[pid]['start']==start}
    if parent in rows:found.add(parent)
    while True:
        more={pid for pid,row in rows.items() if row['ppid'] in found}
        if more<=found:return found
        found|=more


def scratch_usage(root):
    total,count=0,0
    for directory,folders,names in os.walk(root,followlinks=False):
        for name in folders:
            require(not (Path(directory)/name).is_symlink(),'RESOURCE_LIMIT','linked scratch directory')
        for name in names:
            try:info=(Path(directory)/name).lstat()
            except FileNotFoundError:continue  # tool removed its temporary file after os.walk
            require(stat.S_ISREG(info.st_mode) and info.st_size<=artifact_bytes(),'RESOURCE_LIMIT',
                    'scratch file type/size: '+str((Path(directory)/name).relative_to(root))+
                    ' mode='+oct(info.st_mode)+' bytes='+str(info.st_size))
            total+=info.st_size;count+=1
            require(total<=scratch_bytes() and count<=artifact_count(),'RESOURCE_LIMIT','scratch aggregate bound')
    return total


def child_limits(processes=15):
    import ctypes
    if ctypes.CDLL(None,use_errno=True).prctl(38,1,0,0,0):raise OSError('no-new-privileges failed')
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_FSIZE,(artifact_bytes(),artifact_bytes()))
    # HHblits maps the 86.4 GiB database. Virtual address space is NOT RSS.
    resource.setrlimit(resource.RLIMIT_AS,(256*1024**3,256*1024**3))
    resource.setrlimit(resource.RLIMIT_NOFILE,(512,512))
    resource.setrlimit(resource.RLIMIT_CPU,(270000,270000))
    cpus=sorted(os.sched_getaffinity(0))[:processes]
    os.sched_setaffinity(0,cpus)


def namespace_command(launcher, runtime, application, inputs, work, database, entry='dreamm.trusted_entry'):
    argv=[str(launcher),'--unshare-all','--die-with-parent','--new-session','--cap-drop','ALL',
          '--uid',str(os.getuid()),'--gid',str(os.getgid()),'--clearenv']
    # Trusted distribution libraries; no host home, /etc credentials, or sockets.
    for path in ('/usr/lib/x86_64-linux-gnu','/usr/lib64'):
        if Path(path).exists():argv+=['--ro-bind',path,path]
    argv+=['--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
           '--ro-bind',str(runtime),'/runtime','--ro-bind',str(application),'/code/dreamm.pyz',
           '--ro-bind',str(inputs),'/inputs','--ro-bind',str(database),'/database',
           '--bind',str(work),'/work','--proc','/proc','--dev','/dev','--remount-ro','/',
           '--chdir','/work','--setenv','HOME','/nonexistent','--setenv','PATH','/runtime/science/bin:/runtime/jdk/bin',
           '--setenv','TMPDIR','/work/tmp','--setenv','LC_ALL','C','--setenv','PYTHONDONTWRITEBYTECODE','1',
           '--setenv','OPENBLAS_NUM_THREADS','16','--setenv','OMP_NUM_THREADS','16',
           '--setenv','DREAMM_TRUSTED_BUDGET',f'{source_bytes()}:{artifact_bytes()}:{scratch_bytes()}',
           '--','/runtime/science/bin/python3.14','-I','-S','-B','-c',
           "import sys;sys.path.insert(0,'/code/dreamm.pyz');from "+entry+" import main;main()"]
    return argv


def supervise(argv, work, evidence, timeout, *, cpu_processes=15):
    """Bounded serial foreground supervisor; PID namespace kills escaped groups."""
    started=time.monotonic();known={};samples=[];reason=None;peak=0;cpu_before=resource.getrusage(resource.RUSAGE_CHILDREN)
    import ctypes
    libc=ctypes.CDLL(None,use_errno=True);previous=ctypes.c_int()
    require(libc.prctl(37,ctypes.byref(previous),0,0,0)==0 and libc.prctl(36,1,0,0,0)==0,
            'LOCAL_ADMISSION_FAILED','local child-subreaper unavailable')
    old_handlers={}
    for name in (signal.SIGTERM,signal.SIGHUP):
        old_handlers[name]=signal.signal(name,lambda number,frame:(_ for _ in ()).throw(KeyboardInterrupt()))
    with (evidence/'worker.stdout').open('xb') as out,(evidence/'worker.stderr').open('xb') as err:
        child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=out,stderr=err,
            start_new_session=True,preexec_fn=lambda: child_limits(cpu_processes))
        try:
            while True:
                rows=processes();pids=descendants(rows,child.pid,known)
                known.update({pid:rows[pid]['start'] for pid in pids})
                rss=sum(rows[p]['rss'] for p in pids);threads=sum(rows[p]['threads'] for p in pids)
                available=available_memory();size=scratch_usage(work);peak=max(peak,rss)
                sample={'elapsed':time.monotonic()-started,'rss_sum_bytes':rss,'threads':threads,
                        'host_available_bytes':available,'scratch_bytes':size,'pids':sorted(pids)}
                samples.append(sample)
                if len(samples)%10==0:
                    with (evidence/'monitor.jsonl').open('ab') as log:
                        for row in samples:log.write(canonical(row)+b'\n')
                    samples=[]
                require(rss<=MEMORY and threads<=128 and available>=RESERVE,
                        'RESOURCE_LIMIT','trusted-local RSS/tasks/host reserve abort')
                require(time.monotonic()-started<=timeout,'RESOURCE_LIMIT','total local deadline exceeded')
                if child.poll() is not None:break
                time.sleep(.5)
        except BaseException as exc:
            reason=str(exc)
            raise
        finally:
            if child.poll() is None:
                try:os.killpg(child.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    child.wait(timeout=10)
            # Namespace teardown and orphan adoption are asynchronous. Reap only
            # this attempt's remembered descendants; never signal by name.
            deadline=time.monotonic()+5
            while True:
                rows=processes()
                known.update({pid:row['start'] for pid,row in rows.items() if row['ppid']==os.getpid()})
                remaining={pid for pid,start in known.items() if pid in rows and rows[pid]['start']==start}
                for pid in remaining:
                    try:os.waitpid(pid,os.WNOHANG)
                    except ChildProcessError:pass
                rows=processes();remaining={pid for pid,start in known.items() if pid in rows and rows[pid]['start']==start}
                if not remaining or time.monotonic()>=deadline:break
                time.sleep(.05)
            usage=resource.getrusage(resource.RUSAGE_CHILDREN)
            receipt={'schema':'dreamm-trusted-supervision/1','returncode':child.returncode,'failure':reason,
                'wall_seconds':time.monotonic()-started,'cpu_seconds':usage.ru_utime+usage.ru_stime-cpu_before.ru_utime-cpu_before.ru_stime,
                'peak_sampled_rss_sum_bytes':peak,'process_tree_remaining':sorted(remaining),
                'memory_enforcement':'sampled_RSS_abort_not_cgroup','namespace_cleanup_verified':not remaining,
                'maxrss_children_kib':usage.ru_maxrss}
            (evidence/'supervision.json').write_bytes(canonical(receipt))
            with (evidence/'monitor.jsonl').open('ab') as log:
                for row in samples:log.write(canonical(row)+b'\n')
            for name,handler in old_handlers.items():signal.signal(name,handler)
            libc.prctl(36,previous.value,0,0,0)
            require(not remaining,'LOCAL_CLEANUP_FAILED','known worker descendants remain')
    require(child.returncode==0,'LOCAL_WORKER_FAILED','worker failed; inspect retained worker.stderr')
    return receipt


def run(args):
    require(sys.platform=='linux' and os.geteuid()!=0 and sys.flags.isolated,
            'LOCAL_UNAVAILABLE','non-root isolated Python on Linux required')
    from .cli import check_selection, stage_timeouts
    from .modern_pipeline import SECONDS
    require(args.runtime_profile=='modern' and args.mode in ('M1','M2'),
            'LOCAL_MODE_UNQUALIFIED','modern trusted-local M1/M2 required')
    source_format=getattr(args,'source_format',None) or ('mmcif' if args.mode=='M1' else 'pdb')
    chains=getattr(args,'chains',None)
    cpu_processes=getattr(args,'processes',None)
    if cpu_processes is None:cpu_processes=15
    require(1<=cpu_processes<=15,'RESOURCE_LIMIT','CPU affinity bound must be 1..15')
    check_selection(args.mode,source_format,args.pdb_id,chains,None,trusted_input=True)
    require(1<=args.timeout<=86400,'RESOURCE_LIMIT','local deadline 1..86400 seconds')
    deadlines = stage_timeouts(args,{**SECONDS,'inference':180})
    raw=read_input(args.input,source_bytes())
    require(digest(raw)==args.sha256,'SOURCE_INVALID','source SHA-256 differs')
    if source_format=='pdb':
        from .modern_pipeline import admit_reference
        admit_reference(raw)
    runtime=Path(args.runtime_root).absolute();bundle=runtime_bundle(_read(Path(args.runtime_manifest)),expected_sha256=args.runtime_sha256)
    require(bundle.schema=='dreamm-modern-runtime/1','RUNTIME_INVALID','modern runtime required')
    bundle.verify(runtime)
    database=Path(args.database).absolute();db_manifest=_json(_read(Path(args.database_manifest)))
    output=Path(args.output).absolute();_parents(output.parent);output.mkdir(mode=0o700)
    lockfd=os.open('/tmp/dreamm-trusted-'+str(os.getuid())+'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        require(os.fstat(lockfd).st_uid==os.getuid(),'LOCAL_ADMISSION_FAILED','local worker lock owner differs')
        fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        from .database_receipt import verify as verify_local_database
        verified_db=verify_local_database(database,db_manifest,runtime_sha256=bundle.sha256,
            installation=Path(args.runtime_manifest).absolute().parent/'installation.json',
            state=Path(args.runtime_manifest).absolute().parent/'database-verification.json',
            force=getattr(args,'full_database_verify',False))
        require(available_memory()>=MEMORY+RESERVE,'RESOURCE_LIMIT','need 14 GiB available host RAM for this initial profile')
        launcher=Path(args.launcher or '/usr/bin/bwrap').absolute()
        require(launcher.is_file() and not launcher.stat().st_mode&0o022,'LOCAL_ADMISSION_FAILED','trusted non-writable bubblewrap required')
        if args.launcher_sha256:require(digest(_read(launcher,1024**2))==args.launcher_sha256,'LOCAL_ADMISSION_FAILED','launcher differs')
        application=Path(args.application or sys.argv[0]).absolute()
        require(application.is_file() and application.suffix=='.pyz','LOCAL_ADMISSION_FAILED','built source zipapp required')
        with tempfile.TemporaryDirectory(prefix='dreamm-local-') as scratch:
            scratch=Path(scratch);inputs=scratch/'inputs';inputs.mkdir(mode=0o700)
            work=scratch/'work';work.mkdir(mode=0o700)
            require(shutil.disk_usage(scratch).free>=2*scratch_bytes() and shutil.disk_usage(output).free>=scratch_bytes(),
                    'RESOURCE_LIMIT','free space must cover scratch and retained output')
            prepare(argparse.Namespace(mode=args.mode,format=source_format,pdb_id=args.pdb_id,chains=chains,additional=None,
                input=args.input,sha256=args.sha256,output=inputs/'request',trusted_input=True))
            config={'schema':'dreamm-trusted-admission/1','attempt':uuid.uuid4().hex,
                'runtime_sha256':bundle.sha256,'source_sha256':args.sha256,
                'application_sha256':digest(_read(application)), 'runtime_profile':'modern',
                'mode':args.mode,'chains':chains,'source_format':source_format,
                'source_bytes':source_bytes(),'artifact_bytes':artifact_bytes(),'cpu_affinity_max':cpu_processes,
                'host_namespaces':{n:os.readlink('/proc/self/ns/'+n) for n in NAMESPACES},
                'database_verification':verified_db,'memory':MEMORY,'scratch':scratch_bytes(),'timeout':args.timeout,
                'stage_deadlines':deadlines,
                'launcher_sha256':digest(_read(launcher,1024**2)), 'trusted_input_only':True}
            for name,data in (('admission.json',config),('runtime.json',bundle.data),('database.json',db_manifest)):
                write_private(inputs/name,canonical(data))
            write_private(output/'admission.json',canonical(config))
            try:
                receipt=supervise(namespace_command(launcher,runtime,application,inputs,work,database),work,output,args.timeout,
                    cpu_processes=cpu_processes)
                from .trusted_entry import validate_results
                summary,manifest=validate_results(work,config)
                shutil.copytree(work,output/'results',symlinks=False)
                write_private(output/'artifacts.json',canonical(manifest))
                write_private(output/'.complete.pending',canonical({'summary':summary,'supervision':receipt,
                    'artifact_manifest_sha256':digest(canonical(manifest))}))
                os.rename(output/'.complete.pending',output/'complete.json');sync_directory(output)
            except BaseException:
                # Preserve bounded failed-stage diagnostics, never publish success.
                if not (output/'failed-artifacts').exists():
                    try:scratch_usage(work);shutil.copytree(work,output/'failed-artifacts')
                    except (OSError,ValueError):pass
                raise
        return {'completed':True,'output':str(output),'prediction':summary,'supervision':receipt}
    finally:
        os.close(lockfd)
