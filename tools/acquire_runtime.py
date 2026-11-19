"""Fetch exact public runtime artifacts; no installation or scientific execution.

MSMS is deliberately excluded: obtain the vendor archive separately under the
applicable licence. The database has its own bounded acquisition command.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def fetch(row,directory):
    name=row['filename'];expected=row['sha256'];url=row['url']
    if Path(name).name!=name or len(expected)!=64:raise ValueError('invalid artifact identity')
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ('github.com','conda.anaconda.org','files.pythonhosted.org',
        'archive.apache.org','repo.maven.apache.org','files.rcsb.org','julialang-s3.julialang.org'):
        raise ValueError('pinned official HTTPS source required')
    path=directory/name
    if path.exists():
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('existing artifact differs: '+name)
        return {'file':name,'state':'verified_existing'}
    pending=directory/(name+'.pending');checksum=hashlib.sha256();count=0
    with urllib.request.urlopen(url,timeout=90) as response,pending.open('xb') as out:
        while block:=response.read(1024**2):
            count+=len(block)
            if count>512*1024**2:raise ValueError('artifact size bound')
            checksum.update(block);out.write(block)
        out.flush();os.fsync(out.fileno())
    if checksum.hexdigest()!=expected:raise ValueError('download fingerprint differs: '+name)
    if 'bytes' in row and count!=row['bytes']:raise ValueError('download size differs: '+name)
    pending.rename(path)
    return {'file':name,'state':'acquired_verified','bytes':count,'sha256':expected}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    parser.add_argument('--ensemble',action='store_true',help='also fetch the pinned P2Rank, XLSX and Julia assets')
    args=parser.parse_args();directory=Path(args.output).absolute();directory.mkdir(mode=0o700,exist_ok=True)
    rows=[json.loads((ROOT/'runtime/modern-interpreters.json').read_bytes())['artifacts'][0]]
    native=json.loads((ROOT/'runtime/trusted-native.json').read_bytes())
    rows.extend(native[key] for key in ('java','hhsuite','dssp','boost','freesasa'))
    rows.append({'filename':'prody-2.6.1.tar.gz','bytes':35099850,
        'sha256':'911a8ca3ea7c5f8cd1a9c632e7d7f6e09ea1937db84d147030d08ff8c44b200b',
        'url':'https://files.pythonhosted.org/packages/09/bd/2fbeee84c1f314d9fc502c4bf434eafc83d375aa53a4a46e9574b41e665b/prody-2.6.1.tar.gz'})
    for name in ('modern-structure-cp314.json','modern-charge-cp314.json','modern-prody-build-cp314.json'):
        rows.extend(json.loads((ROOT/'runtime'/name).read_bytes())['wheels'])
    if args.ensemble:
        rows.append(json.loads((ROOT/'runtime/modern-interpreters.json').read_bytes())['artifacts'][1])
        rows.extend(json.loads((ROOT/'runtime/ensemble-exports-cp314.json').read_bytes())['wheels'])
        p2rank=json.loads((ROOT/'runtime/p2rank251-pdb-only.json').read_bytes())['artifacts']
        rows.extend({'filename':name,**asset} for name,asset in p2rank.items())
    seen={};results=[]
    for row in rows:
        if row['filename'] in seen:
            if seen[row['filename']]!=row['sha256']:raise ValueError('conflicting artifact locks')
            continue
        seen[row['filename']]=row['sha256'];record=fetch(row,directory);results.append(record);print(json.dumps(record),flush=True)
    receipt={'schema':'dreamm-public-acquisition/1','artifacts':results,'scientific_execution':False,
        'external_prerequisites':['MSMS academic/vendor archive','UniClust30 database','source-built ProDy wheel (tools/build_prody.py)']}
    (directory/'acquisition.json').write_text(json.dumps(receipt,indent=2)+'\n')


if __name__=='__main__':main()
