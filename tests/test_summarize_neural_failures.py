import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from summarize_neural_failures import summarize_report


class SummarizeNeuralFailuresTests(unittest.TestCase):
    def test_summarizes_reason_and_check_failures(self):
        report = {
            "passed": 1,
            "total": 4,
            "cases": [
                {
                    "ok": False,
                    "quality_reason": "too-short",
                    "checks": {"nonempty": True, "runtime_quality_gate": False},
                },
                {
                    "ok": False,
                    "quality_reason": "repeated-words",
                    "checks": {"nonempty": True, "runtime_quality_gate": False},
                },
                {
                    "ok": True,
                    "quality_reason": "accepted",
                    "checks": {"nonempty": True, "runtime_quality_gate": True},
                },
                {
                    "ok": False,
                    "quality_reason": "too-short",
                    "checks": {"nonempty": False, "runtime_quality_gate": False},
                },
            ],
        }

        summary = summarize_report(report)
        self.assertEqual(summary["pass_count"], 1)
        self.assertEqual(summary["fail_count"], 3)
        self.assertEqual(summary["reason_counts"]["too-short"], 2)
        self.assertEqual(summary["reason_counts"]["repeated-words"], 1)
        self.assertEqual(summary["check_fail_counts"]["runtime_quality_gate"], 3)
        self.assertEqual(summary["check_fail_counts"]["nonempty"], 1)


if __name__ == "__main__":
    unittest.main()
