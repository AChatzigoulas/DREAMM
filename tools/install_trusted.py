"""Offline clean modern M1 installation from pinned public artifacts.

Run as a normal Linux user. --cache may be repeated; acquisition is separate.
No OLD-VM tree, package resolver, package hooks, root or system installation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dreamm.installation import install_archive,put
from dreamm.runtime import PINNED
from dreamm.modern_runtime import ModernRuntimeBundle,VERSIONS,LAYOUT
from dreamm_m1.identity import canonical,digest,require
from dreamm_m1.rocky_database import FINGERPRINT

BASE='481055593c5676fb8f2a29c13e776782d772e527'


def locate(caches,name,expected):
    found=[p/name for p in caches if (p/name).is_file()]
    require(found,'INSTALL_INVALID','missing offline artifact: '+name)
    path=found[0];raw=path.read_bytes()
    require(len(raw)<=512*1024**2 and digest(raw)==expected,'INSTALL_INVALID','artifact hash differs: '+name)
    return path


def build_command(cwd, logs, command):
    logpath = logs/('command-'+str(len(list(logs.glob('command-*.log'))))+'.log')
    with logpath.open('xb') as log:
        result = subprocess.run(command,cwd=cwd,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                env={'PATH':os.defpath,'LC_ALL':'C','HOME':str(logs)},timeout=240)
    require(result.returncode==0,'INSTALL_BUILD_FAILED','offline build failed: '+logpath.read_text(errors='replace')[-2048:])


def source_tree(archive,folder):
    folder.mkdir(mode=0o700)
    with tarfile.open(archive) as src:
        prefix=None;count=0;total=0
        for m in src:
            parts=PurePosixPath(m.name).parts;prefix=prefix or parts[0]
            require(parts and parts[0]==prefix and not m.name.startswith('/') and '..' not in parts
                    and '\\' not in m.name and (m.isdir() or m.isfile()),'INSTALL_INVALID','unsafe source archive')
            count+=1;total+=m.size
            require(count<=10000 and total<=64*1024**2 and m.size<=8*1024**2,'INSTALL_INVALID','source archive bound')
            if len(parts)==1:continue
            p=folder.joinpath(*parts[1:]);p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            if m.isdir():p.mkdir(exist_ok=True)
            else:
                with p.open('xb') as out:out.write(src.extractfile(m).read())
                p.chmod(0o700 if m.mode&0o111 else 0o600);os.utime(p,(m.mtime,m.mtime))


def install(args):
    require(sys.platform=='linux' and os.geteuid()>0,'INSTALL_UNAVAILABLE','non-root Linux required')
    caches=[Path(p).absolute() for p in args.cache];target=Path(args.output).absolute()
    resume=args.resume_incomplete
    require(not resume or (target.is_dir() and not target.is_symlink()
            and not (target/'runtime.json').exists() and not (target/'installation.json').exists()),
            'INSTALL_INVALID','only an uncommitted incomplete installation can resume')
    target.mkdir(mode=0o700,exist_ok=resume);tree=target/'tree';tree.mkdir(mode=0o700,exist_ok=resume)
    files,links,origins={}, {}, {};sources=[]
    def add(name,raw,executable=False):put(tree,name,raw,executable,files,origins,collect=True)
    native=json.loads((ROOT/'runtime/trusted-native.json').read_bytes())
    if args.prody_build_receipt:
        raw=Path(args.prody_build_receipt).read_bytes()
        require(args.prody_build_sha256 and digest(raw)==args.prody_build_sha256,
                'INSTALL_INVALID','explicit target-specific build-receipt hash required')
        build=json.loads(raw)
        require(build.get('schema')=='dreamm-prody-build/1'
                and build.get('source_sha256')==native['prody_wheel']['source_sha256']
                and build.get('python','').startswith('3.14.10 ')
                and build.get('wheel')=='prody-2.6.1-cp314-cp314-linux_x86_64.whl',
                'INSTALL_INVALID','pinned ProDy source/version/interpreter build required')
        native['prody_wheel']={**native['prody_wheel'],'filename':build['wheel'],
            'sha256':build['wheel_sha256'],'build_receipt_sha256':digest(raw),
            'qualification':'target_specific_source_build_requires_frozen_scientific_acceptance'}
    def archive(row,destination,fmt):
        print(json.dumps({'phase':'install','artifact':row['filename'],'destination':destination}),flush=True)
        p=locate(caches,row['filename'],row['sha256'])
        item={**row,'destination':destination,'format':fmt}
        install_archive(p,tree,item,files,links,origins,collect=True)
        sources.append({k:row[k] for k in ('filename','sha256','url')})
    interpreter=json.loads((ROOT/'runtime/modern-interpreters.json').read_bytes())['artifacts'][0]
    for name in ('science','charge','dynamics'):archive(interpreter,name,'tar-gz-strip-one')
    archive(native['java'],'jdk','tar-gz-strip-one')
    for name,manifest in [('science','modern-structure-cp314.json'),('charge','modern-charge-cp314.json'),('dynamics','modern-prody-build-cp314.json')]:
        data=json.loads((ROOT/'runtime'/manifest).read_bytes())
        for row in data['wheels']:archive(row,name+'/lib/python3.14/site-packages','wheel')
    archive(native['prody_wheel'],'dynamics/lib/python3.14/site-packages','wheel')
    # Native packages are data archives: install the exact required ELF/library
    # files only; no package scripts or compatibility Python runtimes execute.
    for key,names,destination in [('hhsuite',{'bin/hhblits':'bin/hhblits',
            **{'data/'+n:'data/'+n for n in ('cs219.lib','context_data.lib','context_data.crf')}},'native'),
            ('dssp',{'bin/mkdssp':'bin/mkdssp'},'native'),
            ('boost',{f'lib/libboost_{n}.so.1.73.0':f'lib/libboost_{n}.so.1.73.0' for n in
                ('thread','filesystem','program_options','iostreams','system','chrono','atomic','date_time')},'native'),
            ('msms',{'msms.x86_64Linux2.2.6.1':'bin/msms'},'native')]:
        row=native[key];p=locate(caches,row['filename'],row['sha256']);sources.append(row)
        with tarfile.open(p) as src:
            for name,out in names.items():
                m=src.getmember(name);require(m.isfile() and m.size<=32*1024**2,'INSTALL_INVALID','native package member differs')
                add(destination+'/'+out,src.extractfile(m).read(),True)
    # Published repository blobs are read by exact historical commit, not from
    # arbitrary working-tree files. This includes the unmodified original model.
    public={'model':('data/final_classifier.sav','assets/model.sav'),
        'reference':('data/dataset_res_depth<2.5_selected.csv','assets/reference.csv'),
        'zeroed':('data/unimport_feats.csv','assets/zeroed.csv'),
        'protdcal_jar':('dreamm/ML/ProtDCal_v4.5/ProtDCal.jar','protdcal/ProtDCal.jar'),
        'project':('dreamm/ML/ProtDCal_v4.5/Projects/dreamm.proj','protdcal/reference.proj'),
        'parameter':('dreamm/ML/ProtDCal_v4.5/parameter.cfg','protdcal/parameter.cfg'),
        'groups':('dreamm/ML/ProtDCal_v4.5/groups.gdm','protdcal/groups.gdm'),
        'indices':('dreamm/ML/ProtDCal_v4.5/PropertyIndices.idx','protdcal/PropertyIndices.idx')}
    asset_repo=Path(getattr(args,'asset_repo',None) or ROOT).resolve()
    for role,(source,out) in public.items():
        raw=subprocess.run(['git','-C',str(asset_repo),'show',BASE+':'+source],
            capture_output=True,check=True,timeout=30).stdout
        require(digest(raw)==PINNED[role],'INSTALL_INVALID','public protected asset differs: '+role);add(out,raw)
    classpath=json.loads((ROOT/'runtime/protdcal-classpath.json').read_bytes())
    for row in classpath:
        raw=subprocess.run(['git','-C',str(asset_repo),'show',BASE+':'+row['path']],
            capture_output=True,check=True,timeout=30).stdout
        require(digest(raw)==row['sha256'],'INSTALL_INVALID','original Java dependency differs')
        add('protdcal/lib/'+Path(row['path']).name,raw)
    for role,path in [('kernel','src/dreamm_m1/wclq.py'),('weights','src/dreamm/data/wclq-weights.json'),
            ('schema_json','src/dreamm/data/protdcal-schema.json'),('schema_tsv','src/dreamm/data/protdcal-schema.tsv')]:
        raw=(ROOT/path).read_bytes();require(digest(raw)==PINNED[role],'INSTALL_INVALID','DREAMM helper data differs')
        add('compiled/'+Path(path).name,raw)
    with tempfile.TemporaryDirectory(prefix='dreamm-native-build-') as temp:
        temp=Path(temp);classes=temp/'classes';classes.mkdir()
        build_command(temp,classes,
            [str(tree/'jdk/bin/javac'),'-J-Xmx512m','--release','8','-proc:none','-g:none','-encoding','UTF-8',
             '-classpath',str(tree/'protdcal/ProtDCal.jar'),'-d',str(classes),str(ROOT/'java/src/org/dreamm/m1/WclqFinish.java')])
        import io
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as jar:
            name='org/dreamm/m1/WclqFinish.class';info=zipfile.ZipInfo(name,(1980,1,1,0,0,0))
            info.create_system=3;info.external_attr=2166620160;jar.writestr(info,(classes/name).read_bytes())
        raw=buffer.getvalue();require(digest(raw)==PINNED['helper'],'INSTALL_INVALID','compiled helper is not reproducible')
        add('compiled/dreamm-wclq.jar',raw)
        row=native['freesasa'];p=locate(caches,row['filename'],row['sha256']);src=temp/'freesasa';source_tree(p,src)
        build_command(src,src,['./configure','--disable-json','--disable-xml'])
        build_command(src,src,['make','-j','2'])
        add('native/bin/freesasa',(src/'src/freesasa').read_bytes(),True)
        for log in src.glob('command-*.log'):(target/('freesasa-'+log.name)).write_bytes(log.read_bytes())
    dependency_lock={'schema':'dreamm-modern-closure/1',
        'structure_manifest_sha256':digest((ROOT/'runtime/modern-structure-cp314.json').read_bytes()),
        'charge_manifest_sha256':digest((ROOT/'runtime/modern-charge-cp314.json').read_bytes()),
        'prody_source_version':'2.6.1','old_vm_files_used':False,'sources':sources,
        'public_asset_commit':BASE,'builds':['Java --release 8 -proc:none -g:none','FreeSASA --disable-json --disable-xml'],
        'system_native_abi':'Linux x86_64 glibc; FreeSASA and ProDy are built for the installation host'}
    add('dependency-lock.json',canonical(dependency_lock))
    roles={role:out for role,(_,out) in public.items()};roles.update(LAYOUT)
    roles.update(java='jdk/bin/java',hhblits='native/bin/hhblits',dssp='native/bin/mkdssp',
        msms='native/bin/msms',freesasa='native/bin/freesasa',helper='compiled/dreamm-wclq.jar',
        kernel='compiled/wclq.py',weights='compiled/wclq-weights.json',schema_json='compiled/protdcal-schema.json',
        schema_tsv='compiled/protdcal-schema.tsv',dependency_lock='dependency-lock.json')
    manifest={'schema':'dreamm-modern-runtime/1','runtime_id':'modern-m1-trusted-linux-v1','versions':VERSIONS,
        'roles':roles,'files':files,'links':links,'database_fingerprint':FINGERPRINT,'hhblits_threads':15}
    raw=canonical(manifest);bundle=ModernRuntimeBundle(raw,expected_sha256=digest(raw));verification=bundle.verify(tree)
    (target/'runtime.json').write_bytes(raw)
    receipt={'schema':'dreamm-trusted-installation/1','runtime_sha256':digest(raw),'verification':verification,
        'old_vm_files_used':False,'package_hooks_executed':False,'scientific_prediction_performed':False,
        'installer_sha256':digest(Path(__file__).read_bytes()),'compiler':subprocess.run(['gcc','--version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]}
    (target/'installation.json').write_bytes(canonical(receipt));return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cache',action='append',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--asset-repo',help='trusted local Git checkout containing the pinned public asset commit')
    p.add_argument('--resume-incomplete',action='store_true',help='rehash/reconstruct an uncommitted partial installation; never skip validation')
    p.add_argument('--prody-build-receipt',help='explicit separately built target-ABI wheel provenance; never an automatic fallback')
    p.add_argument('--prody-build-sha256',help='SHA256 of that exact source-build receipt')
    print(json.dumps(install(p.parse_args()),indent=2))
