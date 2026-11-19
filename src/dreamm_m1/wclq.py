"""Independent ordered contact-graph wCLQ for the 29 fixed-table weights.

Scientific contract reconstructed from pinned ProtDCal IndexTools.CLQ,
ResidueUtility.checkContact and WeightsFunctions. No original Java source is
copied or executed here. Ap remains in Java because its weight is surface-state
dependent and asymmetric. Other descriptors still use the complete original
protein; this kernel retains the original strand-local CLQ definition.

Stdlib only, also compatible with CPython 3.7 for the admitted temporary worker.
This is not an executor, scheduler, deployment switch or scientific approval.
"""
import csv
import hashlib
import io
import json
import math
import re
import sys
import time
from collections import OrderedDict

TABLE_SHA256='9d0a8dcc3dcb24a15ffec0336554fd2fbfe78f6ec28b0c46de74b48749d98d27'
PROJECT_SHA256='96add8a2d38888e041fada85dccace524340509646515a062617e265da6ba410'
SCHEMA_SHA256='1746c8bf35581e74e2ee7c179cf62bd189253f7430e90e0f84e5bd124baa6a3c'
GROUPS=tuple('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL'.split())
LIMIT=8*1024**2


class WclqError(ValueError):pass


def need(condition,message):
    if not condition:raise WclqError(message)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


def tables(raw):
    need(isinstance(raw,bytes) and len(raw)<128*1024 and sha(raw)==TABLE_SHA256,'fixed-table fingerprint differs')
    data=json.loads(raw.decode('ascii'))
    need(data['schema']=='dreamm-wclq-fixed-tables/1' and data['mode']==3
         and data['s_cont']==8.0 and data['t_cont']==4.0 and data['missing_value']==-9999.0,'unsupported scientific parameters')
    order=data['weight_order'];need(len(order)==len(set(order))==29 and 'Ap' not in order,'fixed weight set')
    for w in order:
        need(set(data['weights'][w])==set(GROUPS),'residue-type coverage')
        need(all(isinstance(v,(int,float)) and math.isfinite(v) for v in data['weights'][w].values()),'nonfinite weights')
    return data


def without_fixed_wclq(project):
    """Keep Ap; remove only the other 29 weights from the exact reduced recipe."""
    need(isinstance(project,bytes) and sha(project)==PROJECT_SHA256,'reduced project fingerprint differs')
    lines=project.splitlines(keepends=True)
    ix=[i for i,line in enumerate(lines) if line.rstrip(b'\r\n')==b'wCLQ:']
    need(len(ix)==1,'wCLQ section')
    i=ix[0]+1;ending=b'\r\n' if lines[i].endswith(b'\r\n') else b'\n'
    need(len(lines[i].rstrip(b'\r\n').split(b','))==30,'30 original wCLQ weights')
    lines[i]=b'Ap'+ending
    return b''.join(lines)


def protein_ca(raw,stem):
    """Explicit PDB identities/order for the supported prepared-input domain.

    Missing/nonclassic/alternate/multiple-model identities fail, rather than
    emulating an undocumented CDK recovery or guessing a correspondence.
    The unchanged full PDB, including all hetero atoms, is still given to Java.
    """
    need(isinstance(raw,bytes) and 0<len(raw)<=LIMIT,'PDB input bound')
    need(isinstance(stem,str) and re.match(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.pdb$',stem),'PDB stem')
    retained_3ruk_altlocs=(stem=='3RUK_20261003033543_chainA_fixed.pdb_Prot.pdb'
                          and sha(raw)=='3c6dc4b8e23ce8bfc055646d8075123e11355b2edd5db3d9d599c299f10a9f65')
    chains=OrderedDict();residues=OrderedDict();models=0;ended=False
    try:lines=raw.decode('ascii').splitlines()
    except UnicodeError:raise WclqError('non-ASCII PDB')
    for line_number,line in enumerate(lines,1):
        if line.startswith('MODEL '):
            models+=1;need(models<=1 and not residues,'multiple/misplaced models not supported')
        if line.startswith('ENDMDL'):ended=True
        if not line.startswith(('ATOM  ','HETATM')):continue
        need(not ended and len(line)>=54,'atom outside reference model or truncated atom')
        if line.startswith('HETATM'):
            need(not (line[17:20] in GROUPS and line[12:16].strip().upper()=='CA'),'hetero protein-Ca mapping unproven')
            continue
        chain,number,insertion,name=line[21:22],line[22:26].strip(),line[26:27],line[17:20]
        need(re.match(r'^[A-Za-z0-9]$',chain) and re.match(r'^-?[0-9]+$',number),'unrepresentable chain/residue')
        need(insertion==' ' and (line[16:17]==' ' or (retained_3ruk_altlocs and line[16:17]=='A')),
             'insertion/alternate location requires vetted adapter')
        need(name in GROUPS,'nonclassic residue requires original CDK fallback')
        key=(chain,int(number));aa=stem+'_'+chain+'_'+name+'_'+str(int(number))
        if key not in residues:
            item={'AA':aa,'chain':chain,'resnum':int(number),'resname':name,'ca':None,'ca_line':None}
            residues[key]=item;chains.setdefault(chain,[]).append(item)
        item=residues[key];need(item['resname']==name,'conflicting residue identity')
        if line[12:16].strip().upper()=='CA':
            need(item['ca'] is None,'ambiguous Ca identity')
            try:point=tuple(float(line[a:b]) for a,b in ((30,38),(38,46),(46,54)))
            except ValueError:raise WclqError('malformed coordinates')
            need(all(math.isfinite(x) for x in point),'nonfinite coordinates')
            item['ca']=point;item['ca_line']=line_number
    need(0<len(residues)<=5000 and all(r['ca'] is not None for r in residues.values()),'missing Ca or residue bound')
    return chains


def graph(items,s_cont=8.0,t_cont=4.0):
    """Exact original sqrt-distance/number cutoff; symmetric predicate only."""
    n=len(items);adj=[set() for _ in items];checks=0
    for i in range(n):
        a=items[i]
        for j in range(i+1,n):
            b=items[j];checks+=1
            if abs(a['resnum']-b['resnum'])<t_cont:continue
            dx=a['ca'][0]-b['ca'][0];dy=a['ca'][1]-b['ca'][1];dz=a['ca'][2]-b['ca'][2]
            distance=math.sqrt((dx*dx+dy*dy)+dz*dz)
            if distance<=s_cont:adj[i].add(j);adj[j].add(i)
    return adj,checks


def calculate(chains,data):
    """Retain i-then-j order, legacy position gap, weights and arithmetic tree."""
    start=time.perf_counter();cpu=time.process_time();values={};checks=0;triangles=0
    order=data['weight_order'];types={g:i for i,g in enumerate(GROUPS)}
    pairs={}
    for w in order:
        v=[data['weights'][w][g] for g in GROUPS]
        pairs[w]=[a*b for a in v for b in v]
    for chain,items in chains.items():
        adj,count=graph(items,data['s_cont'],data['t_cont']);checks+=count
        n=len(items);codes=[types[r['resname']] for r in items]
        for k,item in enumerate(items):
            neighbors=sorted(adj[k]);eligible=[]
            for i in neighbors:
                if i>=n-int(data['t_cont']):continue
                for j in neighbors:
                    if j>=i+int(data['t_cont']) and j in adj[i]:eligible.append((i,j))
            triangles+=len(eligible);need(triangles<=10000000,'triangle resource bound')
            row={};tk=codes[k]*20
            for w in order:
                pair=pairs[w];numerator=0.0;denominator=0.0
                for i,j in eligible:
                    wij=pair[codes[i]*20+codes[j]]
                    if wij==data['missing_value']:continue
                    wki=pair[tk+codes[i]];wkj=pair[tk+codes[j]]
                    if wki==data['missing_value'] or wkj==data['missing_value']:continue
                    # Do not factor, reassociate, vector-reduce or parallel-sum.
                    product=wki*wkj
                    denominator=denominator+product
                    numerator=numerator+(product*wij)
                result=0.0 if denominator==0.0 else numerator/denominator
                need(math.isfinite(result),'nonfinite wCLQ result requires original handling')
                row['wCLQ('+w+')_NO']=result
            values[item['AA']]=row
    return values,{'kernel':'dreamm-wclq-ordered-graph/1','residues':len(values),'chains':list(chains),
        'contact_pair_checks':checks,'eligible_triangles':triangles,
        'wall_seconds':time.perf_counter()-start,'CPU_seconds':time.process_time()-cpu,
        'independent_columns':29,'Ap':'unchanged original Java','reduction_order':'original target/i/j order',
        'new_tolerance':None,'scientific_parity_approved':False}


def prepare_columns(pdb,stem,reduced,table_bytes,schema_bytes):
    """Emit exact double hex, never a Python-rounded classifier table.

    Java NumberFormat and Python formatting disagree at some decimal ties.
    The admitted native adapter must use the original pinned roundNumber
    formatter before accepting a complete499-column output.
    """
    data=tables(table_bytes);schema=json.loads(schema_bytes.decode('ascii'))
    need(len(schema)==len(set(schema))==499 and sha(canonical(schema))==SCHEMA_SHA256,'499-column schema fingerprint')
    offloaded=set('wCLQ('+w+')_NO' for w in data['weight_order'])
    need(offloaded<=set(schema) and 'wCLQ(Ap)_NO' not in offloaded,'offloaded column set')
    chains=protein_ca(pdb,stem);computed,receipt=calculate(chains,data)
    need(isinstance(reduced,bytes) and 0<len(reduced)<=LIMIT,'reduced AA bound')
    lines=reduced.splitlines(keepends=True)
    need(len(lines)>3 and b'PARAMETERS' in lines[0] and b't_cont:' in lines[1],'AA preamble')
    rows=list(csv.reader(io.StringIO(b''.join(lines[2:]).decode('ascii')),delimiter='\t'))
    header=rows[0];expected=['AA']+[x for x in schema if x not in offloaded]
    need(header==expected,'expected exactly470 original columns with Ap retained')
    ids=[r[0] for r in rows[1:]]
    need(len(ids)==len(set(ids)) and set(ids)==set(computed),'AA-to-PDB identity is not bijective')
    for row in rows[1:]:
        need(len(row)==len(header),'reduced AA row width')
    payload={'schema':'dreamm-wclq-unrounded/1','source_sha256':sha(pdb),'reduced_AA_sha256':sha(reduced),
             'full_schema_sha256':sha(canonical(schema)),'columns':[c for c in schema if c in offloaded],
             'values':{aa:{c:v.hex() for c,v in values.items()} for aa,values in computed.items()},
             'serialization':'pending_original_Java_roundNumber_double_3'}
    raw=canonical(payload)+b'\n'
    receipt.update({'source_sha256':sha(pdb),'reduced_AA_sha256':sha(reduced),'unrounded_sha256':sha(raw),
        'fixed_tables_sha256':sha(table_bytes),'columns':499,
        'complete_AA_created':False,'rounding':'pending original Java roundNumber; Python rounding prohibited',
        'atom_identity':[{'AA':r['AA'],'chain':r['chain'],'resnum':r['resnum'],'resname':r['resname'],'input_CA_line':r['ca_line']} for items in chains.values() for r in items]})
    return raw,receipt


def prepare_compiled_columns(pdb,stem,reduced,table_bytes,schema_bytes):
    """Versioned bounded TSV for the compiled bridge; no JSON engine required.

    Reuses the exact tested calculation and validation. Double hex is unchanged;
    conversion adds no new rounding/scaling and preserves explicit AA keys.
    """
    raw,receipt=prepare_columns(pdb,stem,reduced,table_bytes,schema_bytes)
    data=json.loads(raw.decode('ascii'));schema=json.loads(schema_bytes.decode('ascii'))
    schema_tsv=('AA\t'+'\t'.join(schema)+'\n').encode('ascii')
    lines=['#dreamm-wclq-unrounded/2',
           'source_sha256='+sha(pdb)+'\treduced_AA_sha256='+sha(reduced)
           +'\tschema_sha256='+sha(schema_tsv)+'\ttables_sha256='+sha(table_bytes),
           '\t'.join(['AA']+data['columns'])]
    for aa,values in data['values'].items():lines.append('\t'.join([aa]+[values[c] for c in data['columns']]))
    result=('\n'.join(lines)+'\n').encode('ascii')
    need(len(result)<=LIMIT,'unrounded TSV bound')
    receipt.update({'unrounded_schema':'dreamm-wclq-unrounded/2','unrounded_sha256':sha(result),
                    'formatter_backend':'compiled_WclqFinish_original_roundNumber','Nashorn_required':False})
    return result,receipt


def main():
    # Fixed file roles inside a supervisor-owned private attempt; no subprocesses.
    need(len(sys.argv)==7,'usage: wclq.py input.pdb reduced_AA tables schema unrounded.json receipt')
    import os
    def read(path):
        need(os.path.isfile(path) and not os.path.islink(path) and os.stat(path).st_size<=LIMIT,'input file type/bound')
        with open(path,'rb') as f:return f.read(LIMIT+1)
    raw,receipt=prepare_compiled_columns(read(sys.argv[1]),os.path.basename(sys.argv[1]),read(sys.argv[2]),read(sys.argv[3]),read(sys.argv[4]))
    for path,content in ((sys.argv[5],raw),(sys.argv[6],(json.dumps(receipt,sort_keys=True,indent=2)+'\n').encode())):
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)
        with os.fdopen(os.open(path,flags,0o600),'wb') as f:f.write(content)


if __name__=='__main__':main()
