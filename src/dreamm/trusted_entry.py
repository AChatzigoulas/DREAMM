"""Fixed namespace child for the explicitly trusted local CLI, never HTTP input."""
import os
from pathlib import Path
import socket
import sys

from dreamm_m1 import execution_profiles as policy
from dreamm_m1.capture import _json, _read
from dreamm_m1.identity import canonical,digest,require


class TrustedProfile(policy.ExecutionProfile):
    max_role_bytes = 256 * 1024**2

    def readiness(self):
        result=super().readiness();d=self.data
        if (d['containment'].get('authority')=='trusted_local_supervisor'
                and d['containment'].get('trusted_inputs_only') is True
                and d['containment'].get('fallback') is None
                and d['resources'].get('whole_job_memory_enforced') is False
                and d['resources'].get('memory_policy')=='sampled_aggregate_rss_and_host_pressure'):
            result['missing_verification']=[x for x in result['missing_verification'] if x not in
                ('containment.authority_or_fallback','resources.whole_job_memory_enforced')]
            result['runnable']=not result['missing_verification'] and result['scientific_execution_approved']
        return result


def environment(config):
    from .trusted import NAMESPACES,MEMORY
    from dreamm_m1.local_budget import scratch_bytes
    require(sys.platform=='linux' and sys.version_info[:3]==(3,14,10) and os.geteuid()>0
            and sys.flags.isolated and sys.flags.no_site,'LOCAL_ADMISSION_FAILED','qualified non-root Python required')
    require(config['schema']=='dreamm-trusted-admission/1' and config['trusted_input_only'] is True
            and config['memory']==MEMORY and config['scratch']==scratch_bytes(),
            'LOCAL_ADMISSION_FAILED','trusted-local admission differs')
    import resource
    from dreamm_m1.local_budget import artifact_bytes, source_bytes
    require(resource.getrlimit(resource.RLIMIT_FSIZE)==(artifact_bytes(),artifact_bytes())
            and os.environ.get('DREAMM_TRUSTED_BUDGET')==f'{source_bytes()}:{artifact_bytes()}:{scratch_bytes()}',
            'LOCAL_ADMISSION_FAILED','effective trusted-local file limits differ')
    require(set(config['host_namespaces'])==set(NAMESPACES)
            and all(os.readlink('/proc/self/ns/'+n)!=config['host_namespaces'][n] for n in NAMESPACES),
            'LOCAL_ADMISSION_FAILED','required namespace separation missing')
    require(all(os.statvfs(p).f_flag & os.ST_RDONLY for p in ('/','/runtime','/inputs','/database'))
            and not os.statvfs('/work').f_flag & os.ST_RDONLY,
            'LOCAL_ADMISSION_FAILED','immutable runtime/input/database or private scratch missing')
    require({name for _,name in socket.if_nameindex()} <= {'lo'},'LOCAL_ADMISSION_FAILED','network must be disconnected')
    status=dict(x.split(':',1) for x in Path('/proc/self/status').read_text().splitlines() if ':' in x)
    require(status['NoNewPrivs'].strip()=='1' and all(int(status[k],16)==0 for k in ('CapEff','CapPrm','CapAmb')),
            'LOCAL_ADMISSION_FAILED','capabilities/no-new-privileges differ')
    require(digest(_read(Path('/code/dreamm.pyz')))==config['application_sha256'],
            'LOCAL_ADMISSION_FAILED','application changed')


def profiles(config,bundle):
    from .modern_pipeline import recipe_sha256
    root=Path('/runtime');roles=bundle.data['roles'];files=bundle.data['files']
    result={}
    for scope,tool in {'preparation_charge':'charge_python','preparation_structure':'science_python',
            'features':'hhblits','descriptors':'protdcal_jar','classifier_compatibility':'science_python'}.items():
        result[scope]=TrustedProfile(canonical({'schema':'dreamm-execution-profile/1','version':1,
            'profile_id':'trusted-local-reference-'+scope,'scope':scope,'modes':[config.get('mode','M1')],
            'python':{'interpreter_sha256':files[roles['science_python']]['sha256']},
            'tool':{'path':str(root/roles[tool]),'executable_sha256':files[roles[tool]]['sha256'],'version':'pinned modern install'},
            'dependencies':{'lock_sha256':files[roles['dependency_lock']]['sha256']},
            'parameters':{'recipe_sha256':recipe_sha256(),'model_deserialization_authorized':scope=='classifier_compatibility'},
            'inputs':{'fixture':{'schema':'verified-local-reference','sha256':config['source_sha256'],
                                 'max_bytes':config['source_bytes']}},
            'outputs':{'receipt':{'schema':'dreamm-local-scientific-output/1','sha256':None,
                                  'max_bytes':config['artifact_bytes']}},
            'resources':{'whole_job_memory_enforced':False,'memory_policy':'sampled_aggregate_rss_and_host_pressure',
                'whole_job_memory_bytes':config['memory'],'CPU_threads':15,'process_limit':128,
                'wall_seconds':config['timeout'],'output_budget_bytes':config['scratch']},
            'containment':{'verified_profile_sha256':digest(canonical(config)),'complete_tree_exit_required':True,
                'authority':'trusted_local_supervisor','trusted_inputs_only':True,'fallback':None}}))
    policy.APPROVED_SCIENTIFIC_PROFILES=frozenset(p.sha256 for p in result.values())
    return result


def validate_results(work,config):
    from .trusted import scratch_usage
    from dreamm_m1.inference import residue_rows,verify_membrane_reference
    from dreamm_m1.mmcif_input import CIF_1RLW_SHA256
    from dreamm_m1.pdb_input import PDB_1RLW_SHA256
    scratch_usage(work)
    base=work/'scientific';extraction=base/'extraction';prediction=base/'prediction'
    for name in ('adapter_map.json','unscaled.json','join.json','prepared.pdb','charges.pqr','python-features.csv',
            'output.hhm','query.json','conservation.json','descriptors/Outputs/reference/complete_AA.txt','checkpoint.json'):
        require((extraction/name).is_file(),'LOCAL_OUTPUT_INVALID','missing scientific artifact: '+name)
    for name in ('membrane.csv','scaling.json','labels.json','bridge.json','complete.json'):
        require((prediction/name).is_file(),'LOCAL_OUTPUT_INVALID','missing inference artifact: '+name)
    residues=residue_rows({'adapter_map':_read(extraction/'adapter_map.json')})
    membrane=_read(prediction/'membrane.csv')
    calls,canonical_calls=verify_membrane_reference(membrane,residues)
    labels=_json(_read(prediction/'labels.json'))
    eligible=[r['residue_uid'] for r in _json(_read(extraction/'unscaled.json'))['rows']]
    require(0<len(residues)<=9999 and len(eligible)==len(labels) and set(eligible)==set(labels)
            and set(labels)<=residues.keys() and all(type(v) is int and v in (0,1) for v in labels.values())
            and canonical_calls==membrane and all(labels.get(uid)==1 for uid in calls),
            'LOCAL_OUTPUT_INVALID','reference residue/eligible coverage differs')
    if config['source_sha256'] in (CIF_1RLW_SHA256,PDB_1RLW_SHA256):
        require(len(residues)==126 and len(labels)==95,'LOCAL_OUTPUT_INVALID','1RLW control coverage differs')
    summary={'schema':'dreamm-local-prediction/1','native_complete':True,'attempt':config['attempt'],
        'mode':config.get('mode','M1'),
        'source_sha256':config['source_sha256'],'chain_selection':config.get('chains'),
        'runtime_profile':'modern','execution_boundary':'trusted-local',
        'fresh_feature_extraction':True,'residues':len(residues),'eligible_residues':len(labels),
        'membrane_calls':len(calls),'outcome':'complete_positive' if calls else 'complete_scientific_empty'}
    manifest={'schema':'dreamm-trusted-artifacts/1','attempt':config['attempt'],
        'source_sha256':config['source_sha256'],'runtime_sha256':config['runtime_sha256'],
        'application_sha256':config['application_sha256'],'files':{}}
    for path in sorted(work.rglob('*')):
        if path.is_dir():continue
        raw=_read(path);manifest['files'][path.relative_to(work).as_posix()]={'bytes':len(raw),'sha256':digest(raw)}
    return summary,manifest


def main():
    from dreamm_m1.local_budget import local_budget
    config=_json(_read(Path('/inputs/admission.json'),16384))
    with local_budget(config['source_bytes'],config['artifact_bytes'],config['scratch']):
        environment(config)
        run_reference_worker(config)


def run_reference_worker(config):
    from .runtime import runtime_bundle
    from .modern_runtime import enable_imports,verify_python_versions
    from .modern_pipeline import run_reference
    from .trusted import verify_database
    from .cli import compatibility
    from dreamm_m1.rocky_database import PREFIX
    bundle=runtime_bundle(_read(Path('/inputs/runtime.json')),expected_sha256=config['runtime_sha256'])
    bundle.verify('/runtime');enable_imports('/runtime');verify_python_versions('science')
    database=verify_database('/database',_json(_read(Path('/inputs/database.json'))),hash_contents=False)
    require(database['file_identities']==config['database_verification']['file_identities']
            and config['database_verification']['fully_hashed'] is True,'DATABASE_UNVERIFIED','database replaced after hashing')
    Path('/work/tmp').mkdir(mode=0o700)
    run_reference('/inputs/request',bundle=bundle,runtime_root='/runtime',profiles=profiles(config,bundle),
        database_prefix=Path('/database')/PREFIX,database_verification=database,work='/work/scientific',
        profile_bytes=compatibility(),execution='trusted-local',stage_deadlines=config['stage_deadlines'])
    summary,_=validate_results(Path('/work'),config)
    Path('/work/prediction-summary.json').write_bytes(canonical(summary))
