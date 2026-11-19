"""New full-run wiring/negative tests using explicit fake scientific stages.

No historical regression, dependency installation or scientific tool executes.
"""
import argparse
from contextlib import ExitStack, nullcontext
import copy
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dreamm import cli, local, modern_pipeline as pipeline, modern_runtime, runtime
from dreamm.local_entry import LocalProfile, enable_scientific_imports
from dreamm_m1 import execution_profiles as policy
from dreamm_m1.identity import CIF_3RUK_SHA256, Locator, M1Error, canonical, digest
from dreamm_m1.mmcif_input import CIF_1RLW_SHA256
from dreamm_m1.rocky_database import FINGERPRINT, PREFIX
from test_local import profile_data


def modern_manifest():
    roles = {role:'tools/'+role for role in modern_runtime.ModernRuntimeBundle.roles}
    roles.update(modern_runtime.LAYOUT)
    roles.update(protdcal_jar='protdcal/ProtDCal.jar', parameter='protdcal/parameter.cfg',
                 groups='protdcal/groups.gdm', indices='protdcal/PropertyIndices.idx')
    files = {name:{'bytes':1,'sha256':modern_runtime.ModernRuntimeBundle.pinned.get(role,digest(b'synthetic'))}
             for role,name in roles.items()}
    return {'schema':'dreamm-modern-runtime/1','runtime_id':'synthetic-modern-test',
            'versions':dict(modern_runtime.VERSIONS),'roles':roles,'files':files,'links':{},
            'database_fingerprint':FINGERPRINT,'hhblits_threads':15}


class ModernRunBoundaryTests(unittest.TestCase):
    def test_schema_selects_modern_and_never_falls_back_to_reference(self):
        data = modern_manifest(); raw=canonical(data)
        bundle=runtime.runtime_bundle(raw,expected_sha256=digest(raw))
        self.assertIsInstance(bundle,modern_runtime.ModernRuntimeBundle)
        for bad in ({**data,'schema':'unknown-runtime'}, []):
            raw=canonical(bad)
            with self.assertRaises(ValueError):runtime.runtime_bundle(raw,expected_sha256=digest(raw))

    def test_modern_profile_rejects_changed_science_settings_or_interpreter(self):
        for section,key,value in [('versions','Java_major','8'),('versions','pdb2pqr','3.5.2'),
                                  ('roles','charge_python','science/bin/python3.14')]:
            data=copy.deepcopy(modern_manifest());data[section][key]=value;raw=canonical(data)
            with self.subTest(key=key),self.assertRaises(ValueError):
                runtime.runtime_bundle(raw,expected_sha256=digest(raw))
        data=modern_manifest();data['hhblits_threads']=14;raw=canonical(data)
        with self.assertRaises(ValueError):runtime.runtime_bundle(raw,expected_sha256=digest(raw))

    def test_run_defaults_to_modern_without_coordinator(self):
        argv=['run','--mode','M1','--pdb-id','1RLW']
        for name in ('input','sha256','source-packet','runtime-root','runtime-manifest','runtime-sha256',
                     'installation','installation-sha256','database','database-manifest','cgroup-root',
                     'launcher','launcher-sha256','output'):
            argv.extend(['--'+name,'synthetic'])
        args=cli.parser().parse_args(argv)
        self.assertEqual(args.runtime_profile,'modern')
        self.assertEqual(args.action,cli.run_local)
        self.assertEqual(args.execution,'trusted-local')
        self.assertFalse(hasattr(args,'socket'))
        selected=cli.parser().parse_args(['runtime-requirements'])
        self.assertEqual(selected.action(selected)['versions']['CPython'],'3.14.10')
        self.assertEqual(runtime.requirements(argparse.Namespace(runtime_profile='reference'))['versions']['CPython'],'3.7.16')

    def test_built_public_cli_exposes_modern_run_profile(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('modern_test_builder',root/'build.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as temp:
            artifact=Path(temp)/'dreamm.pyz';builder.build(artifact)
            result=subprocess.run([sys.executable,'-I','-S','-B',str(artifact),'run','--help'],
                                  capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn(b'--runtime-profile {modern,reference}',result.stdout)
            self.assertNotIn(b'--socket',result.stdout)
            result=subprocess.run([sys.executable,'-I','-S','-B',str(artifact),'runtime-requirements'],
                                  capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['versions']['Java_major'],'21')

    def test_modern_launcher_retains_all_existing_bounds_and_no_startup_hooks(self):
        args=local.launch_argv(Path('/bwrap'),Path('/runtime-tree'),Path('/inputs'),Path('/app.pyz'),modern=True)
        for flag in ('--unshare-all','--die-with-parent','--clearenv','--size','--cap-drop','--remount-ro','-I','-S'):
            self.assertIn(flag,args)
        self.assertIn('/runtime/science/bin/python3.14',args)
        self.assertNotIn('/runtime/science/bin/python3.7',args)
        self.assertNotIn('OMP_NUM_THREADS',args) # HHblits must retain its own explicit 15-thread policy.

    def test_modern_site_is_explicit_and_pth_not_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);site=root/'science/lib/python3.14/site-packages';site.mkdir(parents=True)
            (site/'never.pth').write_text('raise RuntimeError("do not execute")')
            before=list(sys.path)
            try:
                result=enable_scientific_imports(root,{'science_python':root/'science/bin/python3.14'},modern=True)
                self.assertEqual(result,site)
            finally:sys.path[:]=before

    def test_stage_commands_are_fixed_and_do_not_accept_arbitrary_tools(self):
        for kind in ('preparation','dynamics','inference'):
            argv=pipeline.stage_argv('/runtime/science/bin/python3.14',kind,'/work/request.json','/app/dreamm.pyz')
            self.assertEqual(argv[1:4],('-I','-S','-B'))
            self.assertEqual(argv[-2:],(kind,'/work/request.json'))
            self.assertNotIn('/bin/sh',argv)
        with self.assertRaises(ValueError):pipeline.stage_argv('/python','user-command','/file','/app/dreamm.pyz')

    def test_preparation_cannot_silently_lose_a_protein_residue(self):
        with tempfile.TemporaryDirectory() as temp:
            work=Path(temp)
            for name in ('before.pdb','prepared.pdb'):(work/name).write_bytes(b'synthetic')
            report={'all_pdb_protein_residues_bound':True,'counts':{'coordinate_changes':0,'pdb_only_atoms':0},
                    'source_only_atoms':[{'record':'ATOM'}]}
            bindings={Locator('A',str(i),' '):object() for i in range(126)}
            with patch.object(pipeline,'_pdb'),patch.object(pipeline,'compare_mmcif_to_pdb',return_value=(report,bindings)):
                with self.assertRaisesRegex(M1Error,'filtered source atom'):
                    pipeline.preparation_outputs(object(),work)


class ModernOrchestrationTests(unittest.TestCase):
    def exercise(self, fail_stage=None):
        with tempfile.TemporaryDirectory() as temp,ExitStack() as stack:
            root=Path(temp);request=root/'request';request.mkdir()
            source=SimpleNamespace(original=b'explicit synthetic mmcif',sha256=CIF_1RLW_SHA256)
            packet=root/'packet';packet.mkdir()
            for name in pipeline._HASHES:(packet/name).write_bytes(b'synthetic source receipt')
            (request/'request.json').write_bytes(canonical({'mode':'M1','pdb_id':'1RLW','chain_selection':None,
                'source':{'sha256':CIF_1RLW_SHA256,'path':'source.cif'}}))
            (request/'source.cif').write_bytes(source.original)
            data=modern_manifest()
            class FakeBundle(modern_runtime.ModernRuntimeBundle):
                def __init__(self):self.sha256='1'*64
                @property
                def data(self):return data
                def verify(self,root):return {'synthetic':True}
            bundle=FakeBundle();runtime_root=root/'runtime';runtime_root.mkdir()
            for role in ('model','reference','zeroed','project'):
                path=runtime_root/data['roles'][role];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'synthetic')
            profiles={}
            rolemap={'preparation_charge':'charge_python','preparation_structure':'science_python',
                     'features':'hhblits','descriptors':'protdcal_jar','classifier_compatibility':'science_python'}
            for scope,role in rolemap.items():
                p=profile_data('local_cli_supervisor');p['scope']=scope
                p['inputs']['fixture']={'schema':'synthetic','sha256':CIF_1RLW_SHA256,'max_bytes':4096}
                p['parameters']={'recipe_sha256':pipeline.recipe_sha256(),'model_deserialization_authorized':scope=='classifier_compatibility'}
                p['tool']['executable_sha256']=data['files'][data['roles'][role]]['sha256']
                p['python']['interpreter_sha256']=data['files'][data['roles']['science_python']]['sha256']
                p['dependencies']['lock_sha256']=data['files'][data['roles']['dependency_lock']]['sha256']
                profiles[scope]=LocalProfile(canonical(p))
            stack.enter_context(patch.object(policy,'APPROVED_SCIENTIFIC_PROFILES',frozenset(p.sha256 for p in profiles.values())))
            for module,name,value in ((pipeline.sys,'version_info',(3,14,10)),(pipeline.os,'geteuid',lambda:1000),
                (pipeline.os,'cpu_count',lambda:16),(pipeline.os,'ST_RDONLY',1),
                (pipeline.os,'statvfs',lambda path:SimpleNamespace(f_flag=1))):
                stack.enter_context(patch.object(module,name,value,create=True))
            stack.enter_context(patch('dreamm.cli.verify_request',return_value={}))
            stack.enter_context(patch.object(pipeline,'verify_python_versions'))
            stack.enter_context(patch.object(pipeline,'verify_reference_assets'))
            stack.enter_context(patch.object(pipeline,'load_1rlw_source_packet',return_value=source))
            stack.enter_context(patch.object(pipeline,'reference_bridge',return_value=(b'synthetic bridge',{})))
            stack.enter_context(patch.object(pipeline,'bind_tools',return_value=nullcontext()))
            extraction=root/'scientific/extraction';events=[];loc=Locator('A','1',' ')
            bindings={loc:SimpleNamespace(source_sha256=CIF_1RLW_SHA256)}
            prepared=b'synthetic prepared structure'
            def tool(argv,work,name,seconds):
                kind=argv[-2];events.append(kind)
                if fail_stage==kind:raise M1Error('TOOL_EXIT','synthetic stage failure')
                if kind=='preparation':
                    extraction.mkdir();(extraction/'prepared.pdb').write_bytes(prepared)
                elif kind=='dynamics':
                    (extraction/'dynamics.json').write_bytes(canonical([{'locator':vars(loc),'values':{'GNM':0.1,'ANM':0.2}}]))
                else:
                    output=root/'scientific/prediction';output.mkdir()
                    for name,raw in {'complete.json':b'{}','scaling.json':b'{}','labels.json':b'{}','membrane.csv':b'synthetic\n'}.items():
                        (output/name).write_bytes(raw)
            stack.enter_context(patch.object(pipeline.native,'_tool',side_effect=tool))
            charge_rows=[{'explicit':'synthetic atom receipt'}]
            stack.enter_context(patch.object(pipeline,'preparation_outputs',return_value=(prepared,bindings,{loc:[0,0,0]},{'rows':charge_rows})))
            def stage(name,result):
                def call(*args,**kwargs):
                    if name == 'charges':self.assertIs(args[2],charge_rows)
                    events.append(name)
                    if fail_stage==name:raise M1Error('TOOL_EXIT','synthetic stage failure')
                    if name=='msms':
                        (extraction/'msms-surface.face').write_bytes(b'synthetic')
                        (extraction/'msms-surface.vert').write_bytes(b'synthetic')
                    return result
                return call
            for name,method,result in [('dssp','_dssp',(None,{loc:{}})),('sasa','_sasa',{}),('msms','_depth',{}),
                ('hhblits','_conservation',{loc:0.4}),('charges','_charge_features',{}),
                ('assemble','_assemble',b'synthetic features'),
                ('descriptors','_descriptors',SimpleNamespace(receipt_bytes=b'synthetic descriptor capture'))]:
                stack.enter_context(patch.object(pipeline.native,method,side_effect=stage(name,result)))
            stack.enter_context(patch.object(pipeline,'_pdb',return_value=SimpleNamespace(atoms=[object()])))
            stack.enter_context(patch.object(pipeline,'validate_mesh',return_value=(1,1)))
            stack.enter_context(patch.object(pipeline,'descriptor_adapter',return_value=SimpleNamespace(stem='1rlw_Prot.pdb')))
            stack.enter_context(patch.object(pipeline,'read_python_features',return_value=[]))
            artifacts={'prepared_before':prepared,'join':b'{}','unscaled':b'{}','adapter_map':b'{}'}
            stack.enter_context(patch.object(pipeline,'prepare_m1',return_value=SimpleNamespace(artifacts=artifacts)))
            kwargs=dict(bundle=bundle,runtime_root=runtime_root,profiles=profiles,database_prefix=Path('/database')/PREFIX,
                database_verification={'manifest_sha256':FINGERPRINT,'mount':'/database'},work=root/'scientific',
                profile_bytes=b'synthetic',source_packet=root/'packet')
            if fail_stage:
                with self.assertRaisesRegex(M1Error,'synthetic stage failure'):
                    pipeline.run_reference(request,**kwargs)
                self.assertNotIn('inference',events)
                self.assertFalse((root/'scientific/prediction').exists())
            else:
                _,prediction=pipeline.run_reference(request,**kwargs)
                self.assertEqual(set(prediction),{'scaling.json','labels.json','membrane.csv'})
                self.assertEqual(events,['preparation','dssp','sasa','msms','hhblits','dynamics','charges','assemble','descriptors','inference'])
                self.assertTrue((extraction/'checkpoint.json').is_file())
                self.assertTrue((extraction/'timings.json').is_file())

    def test_complete_stage_order_and_actual_inference_entry_are_connected(self):
        self.exercise()

    def test_failure_cannot_fall_back_to_archived_features_or_labels(self):
        for name in ('preparation','hhblits','descriptors'):
            with self.subTest(stage=name):self.exercise(fail_stage=name)
