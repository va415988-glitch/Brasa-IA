from pathlib import Path
import sys
import tempfile
import unittest

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
sys.path.insert(0, str(ROOT / 'pretrain'))
from checkpoint_io import load_checkpoint, save_checkpoint
from grow import block_order, grow_checkpoint, grow_state, target_config, verify_growth
from model import build_model

SOURCE = dict(architecture='decoder_transformer_v2', layers=4, hidden_size=128, attention_heads=4, kv_heads=2,
              vocab_size=512, context_length=64, qk_norm=True)


def trained_like_state(config=SOURCE):
    torch.manual_seed(3)
    model = build_model(config)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.add_(torch.randn_like(parameter) * 0.05)
    return model.state_dict()


class ModelGrowthTests(unittest.TestCase):
    def test_depth_growth_is_exactly_the_same_function(self):
        state = trained_like_state()
        grown = target_config(SOURCE, layers=8)
        report = verify_growth(state, SOURCE, grow_state(state, SOURCE, grown), grown, tolerance=0.0)
        self.assertEqual(report['max_abs_logit_difference'], 0.0)
        self.assertGreater(report['large_parameters'], report['small_parameters'])

    def test_width_and_depth_growth_preserve_logits_within_tolerance(self):
        state = trained_like_state()
        for sizes in (dict(hidden_size=192), dict(layers=6, hidden_size=256), dict(hidden_size=256, ffn_hidden=1024)):
            with self.subTest(sizes=sizes):
                grown = target_config(SOURCE, **sizes)
                report = verify_growth(state, SOURCE, grow_state(state, SOURCE, grown), grown)
                self.assertLess(report['relative'], 1e-3)
                self.assertEqual(grown['attention_heads'] // grown['kv_heads'], 2)

    def test_grown_model_keeps_learning(self):
        # Blocos e dimensões novos começam neutros, mas recebem gradiente.
        state = trained_like_state()
        grown = target_config(SOURCE, layers=6, hidden_size=192)
        model = build_model(grown)
        model.load_state_dict(grow_state(state, SOURCE, grown))
        tokens = torch.randint(4, 512, (2, 32))
        loss = torch.nn.functional.cross_entropy(model(tokens[:, :-1]).reshape(-1, 512), tokens[:, 1:].reshape(-1))
        loss.backward()
        new_block = dict(model.named_parameters())
        self.assertGreater(float(new_block['blocks.1.o.weight'].grad.abs().sum()), 0.0)
        self.assertGreater(float(new_block['blocks.0.down.weight'].grad[128:].abs().sum()), 0.0)

    def test_invalid_growth_is_rejected(self):
        with self.assertRaises(ValueError):
            target_config(SOURCE, layers=2)
        with self.assertRaises(ValueError):
            target_config(SOURCE, hidden_size=200)  # não é múltiplo do head_dim
        with self.assertRaises(ValueError):
            target_config(SOURCE, hidden_size=256, kv_heads=8)  # mudaria o agrupamento GQA
        with self.assertRaises(ValueError):
            target_config({**SOURCE, 'architecture': 'decoder_transformer'}, layers=8)

    def test_block_order_spreads_new_blocks(self):
        self.assertEqual(block_order(4, 8), [('old', 0), ('new', 0), ('old', 1), ('new', 1),
                                             ('old', 2), ('new', 2), ('old', 3), ('new', 3)])
        self.assertEqual(block_order(2, 3, 'top'), [('old', 0), ('old', 1), ('new', 1)])
        self.assertEqual(block_order(3, 3), [('old', 0), ('old', 1), ('old', 2)])

    def test_checkpoint_round_trip_records_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / 'small.safetensors'
            save_checkpoint({'config': SOURCE, 'state_dict': trained_like_state(), 'steps': 10}, source_path)
            target = Path(directory) / 'large.safetensors'
            report = grow_checkpoint(source_path, target, layers=6, hidden_size=192)
            loaded = load_checkpoint(target)
            self.assertEqual(loaded['config']['layers'], 6)
            self.assertEqual(loaded['config']['hidden_size'], 192)
            self.assertEqual(loaded['growth_lineage'][-1]['to_shape']['hidden_size'], 192)
            self.assertLess(report['relative'], 1e-3)
            model = build_model(loaded['config'])
            model.load_state_dict(loaded['state_dict'])


if __name__ == '__main__':
    unittest.main()
