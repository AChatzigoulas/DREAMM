"""Lossless local export of an already completed, fenced M1 attempt.

No scientific stage or model is executed. Existing numeric bytes are retained.
"""
import hashlib
import os
from pathlib import Path
import zipfile

from .cli import write_private,sync_directory
from .runtime import relative_path
from dreamm_m1.capture import _read,_json,_parents
from dreamm_m1.identity import canonical,digest,require
from dreamm_m1.inference import residue_rows,verify_membrane_reference
from dreamm_m1.identity import residue_label_parts
from dreamm_m1.local_budget import artifact_count, scratch_bytes, trusted_local


def verified_files(attempt, *, load):
    attempt=Path(attempt).absolute();_parents(attempt)
    complete=_json(_read(attempt/'complete.json'))
    raw=_read(attempt/'artifacts.json');manifest=_json(raw)
    require(complete['artifact_manifest_sha256']==digest(raw)
            and manifest['schema']=='dreamm-trusted-artifacts/1'
            and complete['summary']['native_complete'] is True
            and manifest['attempt']==complete['summary']['attempt'],
            'EXPORT_INVALID','completed scientific attempt and manifest required')
    files={};total=0
    require(0<len(manifest['files'])<=artifact_count() and load<=manifest['files'].keys(),
            'EXPORT_INVALID','artifact inventory or required file coverage differs')
    total_budget = scratch_bytes() if trusted_local() else 128*1024**2
    result_root=attempt/'results'
    for name,row in manifest['files'].items():
        relative_path(name)
        path=result_root/name
        _parents(path.parent)
        content=_read(path);total+=len(content)
        require(total<=total_budget and row=={'bytes':len(content),'sha256':digest(content)},
                'EXPORT_INVALID','artifact size/checksum differs')
        if name in load:files[name]=content
    return manifest,files


def pml_for_calls(adapter,membrane):
    residues=residue_rows({'adapter_map':adapter})
    calls,reconstructed=verify_membrane_reference(membrane,residues)
    require(reconstructed==membrane,'EXPORT_INVALID','membrane identities/terminal flags differ')
    selectors=['(chain '+residues[uid][0]+' and resi '+str(residues[uid][1])+')'
               for uid in sorted(calls,key=lambda uid:(residues[uid][0],*residue_label_parts(residues[uid][1])))]
    return ('# Computational membrane predictions; not experimental binding evidence.\n'
        'load structure.pdb, dreamm_structure\nhide everything\nshow cartoon, dreamm_structure\n'
        'color slate, dreamm_structure\nselect dreamm_membrane, '+(' or '.join(selectors) if selectors else 'none')+
        '\ncolor orange, dreamm_membrane\nshow sticks, dreamm_membrane\nzoom dreamm_structure\n').encode('ascii')


def export(args):
    manifest,files=verified_files(args.attempt,load={'scientific/prediction/membrane.csv',
        'scientific/extraction/prepared.pdb','scientific/extraction/adapter_map.json'})
    membrane=files['scientific/prediction/membrane.csv']
    pdb=files['scientific/extraction/prepared.pdb']
    pml=pml_for_calls(files['scientific/extraction/adapter_map.json'],membrane)
    output=Path(args.output).absolute();_parents(output.parent);output.mkdir(mode=0o700)
    friendly={'membrane.csv':membrane,'structure.pdb':pdb,'view.pml':pml,
        'README.txt':b'Computational DREAMM predictions, not experimental binding validation.\n'
            b'Open view.pml in PyMOL from this directory. Terminal flags remain in membrane.csv.\n'
            b'All native scientific evidence is retained under evidence/.\n'}
    payload={**friendly,'evidence/artifacts.json':canonical(manifest)}
    inventory=manifest['files']
    for name,raw in friendly.items():write_private(output/name,raw)
    archive=output/'DREAMM-results.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as target:
        for name,raw in sorted(payload.items()):
            item=zipfile.ZipInfo(name,(1980,1,1,0,0,0));item.external_attr=0o100644<<16
            target.writestr(item,raw,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
        for name in sorted(inventory):
            raw=_read(Path(args.attempt).absolute()/'results'/name)
            require(inventory[name]=={'sha256':digest(raw),'bytes':len(raw)},
                    'EXPORT_INVALID','artifact changed while building ZIP')
            item=zipfile.ZipInfo('evidence/'+name,(1980,1,1,0,0,0));item.external_attr=0o100644<<16
            target.writestr(item,raw,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    with zipfile.ZipFile(archive) as target:
        require(set(target.namelist())==payload.keys()|{'evidence/'+name for name in inventory}
                and all(target.read(n)==v for n,v in payload.items()),
                'EXPORT_INVALID','ZIP membership/reconstruction differs')
        for name,row in inventory.items():
            stored=target.getinfo('evidence/'+name)
            with target.open(stored) as stream:
                require(stored.file_size==row['bytes'] and hashlib.file_digest(stream,'sha256').hexdigest()==row['sha256'],
                        'EXPORT_INVALID','ZIP artifact checksum differs')
    with archive.open('rb') as stream:
        checksum=hashlib.file_digest(stream,'sha256').hexdigest()
    receipt={'schema':'dreamm-standalone-export/1','attempt':manifest['attempt'],
        'scientific_artifact_manifest_sha256':digest(canonical(manifest)),
        'zip_sha256':checksum,'zip_members':len(payload)+len(inventory),
        'uncompressed_bytes':sum(map(len,payload.values()))+sum(row['bytes'] for row in inventory.values()),
        'scientific_calculation_performed':False}
    write_private(output/'.complete.pending',canonical(receipt));os.rename(output/'.complete.pending',output/'complete.json')
    sync_directory(output);return receipt
