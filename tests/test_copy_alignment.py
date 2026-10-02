"""Check source-position supervision, contiguous copying and causal trimming."""
import copy
from pathlib import Path
import sys
import unittest

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'scripts')]
from cognitive_dialogue import build_frame, cognitive_prompt
from conditioned_context import conditioned_prompt
from copy_supervision import aligned_loss, encode_alignment_rows, token_boundaries, trim_ignored_suffix
from evaluate_cognitive_sft import evidence_oracle, score_decision
from model import build_model
from prepare_cognitive_alignment import annotations, aligned_tokenizer, cases_for, ENTITIES_V4
from prepare_cognitive_copy import ENTITIES_V3
from select_cognitive_sft import validate_splits
from tool_registry import ToolRegistry


class CopyAlignmentTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(2)
        torch.manual_seed(23)
        self.config = {'vocab_size': 40, 'context_length': 48, 'hidden_size': 16,
                       'layers': 2, 'attention_heads': 2, 'feed_forward_multiplier': 2,
                       'initialization': 'scaled-normal-v1', 'copy_attention_size': 8,
                       'copy_boundary_ids': [2, 3], 'copy_transition': True}

    def test_continuation_and_future_boundaries_remain_causal(self):
        model = build_model(self.config).eval()
        tokens = torch.tensor([[5, 7, 2, 3, 9, 10, 2, 3, 12]])
        with torch.inference_mode():
            full = model(tokens)
            for end in [1, 3, 4, 6, 8]:
                torch.testing.assert_close(model(tokens[:, :end]), full[:, :end], atol=1e-5, rtol=1e-5)

    def test_learned_continuation_advances_source_positions_and_recovers_at_end(self):
        model = build_model(self.config).eval()
        with torch.no_grad():
            model.copy_query.weight.zero_()
            model.copy_key.weight.zero_()
            for layer in [model.copy_gate, model.copy_continue]:
                layer.weight.zero_()
                layer.bias.fill_(20)
        with torch.inference_mode():
            logits, state = model(torch.tensor([[5, 6, 7, 2, 3, 9, 10, 11]]), return_copy_state=True)
        self.assertAlmostEqual(float(state['attention'][0, 4, 0]), 1/3, places=5)
        self.assertAlmostEqual(float(state['attention'][0, 5, 1]), .5, places=5)
        self.assertAlmostEqual(float(state['attention'][0, 6, 2]), 1., places=5)
        self.assertAlmostEqual(float(state['attention'][0, 7, 0]), 1/3, places=5)
        self.assertTrue(torch.isfinite(logits).all())

    def test_removing_ignored_tail_preserves_supervised_loss_and_gradients(self):
        model = build_model(self.config)
        x = torch.tensor([[5, 7, 2, 3, 5, 7, 0, 0, 0], [7, 5, 2, 3, 7, 5, 0, 0, 0]])
        y = torch.tensor([[-100, -100, -100, 5, 7, 2, -100, -100, -100],
                          [-100, -100, -100, 7, 5, 2, -100, -100, -100]])
        pointers = torch.tensor([[-100, -100, -100, 0, 1, -100, -100, -100, -100]] * 2)
        continuation = torch.tensor([[-100, -100, -100, 0, 1, 0, -100, -100, -100]] * 2)
        logits, state = model(x, return_copy_state=True)
        loss, _ = aligned_loss(logits, state, y, pointers, continuation)
        loss.backward()
        gradients = {n: p.grad.clone() for n, p in model.named_parameters() if p.grad is not None}
        model.zero_grad(set_to_none=True)
        tx, ty, tp, tc = trim_ignored_suffix((x, y, pointers, continuation))
        trimmed_logits, trimmed_state = model(tx, return_copy_state=True)
        trimmed_loss, _ = aligned_loss(trimmed_logits, trimmed_state, ty, tp, tc)
        torch.testing.assert_close(loss, trimmed_loss, atol=1e-5, rtol=1e-5)
        trimmed_loss.backward()
        for name, parameter in model.named_parameters():
            if name in gradients:
                self.assertTrue(torch.isfinite(parameter.grad).all())
                torch.testing.assert_close(parameter.grad, gradients[name], atol=1e-5, rtol=1e-5)

    def test_repeated_identical_digits_have_distinct_ordered_position_targets(self):
        tokenizer = aligned_tokenizer(['pedido 10001 resposta 10001'], [])
        row = {'messages': [{'role': 'user', 'content': 'pedido 10001'},
                            {'role': 'assistant', 'content': 'resposta 10001'}]}
        prompt = conditioned_prompt(row['messages'][:-1])
        value = '10001'
        row['copy_spans'] = [{'source_bytes': [prompt.index(value), prompt.index(value) + len(value)],
                             'answer_bytes': [9, 14]}]
        _, _, pointers, continuation = encode_alignment_rows([row], tokenizer, 128)
        positions = pointers[pointers >= 0].tolist()
        self.assertEqual(positions, list(range(positions[0], positions[0]+5)))
        self.assertEqual(continuation[continuation == 1].numel(), 4)

    def test_label_corruption_does_not_enter_inference_prompt_or_oracle(self):
        row = cases_for('validation')[0]
        damaged = copy.deepcopy(row)
        damaged['copy_spans'] = [{'source_bytes': [999, 1000], 'answer_bytes': [0, 1]}]
        damaged['reference_decision']['text'] = 'Valor: 999999.'
        damaged['messages'][-1]['content'] = 'An incorrect teacher label.'
        registry = ToolRegistry()
        get_prompt = lambda r: cognitive_prompt(build_frame(r['request_messages'], r['cognition'], registry), 'compact-v1')
        self.assertEqual(get_prompt(row), get_prompt(damaged))
        self.assertEqual(evidence_oracle(row), evidence_oracle(damaged))

    def test_mixed_absence_variants_both_require_missing_field_reason(self):
        rows = [r for r in cases_for('validation') if r['domain'] == 'unrelated']
        self.assertEqual({r['variant'] for r in rows}, {'entity', 'field'})
        for row in rows:
            self.assertEqual(evidence_oracle(row)['cause'], 'missing_field')
            self.assertTrue(all(score_decision(row['reference_decision'], evidence_oracle(row)).values()))

    def test_reserved_entities_are_new_and_splits_disjoint(self):
        self.assertFalse(set(ENTITIES_V4['heldout']) & set(ENTITIES_V3['heldout']))
        validate_splits(cases_for('train'), cases_for('validation'), cases_for('heldout'))

    def test_source_annotations_cannot_point_into_response_role_boundary(self):
        tokenizer = aligned_tokenizer(['pedido 111 resposta 111'], [])
        row = {'messages': [{'role': 'user', 'content': 'pedido 111'}, {'role': 'assistant', 'content': 'resposta 111'}]}
        prompt = conditioned_prompt(row['messages'][:-1])
        at = prompt.index('assistant')
        row['copy_spans'] = [{'source_bytes': [at, at + 9], 'answer_bytes': [0, 7]}]
        with self.assertRaises(ValueError):
            encode_alignment_rows([row], tokenizer, 128)

    def test_no_boundary_still_has_finite_gradients(self):
        model = build_model(self.config)
        logits = model(torch.tensor([[5, 6, 7, 8]]))
        logits.sum().backward()
        for parameter in model.parameters():
            if parameter.grad is not None:
                self.assertTrue(torch.isfinite(parameter.grad).all())

    def test_alignment_distinguishes_source_positions_with_identical_token_value(self):
        logits = torch.zeros(1, 1, 10)
        state = {'attention': torch.tensor([[[.9, .1]]]),
                 'mixture': torch.tensor([[.8]]), 'continuation': torch.tensor([[.1]])}
        labels = torch.tensor([[5]])
        continuation = torch.tensor([[0]])
        right, right_details = aligned_loss(logits, state, labels, torch.tensor([[0]]), continuation)
        wrong, wrong_details = aligned_loss(logits, state, labels, torch.tensor([[1]]), continuation)
        self.assertEqual(float(right_details['generation']), float(wrong_details['generation']))
        self.assertGreater(float(wrong_details['alignment']), float(right_details['alignment']) + 2)
        self.assertGreater(float(wrong), float(right) + 1)

    def test_incremental_copy_cache_matches_full_forward_with_repeated_tokens_and_new_boundaries(self):
        for transition in [False, True]:
            self.config['copy_transition'] = transition
            model = build_model(self.config).eval()
            tokens = torch.tensor([[5, 5, 7, 2, 3, 5, 5, 7, 9, 2, 3, 5, 10],
                                   [8, 8, 6, 2, 3, 8, 8, 6, 9, 2, 3, 8, 11]])
            with torch.inference_mode():
                # Start before the first complete boundary so cache must detect it.
                logits, cache, length = model.prefill_with_cache(tokens[:, :4])
                torch.testing.assert_close(logits, model(tokens[:, :4])[:, -1], atol=1e-5, rtol=1e-5)
                for end in range(5, tokens.shape[1] + 1):
                    logits, cache = model.forward_next_with_cache(tokens[:, end - 1], length, cache, length)
                    length += 1
                    torch.testing.assert_close(logits, model(tokens[:, :end])[:, -1], atol=1e-5, rtol=1e-5)


if __name__ == '__main__':
    unittest.main()
