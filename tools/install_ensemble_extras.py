"""Install hash-locked export libraries offline, without changing the M1 runtime."""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dreamm.modern import checked_wheels
from dreamm.installation import install_archive
from dreamm_m1.capture import _read,_json
from dreamm_m1.identity import canonical,digest,require


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('manifest','sha256','cache','output'):p.add_argument('--'+n,required=True)
    a=p.parse_args();require(sys.platform=='linux' and os.geteuid()>0,'INSTALL_UNAVAILABLE','non-root Linux required')
    raw=_read(Path(a.manifest));require(digest(raw)==a.sha256,'INSTALL_INVALID','manifest checksum differs')
    lock=_json(raw);checked_wheels(lock,a.cache)
    target=Path(a.output).absolute();target.mkdir(mode=0o700);files={};links={};origins={}
    for row in lock['wheels']:
        install_archive(Path(a.cache)/row['filename'],target,dict(row,format='wheel',destination='xlsx'),
                        files,links,origins,collect=True)
    require(not links,'INSTALL_INVALID','unexpected export-package aliases')
    manifest={'schema':'dreamm-ensemble-extensions/1','source_lock_sha256':a.sha256,'files':files}
    (target/'.checkpoint.pending').write_bytes(canonical(manifest));os.rename(target/'.checkpoint.pending',target/'checkpoint.json')
    print(canonical({'output':str(target),'sha256':digest(canonical(manifest)),'files':len(files)}).decode())


if __name__=='__main__':main()
