"""Offline extraction of separately obtained Chimera 1.19 and Julia 1.10.13.

No installer or scientific code executes. The owner/researcher must obtain and
review the vendor licence themselves. ProteinEnsembles installation is separate.
"""
import argparse,hashlib,io,json,os,posixpath,shutil,stat,sys,tarfile,zipfile
from pathlib import Path,PurePosixPath

PINS={'chimera':('0d3cdf8132ae4ef9a4240b31ec123af5a4cb186b94ec0fdd680009b6b29c989d',149448194),
      'julia':('f2bfa080db29b50a9bf250f69e9acdcfd8b7c49409dee5e55588f986706fb8bd',175038520)}
DOC={'include/freetype':'/usr/include/freetype2/freetype','include/ft2build.h':'/usr/include/ft2build.h'}


def checked(path,kind):
    path=Path(path);s=path.lstat();expected,size=PINS[kind]
    if not stat.S_ISREG(s.st_mode) or s.st_size!=size:raise ValueError('vendor artifact type/size differs')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('vendor artifact SHA256 differs')
    return raw


def safe(name):
    p=PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name:raise ValueError('unsafe archive path')
    return p


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('chimera-installer','julia-archive','output'):p.add_argument('--'+n,required=True)
    a=p.parse_args()
    if sys.platform!='linux' or os.geteuid()==0:raise ValueError('non-root Linux required')
    vendor=checked(a.chimera_installer,'chimera');julia=checked(a.julia_archive,'julia')
    target=Path(a.output).absolute()
    if shutil.disk_usage(target.parent).free<3*1024**3:raise ValueError('3 GiB installation headroom required')
    target.mkdir(mode=0o700);destination=target/'chimera';destination.mkdir(mode=0o700)
    with zipfile.ZipFile(io.BytesIO(vendor)) as outer:
        if outer.namelist()!=['installer','chimera.bin']:raise ValueError('vendor installer layout differs')
        with zipfile.ZipFile(io.BytesIO(outer.read('chimera.bin'))) as inner:
            entries=inner.infolist()
            if len(entries)!=12239 or sum(i.file_size for i in entries)!=472343377:raise ValueError('vendor payload bound differs')
            names={i.filename.rstrip('/') for i in entries}
            if len(names)!=len(entries):raise ValueError('duplicate vendor name')
            links={}
            for item in entries:
                name=item.filename.rstrip('/');safe(name);mode=item.external_attr>>16;path=destination/name
                if item.is_dir():path.mkdir(parents=True,exist_ok=True,mode=0o700);continue
                if stat.S_ISLNK(mode):
                    link=inner.read(item).decode('ascii')
                    if name in DOC:
                        if link!=DOC[name]:raise ValueError('external documentation link differs')
                        continue
                    resolved=posixpath.normpath(posixpath.join(posixpath.dirname(name),link));safe(resolved)
                    if resolved not in names:raise ValueError('unresolved vendor alias')
                    links[name]=link;continue
                if not stat.S_ISREG(mode) or item.file_size>32*1024**2:raise ValueError('vendor file type/size differs')
                path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with path.open('xb') as stream:stream.write(inner.read(item))
                path.chmod(0o700 if mode&0o111 else 0o600)
            for name,link in links.items():(destination/name).symlink_to(link)
    with tarfile.open(fileobj=io.BytesIO(julia),mode='r:gz') as archive:
        members=archive.getmembers()
        if len(members)>20000 or sum(m.size for m in members)>1024**3:raise ValueError('Julia archive bound')
        for m in members:
            safe(m.name)
            if PurePosixPath(m.name).parts[0]!='julia-1.10.13':raise ValueError('Julia prefix differs')
        archive.extractall(target,filter='data')
    (target/'julia-1.10.13').rename(target/'julia')
    project=target/'project';project.mkdir(mode=0o700)
    for name in ('Project.toml','Manifest.toml'):
        shutil.copyfile(Path(__file__).resolve().parents[1]/'runtime/julia'/name,project/name)
    receipt={'schema':'dreamm-generation-uninstantiated/1','chimera':'1.19_b42556','julia':'1.10.13',
             'sources':{k:{'sha256':v[0],'bytes':v[1]} for k,v in PINS.items()},
             'omitted_external_documentation_links':DOC,'scientific_execution_performed':False,
             'ProteinEnsembles_instantiation_required':True}
    (target/'installation.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(target),'offline_extraction_complete':True,'scientific_runtime_ready':False}))


if __name__=='__main__':main()
