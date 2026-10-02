import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_neural_completion_training_v2 import build_dataset


class NeuralCompletionTrainingV2Test(unittest.TestCase):
    def test_has_core_failure_modes_and_positive_examples(self):
        rows = build_dataset()
        labels = {row["label"] for row in rows}
        self.assertEqual(labels, {"positive", "negative"})
        modes = {row["failure_mode"] for row in rows}
        self.assertIn("good-response", modes)
        self.assertIn("prompt-leak", modes)
        self.assertIn("not-relevant", modes)
        self.assertIn("repeated-character", modes)
        self.assertGreaterEqual(len(rows), 16)

    def test_positive_answers_are_substantive(self):
        rows = build_dataset()
        positive = [row for row in rows if row["label"] == "positive"]
        for row in positive:
            self.assertGreater(len(row["answer"].split()), 10)
            self.assertNotIn("<|", row["answer"])
            self.assertNotIn("erro erro", row["answer"].lower())


if __name__ == "__main__":
    unittest.main()
