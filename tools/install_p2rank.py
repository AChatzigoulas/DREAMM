"""Offline install of the previously qualified PDB-only P2Rank 2.5.1 closure.

Never modifies a previous installation or executes the original shell launcher.
Java 21 is supplied by the independently verified modern DREAMM runtime.
"""
import argparse,gzip,hashlib,json,os
from pathlib import Path,PurePosixPath
import tarfile,zipfile,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dreamm_m1.identity import canonical,digest,require
from dreamm_m1.capture import _read,_json,_parents
from dreamm.runtime import relative_path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('manifest','sha256','output'):p.add_argument('--'+n,required=True)
    p.add_argument('--cache',action='append',required=True)
    a=p.parse_args();require(sys.platform=='linux' and os.geteuid()>0,'INSTALL_UNAVAILABLE','non-root Linux required')
    raw=_read(Path(a.manifest));require(digest(raw)==a.sha256,'INSTALL_INVALID','manifest checksum differs')
    lock=_json(raw);require(lock['schema']=='dreamm-p2rank-install/1' and lock['version']=='2.5.1'
        and lock['release_sha256']=='d243f2d9036ac053fefb9407b5fe1c85f4fe077c519fd975ac585e995feab274',
        'INSTALL_INVALID','approved release required')
    artifacts={}
    for name,pin in lock['artifacts'].items():
        require(Path(name).name==name,'INSTALL_INVALID','artifact basename required')
        matches=[Path(c)/name for c in a.cache if (Path(c)/name).is_file()]
        require(matches,'INSTALL_INVALID','missing externally acquired artifact: '+name)
        path=matches[0];data=_read(path,512*1024**2)
        require(len(data)==pin['bytes'] and digest(data)==pin['sha256'],'INSTALL_INVALID','artifact identity differs')
        if 'sha512' in pin:require(hashlib.sha512(data).hexdigest()==pin['sha512'],'INSTALL_INVALID','vendor SHA512 differs')
        artifacts[name]=path
    target=Path(a.output).absolute();_parents(target.parent);target.mkdir(mode=0o700);written={}
    def put(name,data):
        relative_path(name);pin=lock['files'][name]
        require(name not in written and len(data)<=64*1024**2 and pin=={'bytes':len(data),'sha256':digest(data)},
                'INSTALL_INVALID','unreviewed or duplicate installed bytes: '+name)
        path=target/name;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with path.open('xb') as f:f.write(data)
        written[name]=pin
    special=lambda n:n.startswith('ccd/') or Path(n).name.startswith(('log4j-','guava-'))
    wanted={n for n in lock['files'] if not special(n)}
    with tarfile.open(artifacts['p2rank_2.5.1.tar.gz'],'r|gz') as archive:
        count=0
        for m in archive:
            count+=1;require(count<=10000,'INSTALL_INVALID','archive entry bound')
            path=PurePosixPath(m.name);require(not path.is_absolute() and '..' not in path.parts,
                                             'INSTALL_INVALID','unsafe archive path')
            prefix,_,name=m.name.partition('/')
            require(prefix=='p2rank_2.5.1','INSTALL_INVALID','release archive prefix differs')
            if name not in wanted:continue
            require(m.isfile() and m.size==lock['files'][name]['bytes'],'INSTALL_INVALID','archive type/size differs')
            put(name,archive.extractfile(m).read())
    require(wanted<=written.keys(),'INSTALL_INVALID','incomplete scientific release')
    with zipfile.ZipFile(artifacts['apache-log4j-2.26.1-bin.zip']) as archive:
        for part in ('api','core','slf4j2-impl'):
            name=f'log4j-{part}-2.26.1.jar';put('bin/lib/'+name,archive.read(name))
    put('bin/lib/guava-33.7.2-jre.jar',_read(artifacts['guava-33.7.2-jre.jar']))
    with zipfile.ZipFile(target/'bin/lib/biojava-structure-7.2.2-rdk.1.jar') as archive:
        for name in lock['files']:
            if name.startswith('ccd/') and name!='ccd/chemcomp/HTA.cif.gz':put(name,archive.read(name[4:]))
    put('ccd/chemcomp/HTA.cif.gz',gzip.compress(_read(artifacts['HTA.cif']),mtime=0))
    require(written==lock['files'],'INSTALL_INVALID','incomplete PDB-only closure')
    result={'schema':'dreamm-p2rank-runtime/1','version':'2.5.1','source_lock_sha256':a.sha256,
            'source_revision':lock['source_revision'],'classpath_order':lock['classpath_order'],
            'science_profiles':lock['science_profiles'],'files':written}
    (target/'.runtime.pending').write_bytes(canonical(result));os.rename(target/'.runtime.pending',target/'runtime.json')
    print(canonical({'output':str(target),'sha256':digest(canonical(result)),'files':len(written)}).decode())


if __name__=='__main__':main()
