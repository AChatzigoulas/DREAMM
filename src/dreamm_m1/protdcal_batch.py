"""Stage independent complete structures for one attempt-scoped ProtDCal JVM.

No executor, scheduler, shared daemon, scientific calculation or output cache.
The existing authoritative worker must admit, launch and reap the whole JVM.
"""
from pathlib import PurePosixPath
import re
from .identity import Adapter, canonical, digest, require
from .pdb_evidence import parse_descriptor_aa

PROJECT_SHA256 = '96add8a2d38888e041fada85dccace524340509646515a062617e265da6ba410'
JAR_SHA256 = 'ac3bb20911deeb4cce6b57026300e516cd4bed1e7526ed831a568c4e188e6a3c'
DESCRIPTOR_SCHEMA_SHA256 = '1746c8bf35581e74e2ee7c179cf62bd189253f7430e90e0f84e5bd124baa6a3c'
MAX_STRUCTURES = 8  # bounded staging, not a claim of scientific memory admission
MAX_INPUT_BYTES = 16 * 1024**2


def stage_batch(adapters, project_template, *, workspace):
    """Return inert payloads/receipt; change only each project's directory line.

    Adapters retain their own complete structure, filename, inverse residue map
    and atom order. Identical bytes in distinct frames are deliberately not
    deduplicated: each project executes independently and has its own output.
    """
    require(isinstance(adapters, (list, tuple)) and 0 < len(adapters) <= MAX_STRUCTURES
            and all(isinstance(a, Adapter) for a in adapters),
            'BENCHMARK_INPUT_INVALID', 'bounded typed complete-structure adapters required')
    require(isinstance(project_template, bytes) and digest(project_template) == PROJECT_SHA256,
            'EXECUTION_PROFILE_INVALID', 'exact reduced configuration required')
    require(isinstance(workspace, str) and re.fullmatch(r'/[A-Za-z0-9_/-]{1,190}', workspace)
            and not workspace.endswith('/') and '//' not in workspace
            and all(x not in ('.', '..') for x in PurePosixPath(workspace).parts),
            'EXECUTION_PROFILE_INVALID', 'private absolute POSIX workspace required')
    require(sum(len(a.data) for a in adapters) <= MAX_INPUT_BYTES,
            'RESOURCE_LIMIT', 'batch input byte budget')
    lines=project_template.splitlines(keepends=True)
    require(lines[0].rstrip(b'\r\n') == b'directory:' and lines[2].rstrip(b'\r\n') == b'indices:',
            'EXECUTION_PROFILE_INVALID', 'reduced project directory slot')
    payloads={}; projects=[]
    for index, adapter in enumerate(adapters):
        project=f'p{index:04d}'
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.pdb',adapter.stem)
                and adapter.stem not in ('.','..') and adapter.links,
                'DESCRIPTOR_MAPPING_UNPROVEN', 'adapter filename/mapping')
        aa=[x.aa_id for x in adapter.links]
        require(len(aa)==len(set(aa)), 'IDENTITY_AMBIGUOUS', 'project AA identities not unique')
        data_path=f'Datasets/{project}/{adapter.stem}'
        config_path=f'Projects/{project}.proj'
        config=list(lines)
        ending=b'\r\n' if lines[1].endswith(b'\r\n') else b'\n'
        config[1]=(workspace+f'/Datasets/{project}/').encode('ascii')+ending
        payloads[data_path]=adapter.data
        payloads[config_path]=b''.join(config)
        payloads[f'Identity/{project}.json']=canonical(adapter.receipt)
        projects.append({'project':project,'adapter_sha256':digest(adapter.data),
                         'config_sha256':digest(payloads[config_path]),'data_path':data_path,
                         'raw_AA_path':f'Outputs/{project}/{project}_AA.txt',
                         'expected_AA':aa,'adapter_receipt':adapter.receipt})
    receipt={'schema':'dreamm-protdcal-batch/1','jar_sha256':JAR_SHA256,
             'template_sha256':PROJECT_SHA256,'workspace':workspace,'projects':projects,
             'structure_count':len(projects),'maximum_complete_jobs':2,
             'scope':'one_authoritative_job_attempt_only',
             'execution_authorized':False,'scientific_parity_approved':False,
             'required_external_controls':'existing_admitted_profile_whole_descendant_cleanup',
             'payloads':{p:{'sha256':digest(raw),'bytes':len(raw)} for p,raw in payloads.items()}}
    return payloads,receipt


def verify_batch_outputs(receipt, outputs, *, descriptor_names):
    """Verify exact project/AA/schema sets; no row-count or coordinate joins.

    This does not attest execution provenance, successful cleanup, complete
    atom preparation, chemical equivalence, prediction parity or publication.
    """
    require(receipt.get('schema')=='dreamm-protdcal-batch/1'
            and receipt.get('jar_sha256')==JAR_SHA256
            and receipt.get('template_sha256')==PROJECT_SHA256,
            'NATIVE_RESULT_INVALID','foreign batch receipt')
    projects=receipt['projects']
    require(isinstance(projects,list) and 0 < len(projects) <= MAX_STRUCTURES
            and len({p['raw_AA_path'] for p in projects})==len(projects)
            and set(outputs)=={p['raw_AA_path'] for p in projects},
            'NATIVE_RESULT_INVALID','missing, duplicate or extra project outputs')
    require(len(descriptor_names)==499 and len(set(descriptor_names))==499
            and digest(canonical(list(descriptor_names)))==DESCRIPTOR_SCHEMA_SHA256,
            'FEATURE_SCHEMA_MISMATCH','499 exact classifier-required ProtDCal columns')
    results=[]
    for project in projects:
        raw=outputs[project['raw_AA_path']]
        converted,(names,rows)=parse_descriptor_aa(raw)
        require(tuple(names)==tuple(descriptor_names),'FEATURE_SCHEMA_MISMATCH','descriptor order changed')
        ids=[r.aa_id for r in rows]; expected=project['expected_AA']
        require(len(ids)==len(set(ids)) and set(ids)==set(expected),
                'DESCRIPTOR_MAPPING_UNPROVEN','missing/extra/duplicate AA identity')
        mapping={r['AA']:r for r in project['adapter_receipt']['residues']}
        require(set(mapping)==set(ids), 'DESCRIPTOR_MAPPING_UNPROVEN','inverse map differs')
        results.append({'project':project['project'],'raw_AA_sha256':digest(raw),
                        'converted_sha256':digest(converted),'rows':len(rows),'columns':len(names),
                        'AA_to_source':[{'AA':r.aa_id,'residue_uid':mapping[r.aa_id]['residue_uid']} for r in rows]})
    return {'schema':'dreamm-protdcal-batch-output/1','projects':results,
            'containment_attested':False,'scientific_parity_approved':False}
