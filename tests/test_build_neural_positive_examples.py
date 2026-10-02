import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_neural_positive_examples import build_dataset


class BuildNeuralPositiveExamplesTests(unittest.TestCase):
    def test_dataset_contains_complete_relevant_answers(self):
        rows = build_dataset()
        self.assertGreaterEqual(len(rows), 4)
        for row in rows:
            self.assertIn("prompt", row)
            self.assertIn("answer", row)
            self.assertGreater(len(row["answer"].split()), 10)
            self.assertNotIn("valor valor", row["answer"].lower())
            self.assertNotIn("erro erro", row["answer"].lower())


if __name__ == "__main__":
    unittest.main()
