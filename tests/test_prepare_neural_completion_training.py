import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from prepare_neural_completion_training import build_training_rows


class PrepareNeuralCompletionTrainingTests(unittest.TestCase):
    def test_combines_positive_and_negative_examples(self):
        rows = build_training_rows()
        labels = {row["label"] for row in rows}
        self.assertEqual(labels, {"positive", "negative"})
        self.assertGreaterEqual(len(rows), 10)
        self.assertTrue(any(row["label"] == "positive" for row in rows))
        self.assertTrue(any(row["label"] == "negative" for row in rows))
        self.assertIn("good-response", {row["failure_mode"] for row in rows if row["label"] == "positive"})


if __name__ == "__main__":
    unittest.main()
