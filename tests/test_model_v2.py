import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from model import build_model  # noqa: E402

CONFIG = {"architecture": "decoder_transformer_v2", "vocab_size": 97, "context_length": 64,
          "layers": 3, "hidden_size": 64, "attention_heads": 4, "kv_heads": 2}


class ModelV2Test(unittest.TestCase):
    config = CONFIG

    def setUp(self):
        torch.manual_seed(0)
        self.model = build_model(self.config).eval()
        self.tokens = torch.randint(0, 97, (1, 20))

    def test_dispatch_and_tied_head(self):
        self.assertIs(self.model.lm_head.weight, self.model.token_embedding.weight)
        self.assertEqual(tuple(self.model(self.tokens).shape), (1, 20, 97))

    def test_is_causal(self):
        with torch.inference_mode():
            full = self.model(self.tokens)
            changed = self.tokens.clone()
            changed[0, -1] = (changed[0, -1] + 1) % 97
            self.assertTrue(torch.allclose(full[:, :-1], self.model(changed)[:, :-1], atol=1e-5))

    def test_cache_matches_full_forward(self):
        with torch.inference_mode():
            full = self.model(self.tokens)
            logits, caches, length = self.model.prefill_with_cache(self.tokens[:, :12], cache_capacity=32)
            self.assertTrue(torch.allclose(logits[0], full[0, 11], atol=1e-4))
            for position in range(12, 20):
                logits, caches = self.model.forward_next_with_cache(
                    self.tokens[:, position], position, caches, length)
                length += 1
                self.assertTrue(torch.allclose(logits[0], full[0, position], atol=1e-4), position)

    def test_rejects_invalid_head_layout(self):
        with self.assertRaises(ValueError):
            build_model({**CONFIG, "kv_heads": 3})


class ModelV2QKNormTest(ModelV2Test):
    config = {**CONFIG, "qk_norm": True}

    def test_old_checkpoints_keep_their_layout(self):
        old = set(build_model(CONFIG).state_dict())
        new = set(self.model.state_dict())
        self.assertEqual(new - old, {f"blocks.{i}.{n}_norm.weight" for i in range(3) for n in "qk"})


if __name__ == "__main__":
    unittest.main()
