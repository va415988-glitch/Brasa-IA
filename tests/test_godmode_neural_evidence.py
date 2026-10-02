"""God Mode's neural gates must measure generation, never a retrieved answer."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'python')]
import godmode


REAL_GENERATION = {'input_tokens': 48, 'max_tokens': 192, 'generated_tokens': 60,
                   'quality_gate_result': 'accepted', 'decoding': 'greedy'}
ANSWER = 'Em Python, uma variável é um nome associado a um valor. Use nomes claros.'


class GodModeNeuralEvidenceTests(unittest.TestCase):
    def service(self, generation=None, answer=ANSWER):
        service = SimpleNamespace(last_generation=None)
        def generate(messages):
            service.last_generation = copy.deepcopy(generation if generation is not None else REAL_GENERATION)
            return answer
        service.local_reply = Mock(side_effect=generate)
        service.neural_reply = Mock(side_effect=AssertionError('retrieval adapter must never be probed'))
        return service

    def audit(self, service=None):
        audit = godmode.GodModeAudit.__new__(godmode.GodModeAudit)
        audit.checkpoint = Path('fixture.safetensors')
        audit.rows = []
        audit.neural_rows = []
        audit.agent_rows = []
        audit.service = service or self.service()
        audit.import_error = None
        audit.checkpoint_data = {'config': {'context_length': 8192, 'vocab_size': 8192,
                                          'hidden_size': 128, 'generation_length': 4096},
                                 'state_dict': {'position_embedding.weight': SimpleNamespace(shape=(8192, 128))}}
        return audit

    def probe(self, service):
        audit = self.audit(service)
        with patch.object(godmode, 'NEURAL_CASES', [godmode.NEURAL_CASES[0]]), \
                patch.object(godmode, 'assess_generation_quality', return_value=(True, 'accepted')) as quality:
            audit.run_neural_probes()
        return audit, quality

    def test_probe_calls_only_the_neural_generator_and_records_its_provenance(self):
        service = self.service()
        audit, quality = self.probe(service)
        service.local_reply.assert_called_once()
        service.neural_reply.assert_not_called()
        row = audit.neural_rows[0]
        self.assertTrue(row['quality'])
        self.assertTrue(row['relevant'])
        self.assertTrue(row['generation_observed'])
        self.assertEqual(row['evidence_kind'], 'neural-generation')
        self.assertEqual(row['generation_source']['method'], 'local_reply')
        self.assertFalse(row['generation_source']['retrieval_allowed'])
        self.assertTrue(godmode.neural_probe_evidence(row))
        quality.assert_called_once()

    def test_good_retrieved_text_is_not_neural_evidence_even_with_positive_counters(self):
        for counters in [{'input_tokens': 0, 'max_tokens': 0, 'generated_tokens': 51}, REAL_GENERATION]:
            with self.subTest(counters=counters):
                service = self.service({**counters, 'backend': 'local-competence-dataset'})
                audit, quality = self.probe(service)
                row = audit.neural_rows[0]
                self.assertFalse(row['quality'])
                self.assertFalse(row['relevant'])
                self.assertFalse(row['generation_observed'])
                self.assertEqual(row['evidence_kind'], 'retrieved-answer')
                self.assertFalse(godmode.neural_probe_evidence(row))
                quality.assert_not_called()

    def test_missing_rejected_or_non_greedy_generation_cannot_pass(self):
        changes = [{'input_tokens': 0}, {'max_tokens': 0}, {'generated_tokens': 0},
                   {'input_tokens': True}, {'quality_gate_result': 'rejected'},
                   {'decoding': 'nucleus'}, {'backend': 'curated-memory'}]
        for change in changes:
            with self.subTest(change=change):
                audit, quality = self.probe(self.service({**REAL_GENERATION, **change}))
                self.assertFalse(audit.neural_rows[0]['quality'])
                quality.assert_not_called()
        audit, _ = self.probe(self.service({}))
        self.assertFalse(audit.neural_rows[0]['generation_observed'])

    def test_stale_metadata_cannot_be_reused_when_a_provider_does_not_record_generation(self):
        service = self.service()
        service.last_generation = copy.deepcopy(REAL_GENERATION)
        service.local_reply = Mock(return_value=ANSWER)
        audit, quality = self.probe(service)
        self.assertFalse(audit.neural_rows[0]['generation_observed'])
        quality.assert_not_called()

    def test_rejected_neural_text_is_not_qualified_merely_by_token_counters(self):
        audit = self.audit()
        with patch.object(godmode, 'NEURAL_CASES', [godmode.NEURAL_CASES[0]]), \
                patch.object(godmode, 'assess_generation_quality', return_value=(False, 'not-relevant')):
            audit.run_neural_probes()
        self.assertTrue(audit.neural_rows[0]['generation_observed'])
        self.assertFalse(audit.neural_rows[0]['quality'])

    def test_repeat_uses_the_same_neural_path_and_requires_valid_evidence_on_both_calls(self):
        service = self.service()
        audit, _ = self.probe(service)
        with patch.object(godmode, 'assess_generation_quality', return_value=(True, 'accepted')):
            self.assertTrue(audit._repeat_neural_probe())
        self.assertEqual(service.local_reply.call_count, 2)
        service.neural_reply.assert_not_called()
        self.assertEqual(service.local_reply.call_args_list[0], service.local_reply.call_args_list[1])

    def test_equal_retrieved_second_answer_cannot_prove_determinism(self):
        service = self.service()
        audit, _ = self.probe(service)
        # Bind the fake's metadata to the actual audited service.
        def retrieved(messages):
            service.last_generation = {**REAL_GENERATION, 'backend': 'local-competence-dataset'}
            return ANSWER
        service.local_reply = Mock(side_effect=retrieved)
        self.assertFalse(audit._repeat_neural_probe())
        service.neural_reply.assert_not_called()

    def test_repeat_cannot_pass_two_empty_answers_or_an_unproved_first_answer(self):
        for answer in ['', None]:
            with self.subTest(answer=answer):
                service = self.service(answer=answer)
                audit, _ = self.probe(service)
                self.assertFalse(audit._repeat_neural_probe())
        service = self.service({'backend': 'local-competence-dataset'})
        audit, _ = self.probe(service)
        self.assertFalse(audit._repeat_neural_probe())
        self.assertEqual(service.local_reply.call_count, 1)

    def test_frozen_legacy_retrieval_cannot_pass_gm018_019_020_or_generation_metrics(self):
        report = json.loads((ROOT / 'model/godmode/training-neural-v1/verification_100.json').read_text())
        audit = self.audit()
        audit.neural_rows = copy.deepcopy(report['neural_probes'])
        # Even rewriting the claimed provenance cannot turn that metadata into generation.
        for row in audit.neural_rows:
            row.update(evidence_kind='neural-generation', generation_source={'method': 'local_reply'})
        for index in range(10):
            audit.add('health', 'fixture only', True, '')
        audit._neural_checks()
        checks = {row.id: row for row in audit.rows}
        for name in ['GM-018', 'GM-019', 'GM-020']:
            self.assertFalse(checks[name].passed)
        self.assertTrue(all(value == 0.0 for value in audit._neural_quality_values.values()))
        audit._neural_quality_checks()
        self.assertEqual(len(audit.rows), 30)
        # The independent check about agent tool claims is separate from neural evidence.
        generation_checks = [row for row in audit.rows if row.category == 'generation']
        self.assertEqual(sum(row.passed for row in generation_checks), 1)

    def test_release_neural_gate_does_not_accept_counterfeit_category_pass_flags(self):
        audit = self.audit()
        audit.neural_rows = [{'evidence_kind': 'neural-generation', 'quality': True, 'relevant': True,
                              'generation_source': {'method': 'local_reply'},
                              'generation': {**REAL_GENERATION, 'backend': 'local-competence-dataset'}}
                             for _ in godmode.NEURAL_CASES]
        for category in ['neural', 'generation']:
            for _ in range(10):
                audit.add(category, 'fixture forged category pass', True, '')
        audit._release_checks()
        gate = next(row for row in audit.rows if row.title == 'gate neural profissional')
        self.assertFalse(gate.passed)

    def test_malformed_provenance_is_unproved_instead_of_crashing(self):
        for row in [None, {}, {'generation_source': None}, {'generation_source': 'local_reply'}]:
            with self.subTest(row=row):
                self.assertFalse(godmode.neural_probe_evidence(row))

    def test_release_neural_gate_still_requires_relevant_accepted_answers(self):
        audit = self.audit()
        audit.neural_rows = [{'evidence_kind': 'neural-generation', 'quality': False, 'relevant': False,
                              'generation_source': {'method': 'local_reply'},
                              'generation': copy.deepcopy(REAL_GENERATION)} for _ in godmode.NEURAL_CASES]
        for category in ['neural', 'generation']:
            for _ in range(10):
                audit.add(category, 'fixture forged category pass', True, '')
        audit._release_checks()
        gate = next(row for row in audit.rows if row.title == 'gate neural profissional')
        self.assertFalse(gate.passed)


if __name__ == '__main__':
    unittest.main()
