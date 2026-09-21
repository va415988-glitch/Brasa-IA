import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from release_evidence import REQUIRED_SUITES, config_hash, evidence_gates, file_hash
from promote_checkpoint import audit, copy_bundle


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'candidate.safetensors'
        self.source.write_bytes(b'fixture weights')
        self.tokenizer = self.root / 'tokenizer.json'
        self.tokenizer.write_text('{}')
        self.checkpoint = {'config': {'context_length': 8192, 'generation_length': 4096},
                           'tokenizer_sha256': file_hash(self.tokenizer)}
        self.path = Path(str(self.source) + '.evaluation.json')
        self.report = {'schema': 'checkpoint-release-evidence/v1',
                       'checkpoint_sha256': file_hash(self.source),
                       'config_sha256': config_hash(self.checkpoint['config']),
                       'tokenizer_sha256': file_hash(self.tokenizer),
                       'evaluation_provenance': {'used_for_training': False, 'used_for_selection': False,
                                                 'suite_sha256': 'a' * 64},
                       'suites': {name: [{'id': f'fixture-{name}', 'passed': True,
                                         'evidence': {'note': 'unit test fixture, not an evaluation'}}]
                                  for name in REQUIRED_SUITES},
                       'resources': {'p95_seconds': 1.2, 'peak_rss_mb': 300}}
        self.report['suites']['long_generation'][0].update(output_tokens=4096, complete=True, truncated=False)
        self.report['suites']['retention'][0].update(input_tokens=8192)

    def gates(self):
        self.path.write_text(json.dumps(self.report))
        return evidence_gates(self.source, self.checkpoint, self.tokenizer)[0]

    def test_complete_fixture_is_valid_but_absence_is_not(self):
        self.assertFalse(all(evidence_gates(self.source, self.checkpoint, self.tokenizer)[0].values()))
        self.assertTrue(all(self.gates().values()))

    def test_failed_or_missing_suite_cannot_pass(self):
        self.report['suites']['creative'][0]['passed'] = False
        self.report['suites'].pop('tools')
        gates = self.gates()
        self.assertFalse(gates['behavioral_creative'])
        self.assertFalse(gates['behavioral_tools'])

    def test_report_cannot_be_reused_for_other_weights_or_config(self):
        self.source.write_bytes(b'changed weights')
        self.checkpoint['config']['context_length'] = 16384
        gates = self.gates()
        self.assertFalse(gates['evaluated_checkpoint_matches'])
        self.assertFalse(gates['evaluated_config_matches'])

    def test_tokenizer_must_match_checkpoint_and_evaluation(self):
        self.tokenizer.write_text('{"changed": true}')
        self.assertFalse(self.gates()['evaluated_tokenizer_matches'])

    def test_configured_generation_does_not_replace_observation(self):
        self.report['suites']['long_generation'][0]['output_tokens'] = 128
        self.report['suites']['retention'][0]['input_tokens'] = 256
        gates = self.gates()
        self.assertFalse(gates['long_generation_observed'])
        self.assertFalse(gates['retention_observed'])

    def test_changing_context_metadata_does_not_expand_weights(self):
        checkpoint = {'config': {'context_length': 8192, 'generation_length': 4096,
                                 'hidden_size': 8, 'vocab_size': 16},
                      'steps': 100, 'state_dict': {
                          'position_embedding.weight': SimpleNamespace(shape=(256, 8)),
                          'token_embedding.weight': SimpleNamespace(shape=(16, 8))}}
        with patch('promote_checkpoint.load_checkpoint', return_value=checkpoint):
            result = audit(self.source, tokenizer=self.tokenizer)
        self.assertTrue(result['gates']['context_minimum'])
        self.assertFalse(result['gates']['context_weights_match'])
        self.assertFalse(result['promotable'])

    def test_nan_costs_and_training_contamination_are_rejected(self):
        self.report['resources']['p95_seconds'] = float('nan')
        self.report['evaluation_provenance']['used_for_training'] = True
        gates = self.gates()
        self.assertFalse(gates['latency_budget'])
        self.assertFalse(gates['reserved_evaluation'])

    def test_malformed_report_fails_closed(self):
        self.path.write_text('[]')
        self.assertFalse(all(evidence_gates(self.source, self.checkpoint, self.tokenizer)[0].values()))

    def test_safetensors_bundle_preserves_metadata_and_evaluation(self):
        self.gates()
        sidecar = Path(str(self.source) + '.json')
        sidecar.write_text(json.dumps(self.checkpoint))
        target = self.root / 'production.safetensors'
        copy_bundle(self.source, target, self.path)
        self.assertEqual(file_hash(target), file_hash(self.source))
        self.assertEqual(Path(str(target) + '.json').read_bytes(), sidecar.read_bytes())
        self.assertEqual(Path(str(target) + '.evaluation.json').read_bytes(), self.path.read_bytes())
        with self.assertRaises(FileExistsError):
            copy_bundle(self.source, target, self.path)

    def test_wrong_extension_and_incomplete_bundle_cannot_be_published(self):
        self.gates()
        with self.assertRaises(ValueError):
            copy_bundle(self.source, self.root / 'production.pt', self.path)
        target = self.root / 'production.safetensors'
        with self.assertRaises(FileNotFoundError):
            copy_bundle(self.source, target, self.path)
        self.assertFalse(target.exists())
        self.assertFalse(Path(str(target) + '.evaluation.json').exists())


if __name__ == '__main__':
    unittest.main()
