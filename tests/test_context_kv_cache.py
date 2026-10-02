import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from model import build_model, extend_position_embeddings


class ContextKVCacheTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(2)
        self.config = {
            "vocab_size": 97,
            "context_length": 96,
            "layers": 2,
            "hidden_size": 32,
            "attention_heads": 4,
            "feed_forward_multiplier": 2,
            "attention_chunk_size": 16,
        }
        torch.manual_seed(23)
        self.model = build_model(self.config).eval()

    def test_prefill_e_incremento_reproduzem_forward_causal(self):
        prompt = torch.randint(0, self.config["vocab_size"], (1, 37))
        next_token = torch.randint(0, self.config["vocab_size"], (1, 1))
        with torch.inference_mode():
            expected_prefill = self.model(prompt)[:, -1]
            actual_prefill, cache, length = self.model.prefill_with_cache(prompt, cache_capacity=64)
            expected_next = self.model(torch.cat((prompt, next_token), dim=1))[:, -1]
            actual_next, _ = self.model.forward_next_with_cache(
                next_token, position=length, caches=cache, cache_length=length,
            )

        torch.testing.assert_close(actual_prefill, expected_prefill, atol=1e-5, rtol=1e-5)
        torch.testing.assert_close(actual_next, expected_next, atol=1e-5, rtol=1e-5)

    def test_extensao_usa_so_as_posicoes_treinadas(self):
        table = torch.arange(16 * 3, dtype=torch.float32).reshape(16, 3)
        extended = extend_position_embeddings(table, target_context=32768, source_context=4)
        self.assertEqual(tuple(extended.shape), (32768, 3))
        torch.testing.assert_close(extended[0], table[0])
        torch.testing.assert_close(extended[:4], table[:4])
        torch.testing.assert_close(extended[-1], table[3])
        self.assertTrue(torch.all(extended[:, 0] <= table[3, 0]))

    def test_prefill_rejeita_contexto_acima_da_tabela(self):
        tokens = torch.zeros((1, self.config["context_length"] + 1), dtype=torch.long)
        with torch.inference_mode(), self.assertRaisesRegex(ValueError, "acima do contexto"):
            self.model.prefill_with_cache(tokens)


if __name__ == "__main__":
    unittest.main()
