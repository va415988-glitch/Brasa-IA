import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from select_neural_candidate import _scan_directory, rank_candidate_reports


class SelectNeuralCandidateTests(unittest.TestCase):
    def test_ranks_by_pass_rate_then_passed_count(self):
        candidates = {
            "baseline": {"passed": 0, "total": 12, "pass_rate": 0.0},
            "candidate_a": {"passed": 4, "total": 12, "pass_rate": 0.333},
            "candidate_b": {"passed": 6, "total": 12, "pass_rate": 0.5},
            "candidate_c": {"passed": 6, "total": 20, "pass_rate": 0.5},
        }
        ranked = rank_candidate_reports(candidates)
        self.assertEqual(ranked[0]["name"], "candidate_b")
        self.assertEqual(ranked[1]["name"], "candidate_c")
        self.assertEqual(ranked[-1]["name"], "baseline")

    def test_ignores_invalid_reports(self):
        candidates = {
            "broken": {"passed": "x", "total": 12},
            "ok": {"passed": 3, "total": 12, "pass_rate": 0.25},
        }
        ranked = rank_candidate_reports(candidates)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["name"], "ok")

    def test_scan_directory_detects_real_neural_eval_filenames(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "model").mkdir()
            (root / "model" / "candidate-neural-eval.json").write_text(json.dumps({"passed": 2, "total": 4, "pass_rate": 0.5}), encoding="utf-8")
            (root / "model" / "neural-candidate.json").write_text(json.dumps({"passed": 3, "total": 4, "pass_rate": 0.75}), encoding="utf-8")
            (root / "model" / "notes.json").write_text(json.dumps({"summary": "not a benchmark"}), encoding="utf-8")

            candidates = _scan_directory(root / "model")

            self.assertEqual(set(candidates), {"candidate-neural-eval", "neural-candidate"})


if __name__ == "__main__":
    unittest.main()
