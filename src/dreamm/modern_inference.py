"""Actual original-model inference from a verified deterministic M1 checkpoint.

This is the reusable modern inference stage, not fresh feature extraction or a
general pickle loader. Call inside an isolated worker with a qualified runtime.
"""
import importlib.metadata
import os
from pathlib import Path
import sys

from dreamm_m1.capture import _json, _parents, _read
from dreamm_m1.features import Profile, prepare_features
from dreamm_m1.identity import AA, SourceResidue, Locator, canonical, descriptor_adapter, digest, require, sha
from dreamm_m1.inference import _scale, _postprocess, residue_rows
from dreamm_m1.measurement import StageRecorder
from dreamm_m1.receipt_chunks import read_scientific_receipt, write_scientific_receipt
from .cli import compatibility, sync_directory, write_private
from .model_compat import load_original, predict_original

ROLES = {'join': 'join.json', 'unscaled': 'unscaled.json',
         'adapter_map': 'adapter_map.json', 'prepared_before': 'prepared.pdb'}
VERSIONS = {'numpy': '2.5.5', 'pandas': '3.1.1', 'scipy': '1.18.1', 'scikit-learn': '1.9.1',
            'joblib': '1.6.0', 'mlxtend': '0.25.0', 'lightgbm': '4.7.0', 'MDAnalysis': '2.11.0'}


def validate_join_mapping(joined, adapter):
    expected = {r['residue_uid']: AA[r['processed_resname']] for r in adapter['residues']}
    rows = joined['rows']
    require(len(rows) == len(expected) and {r['residue_uid'] for r in rows} == expected.keys()
            and all(r['aa'] == expected[r['residue_uid']] for r in rows),
            'FEATURE_MAPPING_UNPROVEN', 'complete identity/AA correspondence required before eligibility filtering')


def verify_checkpoint(directory, expected_sha256):
    directory = Path(directory).absolute()
    _parents(directory)
    raw = _read(directory / 'checkpoint.json', 16384)
    require(digest(raw) == sha(expected_sha256), 'CHECKPOINT_INVALID', 'checkpoint manifest changed')
    meta = _json(raw)
    require(set(meta) == {'schema', 'source_sha256', 'files'} and
            meta['schema'] == 'dreamm-modern-feature-checkpoint/1' and set(meta['files']) == set(ROLES),
            'CHECKPOINT_INVALID', 'complete modern feature checkpoint required')
    source_sha256 = sha(meta['source_sha256'])
    artifacts = {}
    for role, filename in ROLES.items():
        row = meta['files'][role]
        require(set(row) == {'name', 'bytes', 'sha256'} and row['name'] == filename,
                'CHECKPOINT_INVALID', 'fixed checkpoint artifact names required')
        data = read_scientific_receipt(directory / filename, fixture_sha256=source_sha256) if role != 'prepared_before' else _read(directory / filename)
        require(type(row['bytes']) is int and len(data) == row['bytes'] and digest(data) == row['sha256'],
                'CHECKPOINT_INVALID', 'checkpoint artifact identity changed')
        artifacts[role] = data
    adapter = _json(artifacts['adapter_map'])
    links = adapter['residues']
    from dreamm_m1.local_budget import trusted_local
    require(0 < len(links) <= (9999 if trusted_local() else 1788)
            and len({r['residue_uid'] for r in links}) == len(links),
            'CHECKPOINT_INVALID', 'bounded unique reference residues required')
    bindings = {Locator(**r['python_locator']): SourceResidue(**r['source']) for r in links}
    require(len(bindings) == len(links) and all(r.source_sha256 == source_sha256 and r.model_index == 0
            and r.uid == link['residue_uid'] for r, link in zip(bindings.values(), links)),
            'CHECKPOINT_INVALID', 'source/reference correspondence changed')
    rebuilt = descriptor_adapter(artifacts['prepared_before'], bindings,
        source_mapping_evidence_ref=adapter['source_mapping_evidence_ref'], stem=adapter['stem'],
        chain_map={Locator(**r['python_locator']): r['tool_chain'] for r in links})
    require(rebuilt.receipt == adapter, 'CHECKPOINT_INVALID', 'prepared atom/residue correspondence changed')
    joined = _json(artifacts['join'])
    require(joined['adapter_receipt'] == adapter, 'CHECKPOINT_INVALID', 'joined features have another adapter')
    validate_join_mapping(joined, adapter)
    profile = Profile.owner_selected(compatibility())
    require(prepare_features(joined, profile) == _json(artifacts['unscaled']),
            'CHECKPOINT_INVALID', 'ordered feature matrix differs from complete joined features')
    return artifacts, source_sha256


def predict_features(artifacts, *, model_bytes, reference, zeroed, work):
    """Keep original scaling and terminal postprocessing; use real inference."""
    model, bridge = load_original(model_bytes)
    profile_bytes = compatibility()
    profile = Profile.owner_selected(profile_bytes)
    uids, matrix, scaling = _scale(artifacts, reference, zeroed, profile_bytes)
    residues = residue_rows(artifacts)
    require(len(set(uids)) == len(uids) and set(uids) <= residues.keys(),
            'FEATURE_MAPPING_UNPROVEN', 'classifier reference identities changed')
    values = predict_original(model, matrix, profile.names)
    labels = {uid: int(value) for uid, value in zip(uids, values)}
    calls = _postprocess(artifacts, residues, labels, Path(work))
    return {'scaling.json': canonical(scaling), 'labels.json': canonical(labels), 'membrane.csv': calls}, bridge


def infer_checkpoint(args):
    require(sys.platform == 'linux' and os.geteuid() != 0 and sys.version_info[:3] == (3, 14, 10),
            'SCIENTIFIC_RUNTIME_MISMATCH', 'non-root Linux CPython 3.14.10 required')
    versions = {name: importlib.metadata.version(name) for name in VERSIONS}
    require(versions == VERSIONS, 'SCIENTIFIC_RUNTIME_MISMATCH', 'qualified modern inference versions required')
    artifacts, source_sha256 = verify_checkpoint(args.checkpoint, args.sha256)
    # Verification and reference/scaler checks precede any model loading.
    from dreamm_m1.reference import verify_reference_assets
    reference, zeroed = _read(Path(args.reference), 16*1024**2), _read(Path(args.zeroed), 32768)
    verify_reference_assets(reference, zeroed, compatibility())
    model_bytes = _read(Path(args.model), 1024**2)
    target = Path(args.output).absolute()
    _parents(target.parent)
    target.mkdir(mode=0o700)
    recorder = StageRecorder(evidence_class='scientific_unvalidated')
    with recorder.activate():
        result, bridge = predict_features(artifacts, model_bytes=model_bytes, reference=reference,
                                         zeroed=zeroed, work=target)
    for name, raw in result.items():
        if name == 'scaling.json':
            write_scientific_receipt(target / name, raw, fixture_sha256=source_sha256)
        else:
            write_private(target / name, raw)
    write_private(target / 'bridge.json', canonical(bridge))
    write_private(target / 'timings.json', canonical(recorder.receipt()))
    # Flush complete chunks and geometry as well as their directory entries
    # before committing the completion manifest. Partial trees remain invalid.
    for path in sorted(target.rglob('*')):
        if path.is_file():
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
    for path in sorted((p for p in target.rglob('*') if p.is_dir()), reverse=True):
        sync_directory(path)
    summary = {'schema': 'dreamm-modern-inference/1', 'source_sha256': source_sha256,
        'checkpoint_sha256': args.sha256, 'model_sha256': digest(model_bytes), 'versions': versions,
        'actual_classifier_inference': True, 'frozen_labels_used_for_inference': False,
        'fresh_feature_extraction': False, 'eligible_residues': len(_json(result['labels.json'])),
        'final_calls': len(result['membrane.csv'].splitlines())-1,
        'files': {p.relative_to(target).as_posix(): {'bytes': p.stat().st_size, 'sha256': digest(_read(p))}
                  for p in sorted(target.rglob('*')) if p.is_file()}}
    write_private(target / '.complete.pending', canonical(summary))
    os.rename(target / '.complete.pending', target / 'complete.json')
    sync_directory(target)
    return summary
