import unittest

from tests.model_assessment import keyword_hit_rate, selection_accuracy


class ModelAssessmentTests(unittest.TestCase):
    def test_selection_accuracy_counts_expected_matches(self):
        rows = [
            {"expected": "read_file", "actual": "read_file"},
            {"expected": "create_file", "actual": "search_files"},
            {"expected": "project_checks", "actual": "project_checks"},
        ]
        self.assertAlmostEqual(selection_accuracy(rows), 2 / 3)

    def test_keyword_hit_rate_requires_relevant_terms(self):
        rows = [
            {"required": ["rust", "ownership"], "answer": "Rust ownership define o dono do valor."},
            {"required": ["rust", "ownership"], "answer": "Rust é legal."},
        ]
        self.assertAlmostEqual(keyword_hit_rate(rows), 0.75)


if __name__ == "__main__":
    unittest.main()
