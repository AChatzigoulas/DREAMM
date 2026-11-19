"""Exact fixture-scoped PDB writer metadata projection, never chemistry changes."""
from dreamm_m1.identity import digest,require
from dreamm_m1.pdb_input import PDB_1RLW_SHA256

PINS={
 'before':('13f0072c21a7aac7bab803cb6fb0900cfcd525d8e8bdf00094fe73fb78daeef3',
           'b44719e704cba6f4cb972c820242c42262772bf6cff801ce49c22b04e296e754',1001),
 'prepared':('5c1602a34284cec364ded4df664ef7230385100a1270a93a7edf26981ba8608a',
             '77725c01c422b75e9efa7f932836ae8447166488ec4ea27b92caa886a5383b1f',1989)}


def project(raw,source_sha256,phase):
    if source_sha256!=PDB_1RLW_SHA256:return raw,{'applied':False}
    require(phase in PINS,'PREPARATION_MAPPING_UNPROVEN','unknown writer phase')
    native,expected,count=PINS[phase];original=digest(raw)
    require(original in (native,expected),'PREPARATION_MAPPING_UNPROVEN','unreviewed PDB writer output')
    if original==expected:return raw,{'applied':False,'already_canonical':True,'sha256':expected}
    lines=[];changed=0
    for line in raw.splitlines(keepends=True):
        if line.startswith((b'ATOM  ',b'HETATM')):
            require(line[72:76].strip()==b'0','PREPARATION_MAPPING_UNPROVEN','unexpected segment identity')
            line=line[:72]+b'    '+line[76:];changed+=1
        lines.append(line)
    result=b''.join(lines)
    require(changed==count and digest(result)==expected,'PREPARATION_MAPPING_UNPROVEN','projection changed more than reviewed metadata')
    return result,{'schema':'dreamm-writer-identity/1','applied':True,'source_sha256':source_sha256,
        'phase':phase,'native_sha256':native,'canonical_sha256':expected,'atom_records':changed,
        'operation':'synthetic_uniform_segment_0_to_original_blank_columns_73_76',
        'coordinates_and_all_other_bytes_unchanged':True}


def blank_writer_segment(raw):
    """Project a uniform synthetic segment for an original segment-free PDB.

    The caller checks the actual source layout. The pipeline subsequently checks
    exact atom and coordinate correspondence against the source before any
    features or predictions may be published.
    """
    lines=raw.splitlines(keepends=True)
    atom_lines=[line for line in lines if line.startswith((b'ATOM  ',b'HETATM'))]
    segments={line[72:76].strip() for line in atom_lines}
    require(segments in ({b''},{b'0'}),'PREPARATION_MAPPING_UNPROVEN','unrecognised writer segment layout')
    if segments=={b''}:return raw,{'applied':False}
    result=b''.join(line[:72]+b'    '+line[76:] if line.startswith((b'ATOM  ',b'HETATM')) else line for line in lines)
    return result,{'applied':True,'operation':'uniform_writer_segment_0_to_original_blank',
        'native_sha256':digest(raw),'canonical_sha256':digest(result),'atom_records':len(atom_lines)}


def project_ter_segments(raw):
    """Strip only synthetic TER-derived segment IDs on a segment-free source."""
    import re
    lines=raw.splitlines(keepends=True)
    segments={}
    for line in lines:
        if not line.startswith((b'ATOM  ',b'HETATM')):
            continue
        segment=line[72:76].strip()
        require(segment==b'' or re.fullmatch(rb'[0-9]{1,4}',segment),
                'PREPARATION_MAPPING_UNPROVEN','unexpected nonnumeric synthetic segment')
        chain=line[21:22].decode('ascii')
        segments.setdefault(chain,set()).add(segment.decode('ascii'))
    require(segments,'PREPARATION_MAPPING_UNPROVEN','empty writer projection')
    result=b''.join(line[:72]+b'    '+line[76:] if line.startswith((b'ATOM  ',b'HETATM')) and line[72:76].strip()
                    else line for line in lines)
    return result,{'applied':result!=raw,'operation':'TER-derived writer segments to input blank field',
        'chains_and_writer_segments':{chain:sorted(values) for chain,values in segments.items()},
        'native_sha256':digest(raw),'canonical_sha256':digest(result)}
