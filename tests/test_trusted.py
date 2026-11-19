"""Trusted-local boundaries and fixed scientific settings; no model/tool run."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from dreamm import cli
from dreamm_m1.identity import canonical,digest
from dreamm_m1 import execution_profiles as policy


@unittest.skipUnless(sys.platform=='linux','Linux trusted-local execution boundary')
class TrustedTests(unittest.TestCase):
    def test_cli_has_no_cgroup_or_service_prerequisite(self):
        argv=['run','--mode','M1','--pdb-id','1RLW']
        for name in ('input','sha256','runtime-root','runtime-manifest','runtime-sha256','database','database-manifest','output'):
            argv+=['--'+name,'placeholder']
        args=cli.parser().parse_args(argv)
        self.assertEqual(args.execution,'trusted-local');self.assertIsNone(args.cgroup_root)
        self.assertIsNone(args.source_packet);self.assertIsNone(args.launcher)
        args.cgroup_root='/sys/fs/cgroup/private'
        with self.assertRaisesRegex(ValueError,'never downgrade isolation'):cli.run_local(args)
        args.execution='bounded-worker'
        with self.assertRaises(ValueError):cli.run_local(args)

    def test_local_policy_does_not_satisfy_public_queue_policy(self):
        from dreamm.trusted_entry import TrustedProfile
        from test_local import profile_data
        data=profile_data('trusted_local_supervisor');data['containment'].update(trusted_inputs_only=True)
        data['resources'].update(whole_job_memory_enforced=False,memory_policy='sampled_aggregate_rss_and_host_pressure')
        raw=canonical(data);trusted=TrustedProfile(raw);server=policy.ExecutionProfile(raw)
        with patch.object(policy,'APPROVED_SCIENTIFIC_PROFILES',frozenset([trusted.sha256])):
            self.assertTrue(trusted.readiness()['runnable'])
            self.assertFalse(server.readiness()['runnable'])
            trusted.require_scientific_admission()
            with self.assertRaises(ValueError):server.require_scientific_admission()
        data['containment']['trusted_inputs_only']=False
        self.assertFalse(TrustedProfile(canonical(data)).readiness()['runnable'])

    def test_database_alias_hash_and_unlisted_member_rejection(self):
        from dreamm import trusted
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);raw=b'pinned database fixture';(root/'x').write_bytes(raw);(root/'y').symlink_to('x')
            rows=[{'name':'x','kind':'regular','bytes':len(raw),'sha256':digest(raw)},
                  {'name':'y','kind':'link','target':'x'}]
            manifest={'fingerprint':digest(canonical(rows)),'release':'2018_08','prefix':trusted.PREFIX,'files':rows}
            with patch.object(trusted,'FINGERPRINT',manifest['fingerprint']):
                self.assertTrue(trusted.verify_database(root,manifest)['fully_hashed'])
                (root/'x').write_bytes(b'wrong bytes same size!!')
                with self.assertRaises(ValueError):trusted.verify_database(root,manifest)
                (root/'x').write_bytes(raw);(root/'extra').write_bytes(b'')
                with self.assertRaises(ValueError):trusted.verify_database(root,manifest)

    def test_descendant_tracking_catches_reparented_known_process_and_pid_reuse(self):
        from dreamm.trusted import descendants
        rows={10:{'ppid':1,'start':4},11:{'ppid':10,'start':5},12:{'ppid':1,'start':6}}
        self.assertEqual(descendants(rows,10,{12:6}),{10,11,12})
        self.assertEqual(descendants(rows,10,{12:7}),{10,11})

    def test_scratch_rejects_links_and_oversize(self):
        from dreamm.trusted import scratch_usage
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'x').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):scratch_usage(root)
            (root/'x').unlink()
            with (root/'x').open('wb') as stream:stream.truncate(8*1024**2+1)
            with self.assertRaises(ValueError):scratch_usage(root)

    def test_fixed_hhblits_threads_do_not_depend_on_visible_cpu_count(self):
        from dreamm import trusted_science
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            work=Path(temp);dssp={'loc':{'Amino acid':'A'}};bindings={'loc':SimpleNamespace(uid='residue')}
            def tool(argv,*args):
                self.assertEqual(argv[argv.index('-cpu')+1],'15')
                self.assertEqual(argv[argv.index('-M'):argv.index('-M')+4],('-M','first','-n','3'))
                (work/'output.hhm').write_bytes(b'verified HHM stand-in')
            with patch.object(os,'cpu_count',return_value=2),patch.object(trusted_science.native,'_tool',side_effect=tool),\
                    patch.object(trusted_science.native,'query_input',return_value=(b'>query\nA\n',{'mapping':'explicit'})),\
                    patch.object(trusted_science.native,'conservation_scores',return_value=[0.25]):
                values=trusted_science.conservation(work,dssp,bindings,10)
            self.assertEqual(values,{'loc':0.25})
            self.assertEqual(json.loads((work/'conservation.json').read_bytes())[0]['source_residue_uid'],'residue')

    def test_local_policy_still_rejects_missing_complete_tree_requirement(self):
        from dreamm.trusted_entry import TrustedProfile
        from test_local import profile_data
        data=profile_data('trusted_local_supervisor');data['containment'].update(
            trusted_inputs_only=True,complete_tree_exit_required=False)
        data['resources'].update(whole_job_memory_enforced=False,memory_policy='sampled_aggregate_rss_and_host_pressure')
        profile=TrustedProfile(canonical(data))
        with patch.object(policy,'APPROVED_SCIENTIFIC_PROFILES',frozenset([profile.sha256])):
            with self.assertRaises(ValueError):profile.require_scientific_admission()


if __name__=='__main__':unittest.main()
