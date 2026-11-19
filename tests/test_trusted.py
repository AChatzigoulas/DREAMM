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

    def test_database_alias_hash_and_unlisted_member_rejection(self):
        from dreamm import database_receipt as trusted
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

    @unittest.skipIf(os.name != 'posix', 'Linux process cleanup')
    def test_deadline_cleans_up_a_child_and_keeps_no_completion(self):
        from dreamm.process import launch
        program = (
            "import subprocess,sys,time; from pathlib import Path; "
            "c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            "Path('child.pid').write_text(str(c.pid)); time.sleep(30)"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work = root/'attempt'
            work.mkdir()
            with self.assertRaisesRegex(ValueError, 'deadline reached'):
                launch(argparse.Namespace(timeout=2), root, work, [sys.executable,'-c',program], {}, threads=1)
            receipt = json.loads((root/'supervision.json').read_text())
            self.assertTrue(receipt['process_group_cleanup_verified'])
            self.assertEqual(receipt['process_tree_remaining'], [])
            self.assertTrue((work/'child.pid').exists())
            self.assertFalse((root/'complete.json').exists())

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

if __name__=='__main__':unittest.main()
