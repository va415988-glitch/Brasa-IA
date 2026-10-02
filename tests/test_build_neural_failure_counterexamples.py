import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_neural_failure_counterexamples import build_dataset


class BuildNeuralFailureCounterexamplesTests(unittest.TestCase):
    def test_build_dataset_has_core_failure_modes(self):
        rows = build_dataset()
        modes = {row["failure_mode"] for row in rows}
        self.assertIn("repeated-words", modes)
        self.assertIn("too-short", modes)
        self.assertTrue(any(row["is_good_example"] for row in rows))
        self.assertTrue(any(not row["is_good_example"] for row in rows))
        self.assertGreaterEqual(len(rows), 8)

    def test_good_examples_are_complete_and_relevant(self):
        rows = build_dataset()
        good = [row for row in rows if row["is_good_example"]]
        for row in good:
            self.assertIn("prompt", row)
            self.assertIn("answer", row)
            self.assertGreater(len(row["answer"].split()), 10)
            self.assertNotIn("repeated", row["answer"].lower())


if __name__ == "__main__":
    unittest.main()
