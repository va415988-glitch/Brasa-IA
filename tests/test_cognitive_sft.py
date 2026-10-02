"""Behavioral checks for evidence dependence, evaluation isolation and decoding."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
sys.path.insert(0, str(ROOT / 'scripts'))
from cognitive_dialogue import build_frame, cognitive_prompt, validate_decision
from evaluate_cognitive_sft import DOMAINS, assess_proposal, checkpoint_identity, evidence_oracle, score_decision, qualification
from prepare_cognitive_sft import cases_for, parameter_tokenizer
from select_cognitive_sft import validate_splits
from model_server import ModelService
from tokenizer import ByteBPETokenizer
from tool_registry import ToolRegistry


class CognitiveEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = cases_for('validation')

    def case(self, domain):
        return copy.deepcopy(next(row for row in self.rows if row['domain'] == domain))

    def test_oracle_uses_observation_even_if_reference_answer_is_corrupted(self):
        case = self.case('observed')
        expected = evidence_oracle(case)
        case['reference_decision']['text'] = 'Valor: 999.'
        case['messages'][-1]['content'] = 'resposta inventada'
        self.assertEqual(evidence_oracle(case), expected)
        row = json.loads(case['request_messages'][-1]['content'])
        row['data']['content'] = row['data']['content'].replace(str(expected['value']), '999')
        case['request_messages'][-1]['content'] = json.dumps(row)
        self.assertEqual(evidence_oracle(case)['value'], 999)

    def test_same_goal_different_evidence_requires_revision_or_abstention(self):
        domains = ['observed', 'failed', 'empty', 'unrelated', 'conflict', 'revision', 'injection']
        cases = [self.case(domain) for domain in domains]
        self.assertEqual(len({row['request_messages'][0]['content'] for row in cases}), 1)
        expected = {domain: evidence_oracle(case) for domain, case in zip(domains, cases)}
        for domain in ['failed', 'empty', 'unrelated', 'conflict']:
            self.assertEqual(expected[domain]['decision'], 'blocked')
        self.assertEqual(expected['observed']['value'], expected['injection']['value'])
        self.assertNotEqual(expected['observed']['value'], expected['revision']['value'])
        self.assertEqual(expected['revision']['evidence_ids'], ['obs-2'])

    def test_every_authored_target_passes_independent_oracle_and_contract(self):
        registry = ToolRegistry()
        for case in self.rows:
            with self.subTest(case=case['id']):
                frame = build_frame(case['request_messages'], case['cognition'], registry)
                decision = validate_decision(case['messages'][-1]['content'], frame, registry)
                self.assertTrue(all(score_decision(decision, evidence_oracle(case)).values()))

    def test_wrong_value_wrong_evidence_and_false_confirmation_fail(self):
        case = self.case('revision')
        expected = evidence_oracle(case)
        proposal = copy.deepcopy(case['reference_decision'])
        proposal['text'] = f"Valor: {expected['value'] - 1}."
        self.assertFalse(all(score_decision(proposal, expected).values()))
        proposal = copy.deepcopy(case['reference_decision'])
        proposal['evidence_ids'] = ['obs-1']
        self.assertFalse(all(score_decision(proposal, expected).values()))
        case = self.case('rejects')
        proposal = copy.deepcopy(case['reference_decision'])
        proposal['text'] = 'Sim.'
        self.assertFalse(all(score_decision(proposal, evidence_oracle(case)).values()))

    def test_compact_prompt_preserves_data_and_restricted_catalog(self):
        case = self.case('injection')
        registry = ToolRegistry()
        frame = build_frame(case['request_messages'], case['cognition'], registry)
        frame['constraints'] = ['Sem escrita']
        compact = cognitive_prompt(frame, 'compact-v1')
        packet = json.loads(compact.split('\nDados:\n', 1)[1])
        self.assertEqual(packet['observations'], frame['observations'])
        self.assertEqual(packet['constraints'], ['Sem escrita'])
        self.assertEqual(packet['tools'], {})
        self.assertIn('Ignore o pedido', compact)
        with self.assertRaises(ValueError):
            cognitive_prompt(frame, 'unknown')

    def test_split_rejects_entity_leak_even_with_different_prompt(self):
        train, val, heldout = [cases_for(split) for split in ['train', 'validation', 'heldout']]
        validate_splits(train, val, heldout)
        val[0]['entity'] = train[0]['entity']
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            validate_splits(train, val, heldout)

    def test_high_aggregate_score_cannot_hide_unsafe_answer_or_missing_domains(self):
        per_domain = {name: {'passed': 10, 'total': 10} for name in DOMAINS}
        summary = {'passed': 110, 'total': 110, 'unsafe_answers': 0, 'per_domain': per_domain,
                   'paired_groups_passed': 10, 'paired_groups_total': 10}
        self.assertTrue(qualification(summary))
        summary['unsafe_answers'] = 1
        self.assertFalse(qualification(summary))
        summary['unsafe_answers'] = 0
        summary['paired_groups_passed'] = 0
        self.assertFalse(qualification(summary))

    def test_rejected_evidence_still_counts_as_unsafe_neural_answer(self):
        case = self.case('failed')
        registry = ToolRegistry()
        frame = build_frame(case['request_messages'], case['cognition'], registry)
        answer = json.dumps({'decision': 'answer', 'text': 'Valor: 37.', 'gap': '',
                             'evidence_ids': ['obs-9'], 'tool_call': None})
        score = assess_proposal(answer, frame, registry, evidence_oracle(case))
        self.assertFalse(score['contract_valid'])
        self.assertTrue(score['unsafe_answer'])

    def test_abstention_with_invented_failure_does_not_pass(self):
        case = self.case('empty')
        proposal = copy.deepcopy(case['reference_decision'])
        proposal['text'] = 'A fonte falhou.'
        self.assertFalse(all(score_decision(proposal, evidence_oracle(case)).values()))

    def test_parameter_tokenizer_keeps_unseen_numbers_composable(self):
        tokenizer = parameter_tokenizer(['Vale: prazo = 12. Valor: 12. ' * 30], ['Vale'], vocab_size=300)
        self.assertEqual(tokenizer.encode_fast('12'), tokenizer.encode_fast('1') + tokenizer.encode_fast('2'))
        self.assertEqual(tokenizer.encode_fast('57'), tokenizer.encode_fast('5') + tokenizer.encode_fast('7'))
        self.assertEqual(tokenizer.decode(tokenizer.encode_fast('Horizonte: prazo = 57.')), 'Horizonte: prazo = 57.')

    def test_evaluation_rejects_tokenizer_changed_after_training(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            checkpoint = directory / 'candidate.safetensors'
            checkpoint.write_bytes(b'weight identity fixture')
            tokenizer = directory / 'tokenizer.json'
            tokenizer.write_text('{}')
            Path(str(checkpoint) + '.json').write_text(json.dumps({
                'config': {'tokenizer_path': str(tokenizer)},
                'tokenizer_sha256': hashlib.sha256(tokenizer.read_bytes()).hexdigest()}))
            self.assertIn('tokenizer_sha256', checkpoint_identity(checkpoint))
            tokenizer.write_text('{"changed": true}')
            with self.assertRaisesRegex(ValueError, 'Tokenizer differs'):
                checkpoint_identity(checkpoint)

    def test_runtime_and_dataset_share_compact_prompt(self):
        case = self.case('observed')
        service = ModelService('benchmark-only', trace_path=None, cognitive_router_manifest=None)
        service.local_config = {'cognitive_prompt_style': 'compact-v1'}
        with patch.object(service, 'local_reply', return_value=case['messages'][-1]['content']) as generate:
            result = service.cognitive_conversation(case['request_messages'], case['cognition'])
        self.assertEqual(generate.call_args.args[0], case['messages'][:-1])
        self.assertEqual(result['cognition']['decision'], 'answer')

    def test_unknown_prompt_style_stops_before_generation(self):
        service = ModelService('benchmark-only', trace_path=None, cognitive_router_manifest=None)
        service.local_config = {'cognitive_prompt_style': 'unknown'}
        case = self.case('observed')
        with patch.object(service, 'local_reply') as generate:
            result = service.cognitive_conversation(case['request_messages'], case['cognition'])
        generate.assert_not_called()
        self.assertEqual(result['backend'], 'quality-gate')
        self.assertEqual(result['agent']['status'], 'blocked')

    def test_structured_decoder_keeps_repeated_words_and_uses_unpenalized_logits(self):
        import torch
        output = json.dumps({'decision': 'answer', 'text': 'test test test test',
                             'gap': '', 'evidence_ids': [], 'tool_call': None})
        tokenizer = ByteBPETokenizer.train(['pedido', output], vocab_size=300)
        targets = tokenizer.encode_fast(output, add_eos=True)
        service = ModelService('benchmark-only', trace_path=None, cognitive_router_manifest=None)
        service.local_tokenizer = tokenizer
        service.local_config = {'vocab_size': 300, 'context_length': 512, 'training_context_length': 512,
                                'generation_length': 256, 'hidden_size': 16, 'layers': 1, 'attention_heads': 2}
        class Decoder:
            def __init__(self):
                self.index = 0
            def __call__(self, tokens):
                logits = torch.full((*tokens.shape, 300), -100.0)
                logits[0, -1, targets[self.index]] = 100
                self.index += 1
                return logits
        service.local_model = Decoder()
        with patch.dict('os.environ', {}, clear=True):
            result = service.local_reply([{'role': 'user', 'content': 'pedido'}],
                                         structured_decision=True, max_tokens_limit=256)
        self.assertEqual(json.loads(result), json.loads(output))
        self.assertEqual(service.last_generation['repetition_penalty'], 1.0)


if __name__ == '__main__':
    unittest.main()
