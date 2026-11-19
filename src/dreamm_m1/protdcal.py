"""M1 single-protein descriptor adapter; no scheduler or lifecycle authority.

The existing admitted worker executes the three fixed argv stages. This module
stages scientific data and verifies captures; it cannot authorize execution,
release a queue slot, attest cleanup, or publish a result.
"""
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from .identity import Adapter, canonical, digest, require
from .pdb_evidence import parse_descriptor_aa
from .wclq import without_fixed_wclq, protein_ca, SCHEMA_SHA256

JAR_SHA256='ac3bb20911deeb4cce6b57026300e516cd4bed1e7526ed831a568c4e188e6a3c'
HELPER_SHA256='983b2a53c1790366de180ee6cac85161586625aadf4e82af4a5fac4cf35ea7de'


@dataclass(frozen=True)
class DescriptorPlan:
    payloads: object
    commands: tuple
    receipt_bytes: bytes

    def __post_init__(self):
        object.__setattr__(self,'payloads',MappingProxyType(dict(self.payloads)))


@dataclass(frozen=True)
class DescriptorExtraction:
    source_pdb_sha256: str
    raw_aa: bytes
    descriptors: bytes
    receipt_bytes: bytes


def _path(path):
    require(isinstance(path,str) and re.fullmatch(r'/[A-Za-z0-9_./-]{1,240}',path)
            and '..' not in PurePosixPath(path).parts and '//' not in path
            and not path.endswith('/'),'EXECUTION_PROFILE_INVALID','fixed absolute internal path')
    return path


def plan_single_descriptors(pdb,stem,template,*,workspace,runtime,optimized=True,adapter_receipt=None):
    """One complete structure, no batching. Runtime is trusted worker metadata.

    Raw public component tests may omit adapter_receipt; M1 uses plan_m1 below.
    No arbitrary shell/executable is accepted from a scientific input field.
    The external admitted runtime must independently verify the actual tools.
    """
    _path(workspace)
    require(type(optimized) is bool,'EXECUTION_PROFILE_INVALID','explicit variant')
    require(set(runtime)=={'java','python','jar','helper','kernel','tables','schema_json','schema_tsv',
                           'jar_sha256','helper_sha256'},'EXECUTION_PROFILE_INVALID','runtime roles')
    require(runtime['jar_sha256']==JAR_SHA256 and runtime['helper_sha256']==HELPER_SHA256,
            'EXECUTION_PROFILE_INVALID','original/compiled tool fingerprints')
    for name,value in runtime.items():
        if not name.endswith('sha256'):_path(value)
    chains=protein_ca(pdb,stem)
    expected=[r['AA'] for chain in chains.values() for r in chain]
    if adapter_receipt is not None:
        require(adapter_receipt['adapter_sha256']==digest(pdb) and adapter_receipt['stem']==stem
                and set(r['AA'] for r in adapter_receipt['residues'])==set(expected),
                'DESCRIPTOR_MAPPING_UNPROVEN','M1 inverse map differs from actual input')
    # Validate exact original template even for the unmodified comparison route.
    reduced=without_fixed_wclq(template)
    lines=(reduced if optimized else template).splitlines(keepends=True)
    lines[1]=(workspace+'/Datasets/\n').encode('ascii')
    input_path=workspace+'/Datasets/'+stem
    raw_path=workspace+'/Outputs/reference/reference_AA.txt'
    complete_path=workspace+'/Outputs/reference/complete_AA.txt' if optimized else raw_path
    payloads={'Datasets/'+stem:pdb,'Projects/reference.proj':b''.join(lines)}
    commands=[(runtime['java'],'-Xms1g','-Xmx4g','-Djava.io.tmpdir='+workspace+'/tmp','-jar',runtime['jar'],
               '-p',workspace+'/Projects','-o',workspace+'/Outputs')]
    if optimized:
        commands.extend([
            (runtime['python'],'-I','-S','-B',runtime['kernel'],input_path,raw_path,runtime['tables'],runtime['schema_json'],
             workspace+'/unrounded.tsv',workspace+'/kernel-receipt.json'),
            (runtime['java'],'-Xms32m','-Xmx256m','-XX:TieredStopAtLevel=1','-Djava.io.tmpdir='+workspace+'/tmp',
             '-cp',runtime['helper']+':'+runtime['jar'],'org.dreamm.m1.WclqFinish',input_path,raw_path,
             workspace+'/unrounded.tsv',runtime['schema_tsv'],complete_path)])
    receipt={'schema':'dreamm-single-descriptor-plan/1','input_sha256':digest(pdb),'stem':stem,'workspace':workspace,
             'optimized':optimized,'expected_AA':expected,'adapter_receipt':adapter_receipt,
             'complete_AA_path':complete_path,'runtime':dict(runtime),'commands':commands,
             'execution_authorized':False,'lifecycle_authority':'existing_external_coordinator_only',
             'payloads':{name:{'bytes':len(raw),'sha256':digest(raw)} for name,raw in payloads.items()}}
    return DescriptorPlan(payloads,tuple(commands),canonical(receipt))


def plan_m1_descriptors(adapter,template,*,workspace,runtime):
    require(isinstance(adapter,Adapter),'DESCRIPTOR_MAPPING_UNPROVEN','typed M1 prepared adapter required')
    return plan_single_descriptors(adapter.data,adapter.stem,template,workspace=workspace,runtime=runtime,
                                   optimized=True,adapter_receipt=adapter.receipt)


def collect_descriptors(plan,raw_aa):
    """Validate scientific data, not a caller's execution/cleanup claim."""
    import json
    require(isinstance(plan,DescriptorPlan),'NATIVE_RESULT_INVALID','descriptor plan required')
    receipt=json.loads(plan.receipt_bytes)
    converted,(columns,rows)=parse_descriptor_aa(raw_aa)
    require(len(columns)==499 and digest(canonical(list(columns)))==SCHEMA_SHA256,
            'FEATURE_SCHEMA_MISMATCH','exact499-column order')
    ids=[r.aa_id for r in rows]
    require(len(ids)==len(set(ids)) and set(ids)==set(receipt['expected_AA']),
            'DESCRIPTOR_MAPPING_UNPROVEN','exact input/AA bijection required')
    result={'schema':'dreamm-m1-descriptor-capture/1','input_sha256':receipt['input_sha256'],
            'plan_sha256':digest(plan.receipt_bytes),'raw_AA_sha256':digest(raw_aa),'descriptor_sha256':digest(converted),
            'rows':len(rows),'columns':499,'adapter_receipt':receipt['adapter_receipt'],
            'backend':'compiled_wclq29_original_Ap' if receipt['optimized'] else 'original',
            'execution_attested':False,'cleanup_attested':False,'classification_performed':False}
    return DescriptorExtraction(receipt['input_sha256'],raw_aa,converted,canonical(result))
