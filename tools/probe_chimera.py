"""Offline Chimera 1.19 startup check in a private rootless namespace."""
import argparse,json,os,resource,signal,subprocess,tempfile,time
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--evidence',required=True);a=p.parse_args()
    if os.geteuid()==0:raise ValueError('non-root qualification required')
    root=Path(a.root).absolute();out=Path(a.evidence).absolute();out.mkdir(mode=0o700)
    launcher=root/'chimera/bin/chimera';magic=launcher.read_bytes()[:120]
    with tempfile.TemporaryDirectory(prefix='dreamm-chimera-probe-') as t:
        work=Path(t);(work/'home').mkdir();argv=['/usr/bin/bwrap','--unshare-all','--die-with-parent','--new-session',
          '--cap-drop','ALL','--uid',str(os.geteuid()),'--gid',str(os.getgid()),'--clearenv']
        for path in ('/usr/lib/x86_64-linux-gnu','/usr/lib64'):
            if Path(path).exists():argv+=['--ro-bind',path,path]
        argv+=['--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
          '--ro-bind','/usr/bin','/usr/bin','--symlink','usr/bin','/bin',
          '--ro-bind',str(root),'/generation','--bind',str(work),'/work','--proc','/proc','--dev','/dev',
          '--chdir','/work','--setenv','HOME','/work/home','--setenv','TMPDIR','/work',
          '--setenv','PATH','/generation/chimera/bin','--remount-ro','/',
          '--','/generation/chimera/bin/chimera','--nogui','--version']
        def limits():
            resource.setrlimit(resource.RLIMIT_CORE,(0,0));resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
            resource.setrlimit(resource.RLIMIT_CPU,(30,30));resource.setrlimit(resource.RLIMIT_FSIZE,(8*1024**2,8*1024**2))
        started=time.monotonic()
        with (out/'stdout').open('xb') as so,(out/'stderr').open('xb') as se:
            child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=so,stderr=se,start_new_session=True,preexec_fn=limits)
            try:status=child.wait(timeout=40)
            finally:
                if child.poll() is None:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=10)
        receipt={'returncode':status,'wall_seconds':time.monotonic()-started,'network':'private disconnected namespace',
                 'launcher_prefix_hex':magic.hex(),'scientific_preparation_performed':False}
        (out/'receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
        if status:raise RuntimeError('Chimera startup failed; inspect retained stderr')


if __name__=='__main__':main()
