import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from model import build_model


class ModelInitializationTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        torch.manual_seed(42)
        self.config = {'vocab_size': 64, 'hidden_size': 32, 'layers': 2,
                       'attention_heads': 4, 'context_length': 32,
                       'initialization': 'scaled-normal-v1'}

    def test_initialization_preserves_tying_and_separates_layers(self):
        model = build_model(self.config)
        self.assertIs(model.lm_head.weight, model.token_embedding.weight)
        self.assertLess(model.token_embedding.weight.std().item(), 0.03)
        self.assertFalse(torch.equal(model.blocks.layers[0].linear1.weight,
                                     model.blocks.layers[1].linear1.weight))

    def test_future_tokens_cannot_change_prefix_prediction(self):
        model = build_model(self.config)
        short = torch.tensor([[1, 2, 3]])
        long = torch.tensor([[1, 2, 3, 4, 5]])
        for training in [True, False]:
            model.train(training)
            with torch.no_grad():
                torch.testing.assert_close(model(short)[:, -1], model(long)[:, 2], atol=1e-6, rtol=1e-5)

    def test_weights_remain_checkpoint_compatible(self):
        model = build_model(self.config)
        legacy = build_model({k: v for k, v in self.config.items() if k != 'initialization'})
        legacy.load_state_dict(model.state_dict(), strict=True)
        self.assertEqual(set(model.state_dict()), set(legacy.state_dict()))


if __name__ == '__main__':
    unittest.main()
