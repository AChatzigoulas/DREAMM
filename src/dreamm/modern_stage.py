"""Fixed modern preparation/ProDy child entries inside the admitted job."""
from pathlib import Path
import sys

from dreamm_m1.capture import _json, _read
from dreamm_m1.identity import Locator, SourceResidue, canonical, require
from dreamm_m1.measurement import StageRecorder
from .modern_runtime import enable_imports, verify_python_versions


def main():
    import os
    from dreamm_m1.local_budget import local_budget
    if os.environ.get('DREAMM_TRUSTED_BUDGET'):
        with local_budget(*map(int,os.environ['DREAMM_TRUSTED_BUDGET'].split(':'))):
            return run_stage()
    return run_stage()


def run_stage():
    require(len(sys.argv) == 3 and sys.argv[1] in ('preparation', 'dynamics', 'inference'),
            'SCIENTIFIC_RUNTIME_MISMATCH', 'fixed modern stage request required')
    kind = sys.argv[1]
    config = _json(_read(Path(sys.argv[2])))
    enable_imports('/runtime', stage='dynamics' if kind == 'dynamics' else 'science')
    verify_python_versions('dynamics' if kind == 'dynamics' else 'science')
    if kind == 'inference':
        import argparse
        from .modern_inference import infer_checkpoint
        infer_checkpoint(argparse.Namespace(**config))
        return
    recorder = StageRecorder(evidence_class='scientific_unvalidated')
    work = Path(config['work'])
    try:
        with recorder.activate():
            if kind == 'preparation':
                from .modern_preparation import prepare_reference
                options={'project_blank_pdb_segment':True} if config.get('project_blank_pdb_segment') else {}
                prepare_reference(config['input'], expected_sha256=config['input_sha256'],
                    charge_python='/runtime/charge/bin/python3.14', work=work, timeout=config.get('charge_timeout',180),
                    chain=config.get('chains'), general=config.get('general',False), **options)
            else:
                import os
                os.environ['OMP_NUM_THREADS'] = '1'
                from dreamm_m1.native_m1 import _dynamics
                rows = config['bindings']
                bindings = {Locator(**r['locator']): SourceResidue(**r['source']) for r in rows}
                require(len(bindings) == len(rows), 'FEATURE_MAPPING_UNPROVEN', 'duplicate dynamics binding')
                result = _dynamics(work, bindings)
                data = [{'locator': vars(loc), 'values': dict(values)} for loc, values in result.items()]
                (work/'dynamics.json').write_bytes(canonical(data))
    finally:
        # On preparation failure the directory may not have been created yet.
        if work.is_dir():
            (work/(kind+'-timings.json')).write_bytes(canonical(recorder.receipt()))


if __name__ == '__main__':
    main()
