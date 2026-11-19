"""Instantiate the pinned Julia packages in a private, user-owned depot."""
import argparse,hashlib,json,os,signal,subprocess,sys,time
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
    argv = [str(root/'julia/bin/julia'),'--startup-file=no','--history-file=no','--compiled-modules=no',
            '--project='+str(project),str(command)]
    env = {key: value for key,value in os.environ.items()
           if key.lower() in ('http_proxy','https_proxy','no_proxy','ssl_cert_file','ssl_cert_dir')}
    env.update(HOME=str(root/'home'), PATH=os.defpath, LANG='C.UTF-8',
               JULIA_DEPOT_PATH=str(root/'depot'),JULIA_NUM_THREADS='1',JULIA_NUM_PRECOMPILE_TASKS='1',
               JULIA_PKG_PRECOMPILE_AUTO='0',JULIA_CPU_TARGET='generic',OPENBLAS_NUM_THREADS='1',
               OMP_NUM_THREADS='1',JULIA_PKG_SERVER='https://pkg.julialang.org')
    started=time.monotonic()
    with (out/'stdout').open('xb') as so,(out/'stderr').open('xb') as se:
        child=subprocess.Popen(argv,cwd=project,env=env,stdin=subprocess.DEVNULL,stdout=so,stderr=se,start_new_session=True)
        try:status=child.wait(timeout=1200)
        finally:
            if child.poll() is None:
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=10)
            (out/'installation-process.json').write_text(json.dumps({'returncode':child.returncode,
                'wall_seconds':time.monotonic()-started,'network':'Julia package acquisition','timeout_seconds':1200,
                'scientific_generation_performed':False,'project_locks':pins},indent=2),encoding='utf-8')
    if status:raise RuntimeError('Julia instantiation failed; inspect retained stderr')
    if pins!={n:hashlib.sha256((project/n).read_bytes()).hexdigest() for n in pins}:raise RuntimeError('Pkg changed the approved lock')
    print(json.dumps({'instantiation_passed':True,'lock_files_unchanged':True,'scientific_generation_performed':False}))


if __name__=='__main__':main()
