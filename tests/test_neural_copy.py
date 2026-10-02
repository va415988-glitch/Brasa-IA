"""Verify causality, finite training gradients and actual source-token copying."""
import sys
from pathlib import Path
import unittest

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from model import build_model


class NeuralCopyTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(2)
        torch.manual_seed(29)
        self.config = {'vocab_size': 31, 'context_length': 48, 'hidden_size': 16,
                       'layers': 2, 'attention_heads': 2, 'feed_forward_multiplier': 2,
                       'initialization': 'scaled-normal-v1',
                       'copy_attention_size': 8, 'copy_boundary_ids': [2, 3]}
        self.model = build_model(self.config)

    def test_prompt_and_response_logits_do_not_see_future_tokens_or_boundaries(self):
        self.model.eval()
        tokens = torch.tensor([[5, 7, 2, 3, 9, 10, 2, 3, 12]])
        with torch.inference_mode():
            complete = self.model(tokens)
            for end in [1, 3, 4, 6, 8]:
                prefix = self.model(tokens[:, :end])
                torch.testing.assert_close(prefix, complete[:, :end], atol=1e-5, rtol=1e-5)

    def test_copy_distribution_comes_from_prompt_only(self):
        self.model.eval()
        with torch.no_grad():
            self.model.copy_query.weight.zero_()
            self.model.copy_key.weight.zero_()
            self.model.copy_gate.weight.zero_()
            self.model.copy_gate.bias.fill_(20)
        with torch.inference_mode():
            probabilities = self.model(torch.tensor([[5, 5, 7, 2, 3, 9]])).softmax(-1)
        self.assertAlmostEqual(float(probabilities[0, 4, 5]), 2 / 3, places=5)
        self.assertAlmostEqual(float(probabilities[0, 4, 7]), 1 / 3, places=5)
        self.assertLess(float(probabilities[0, 4, 9]), 1e-6)
        self.assertLess(float(probabilities[0, 4, 2]), 1e-6)

    def test_missing_boundary_has_finite_logits_and_gradients(self):
        tokens = torch.tensor([[5, 7, 9, 11, 13], [6, 8, 2, 3, 14]])
        logits = self.model(tokens)
        self.assertTrue(torch.isfinite(logits).all())
        loss = torch.nn.functional.cross_entropy(logits.reshape(-1, 31), tokens.reshape(-1))
        loss.backward()
        for parameter in self.model.parameters():
            if parameter.grad is not None:
                self.assertTrue(torch.isfinite(parameter.grad).all())

    def test_cache_keeps_source_memory_and_cannot_silently_bypass_learned_copy(self):
        self.assertTrue(self.model.supports_kv_cache)
        self.model.eval()
        with torch.inference_mode():
            tokens = torch.tensor([[5, 2, 3]])
            logits, cache, length = self.model.prefill_with_cache(tokens)
            torch.testing.assert_close(logits, self.model(tokens)[:, -1], atol=1e-5, rtol=1e-5)
            self.assertIn('copy_keys', cache[-1])
            with self.assertRaisesRegex(ValueError, 'memória de origem'):
                self.model.forward_next_with_cache(torch.tensor([7]), length, cache[:-1], length)

    def test_existing_model_has_identical_state_shape_without_copy_setting(self):
        self.config.pop('copy_attention_size')
        self.config.pop('copy_boundary_ids')
        plain = build_model(self.config)
        self.assertTrue(plain.supports_kv_cache)
        self.assertFalse(any(name.startswith('copy_') for name in plain.state_dict()))

    def test_missing_copy_boundary_rejects_architecture(self):
        self.config.pop('copy_boundary_ids')
        with self.assertRaisesRegex(ValueError, 'delimitador'):
            build_model(self.config)


if __name__ == '__main__':
    unittest.main()
