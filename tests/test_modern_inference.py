"""Private checkpoint input boundary; no scientific execution or model load."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from dreamm.modern_inference import verify_checkpoint, validate_join_mapping, ROLES


class CheckpointBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def manifest(self, data):
        raw = (json.dumps(data) + '\n').encode()
        (self.root / 'checkpoint.json').write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def test_changed_manifest_rejected_before_artifact_reads(self):
        self.manifest({})
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            verify_checkpoint(self.root, '0' * 64)

    def test_incomplete_stage_and_extra_fields_cannot_be_committed(self):
        for data in ({}, {'schema': 'dreamm-modern-feature-checkpoint/1', 'source_sha256': '0'*64, 'files': {}},
                     {'schema': 'dreamm-modern-feature-checkpoint/1', 'source_sha256': '0'*64,
                      'files': {role:{} for role in ROLES}, 'complete': True}):
            pin = self.manifest(data)
            with self.assertRaisesRegex(ValueError, 'complete modern feature'):
                verify_checkpoint(self.root, pin)

    def test_manifest_cannot_select_paths_or_serialized_prediction_labels(self):
        for name in ('../join.json', '/private/file', 'labels.json'):
            data = {'schema': 'dreamm-modern-feature-checkpoint/1', 'source_sha256': '0'*64,
                    'files': {role: {'name': name, 'bytes': 1, 'sha256': '0'*64} for role in ROLES}}
            pin = self.manifest(data)
            with self.assertRaisesRegex(ValueError, 'fixed checkpoint artifact'):
                verify_checkpoint(self.root, pin)

    def test_modified_artifact_fails_before_model_loading(self):
        (self.root / 'join.json').write_bytes(b'changed')
        data = {'schema': 'dreamm-modern-feature-checkpoint/1', 'source_sha256': '0'*64,
                'files': {role: {'name': name, 'bytes': 1, 'sha256': '0'*64} for role, name in ROLES.items()}}
        pin = self.manifest(data)
        with self.assertRaisesRegex(ValueError, 'artifact identity changed'):
            verify_checkpoint(self.root, pin)

    def test_equal_counts_do_not_prove_residue_mapping_even_for_excluded_rows(self):
        adapter = {'residues': [{'residue_uid': 'a', 'processed_resname': 'ALA'},
                                {'residue_uid': 'b', 'processed_resname': 'GLY'}]}
        valid = {'rows': [{'residue_uid': 'a', 'aa': 'A'}, {'residue_uid': 'b', 'aa': 'G'}]}
        validate_join_mapping(valid, adapter)
        for bad in ([{'residue_uid': 'a', 'aa': 'A'}, {'residue_uid': 'foreign', 'aa': 'G'}],
                    [{'residue_uid': 'a', 'aa': 'A'}, {'residue_uid': 'a', 'aa': 'A'}],
                    [{'residue_uid': 'a', 'aa': 'G'}, {'residue_uid': 'b', 'aa': 'A'}]):
            with self.assertRaisesRegex(ValueError, 'identity/AA correspondence'):
                validate_join_mapping({'rows': bad}, adapter)
