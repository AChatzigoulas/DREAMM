"""Instantiate the fixed modern Julia lock in a private installation namespace.

Networking is enabled only for official Julia package/artifact acquisition.
Scientific generation must run separately with networking disconnected.
"""
import argparse,hashlib,json,os,resource,signal,subprocess,sys,time
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--evidence',required=True)
    a=p.parse_args()
    if sys.platform!='linux' or os.geteuid()==0:raise ValueError('non-root Linux required')
    root=Path(a.root).absolute();out=Path(a.evidence).absolute();out.mkdir(mode=0o700)
    project=root/'project';pins={n:hashlib.sha256((project/n).read_bytes()).hexdigest() for n in ('Project.toml','Manifest.toml')}
    for n in ('depot','home'): (root/n).mkdir(mode=0o700)
    command=out/'instantiate.jl'
    command.write_text('''using Pkg
Pkg.instantiate()
using ProteinEnsembles
println("DREAMM_PROTEINENSEMBLES_SOURCE=", pathof(ProteinEnsembles))
println("DREAMM_JULIA_VERSION=", VERSION)
println("DREAMM_RNG=", typeof(Random.default_rng()))
'''.replace('using ProteinEnsembles','using ProteinEnsembles, Random'),encoding='utf-8')
    base=['/usr/bin/bwrap','--unshare-all','--die-with-parent','--new-session','--cap-drop','ALL',
          '--uid',str(os.geteuid()),'--gid',str(os.getgid()),'--clearenv']
    for path in ('/usr/lib/x86_64-linux-gnu','/usr/lib64'):
        if Path(path).exists():base+=['--ro-bind',path,path]
    base+=['--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--ro-bind',str(root),'/generation',
           '--bind',str(project),'/generation/project','--bind',str(root/'depot'),'/generation/depot',
           '--bind',str(root/'home'),'/generation/home','--bind',str(out),'/evidence',
           '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--chdir','/generation/project',
           '--setenv','HOME','/generation/home','--setenv','PATH','/generation/julia/bin',
           '--setenv','JULIA_DEPOT_PATH','/generation/depot','--setenv','JULIA_NUM_THREADS','1',
           '--setenv','JULIA_NUM_PRECOMPILE_TASKS','1','--setenv','JULIA_PKG_PRECOMPILE_AUTO','0',
           '--setenv','JULIA_CPU_TARGET','generic','--setenv','OPENBLAS_NUM_THREADS','1',
           '--setenv','OMP_NUM_THREADS','1','--setenv','LANG','C.UTF-8',
           '--setenv','JULIA_PKG_SERVER','https://pkg.julialang.org',
           '--share-net','--ro-bind','/etc/resolv.conf','/etc/resolv.conf','--ro-bind','/etc/ssl/certs','/etc/ssl/certs',
           '--remount-ro','/','--','/generation/julia/bin/julia','--startup-file=no','--history-file=no',
           '--compiled-modules=no','--project=/generation/project','/evidence/instantiate.jl']
    def limits():
        resource.setrlimit(resource.RLIMIT_CORE,(0,0));resource.setrlimit(resource.RLIMIT_AS,(8*1024**3,8*1024**3))
        resource.setrlimit(resource.RLIMIT_CPU,(900,900));resource.setrlimit(resource.RLIMIT_FSIZE,(256*1024**2,256*1024**2))
        os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
    started=time.monotonic()
    with (out/'stdout').open('xb') as so,(out/'stderr').open('xb') as se:
        child=subprocess.Popen(base,stdin=subprocess.DEVNULL,stdout=so,stderr=se,start_new_session=True,preexec_fn=limits)
        try:status=child.wait(timeout=1200)
        finally:
            if child.poll() is None:
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=10)
            (out/'installation-process.json').write_text(json.dumps({'returncode':child.returncode,
                'wall_seconds':time.monotonic()-started,'network':'package acquisition only in isolated mount/PID namespace',
                'limits':{'virtual_address_bytes':8*1024**3,'cpu_seconds':900,'cpus':2,'wall_seconds':1200},
                'scientific_generation_performed':False,'project_locks':pins},indent=2),encoding='utf-8')
    if status:raise RuntimeError('Julia instantiation failed; inspect retained stderr')
    if pins!={n:hashlib.sha256((project/n).read_bytes()).hexdigest() for n in pins}:raise RuntimeError('Pkg changed the approved lock')
    print(json.dumps({'instantiation_passed':True,'lock_files_unchanged':True,'scientific_generation_performed':False}))


if __name__=='__main__':main()
