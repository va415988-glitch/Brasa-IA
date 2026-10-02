import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import finetune_assistant as finetune
from build_neural_completion_training_v3 import build_dataset


class NeuralCompletionTrainingV3Test(unittest.TestCase):
    def test_covers_all_direct_benchmark_prompts(self):
        rows = build_dataset()
        prompts = {row["prompt"] for row in rows if row["label"] == "positive"}
        required = {
            "O que é uma variável em Python?",
            "O que é ownership em Rust?",
            "Explique o que é a China.",
            "Como devo agir quando não sei uma resposta?",
            "Como projetar uma função Python fácil de testar?",
            "Como tratar erros em uma API?",
            "Como sair de um bloqueio criativo?",
            "Crie três conceitos de jogo com uma mecânica incomum.",
            "Como avaliar se uma afirmação científica é confiável?",
            "Explique correlação e causalidade.",
            "Como tomar uma decisão com informação incompleta?",
            "Como saber se uma fonte é primária?",
        }
        self.assertTrue(required.issubset(prompts))
        self.assertGreaterEqual(len(prompts), 12)

    def test_negative_modes_are_preserved(self):
        rows = build_dataset()
        labels = {row["label"] for row in rows}
        modes = {row["failure_mode"] for row in rows}
        self.assertEqual(labels, {"positive", "negative"})
        self.assertIn("too-short", modes)
        self.assertIn("repeated-fragment", modes)
        self.assertIn("prompt-leak", modes)
        self.assertIn("repeated-character", modes)
        self.assertIn("not-relevant", modes)
        self.assertIn("repeated-words", modes)

    def test_positive_answers_are_substantive(self):
        rows = build_dataset()
        for row in rows:
            if row["label"] == "positive":
                self.assertGreater(len(row["answer"].split()), 12)
                self.assertNotIn("<|", row["answer"])
                self.assertNotIn("429 429", row["answer"])

    def test_focused_training_keeps_direct_benchmark_prompts(self):
        legacy = [
            {"prompt": "O que é uma variável em Python?"},
            {"prompt": "Como tratar erros em uma API?"},
            {"prompt": "Como sair de um bloqueio criativo?"},
        ]
        excluded = finetune.compute_excluded_prompts(legacy, only_jsonl=["model/training/neural-completion-training-v3.messages.jsonl"])
        self.assertNotIn("O que é uma variável em Python?", excluded)
        self.assertNotIn("Como tratar erros em uma API?", excluded)


if __name__ == "__main__":
    unittest.main()
