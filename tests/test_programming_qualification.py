import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from programming_qualification import grade, load_suite, reference_plan, summarize  # noqa: E402
from programming_qualification_tasks import regression_tasks, reserved_tasks  # noqa: E402

DATASET = ROOT / "datasets/programming_qualification_v1"
SPEC = next(c for c in json.loads((ROOT / "config/cognitive_cores.json").read_text())["cores"] if c["id"] == "programming")
DOMAINS = ["strings", "lists", "numbers", "records"]


def plan(app: str, tests: str = "import unittest\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(True)\n") -> str:
    return json.dumps({"assumptions": [], "operations": [
        {"tool": "create_file", "arguments": {"path": "app.py", "content": app}},
        {"tool": "create_file", "arguments": {"path": "test_app.py", "content": tests}}]})


class SuiteShapeTests(unittest.TestCase):
    def test_suites_meet_catalog_minimums_and_are_disjoint(self):
        reserved, regression = reserved_tasks(), regression_tasks()
        for suite in (reserved, regression):
            for domain in DOMAINS:
                self.assertGreaterEqual(sum(t["domain"] == domain for t in suite), SPEC["qualification"]["minimum_per_domain"])
        self.assertFalse({t["id"] for t in reserved} & {t["id"] for t in regression})
        self.assertFalse({t["function"] for t in reserved} & {t["function"] for t in regression})

    def test_generator_inputs_hide_oracle(self):
        text = (DATASET / "reserved.jsonl").read_text()
        self.assertNotIn("cases", text)
        self.assertNotIn("reference", text)
        self.assertNotIn("expect", text)

    def test_every_task_has_both_failure_and_success_cases(self):
        for task in reserved_tasks() + regression_tasks():
            self.assertGreaterEqual(len(task["cases"]), 3, task["id"])


class GraderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = {t["id"]: t for t in load_suite(DATASET, "reserved")}
        cls.task = cls.tasks["numbers-is_prime"]

    def test_reference_passes(self):
        refs = {t["id"]: t for t in reserved_tasks()}
        self.assertTrue(grade(self.task, reference_plan(refs["numbers-is_prime"]))["passed"])

    def test_wrong_behavior_is_rejected_even_with_passing_own_tests(self):
        result = grade(self.task, plan("def is_prime(n):\n    return n > 1\n"))
        self.assertTrue(result["own_tests_passed"])
        self.assertFalse(result["hidden_tests_passed"])
        self.assertFalse(result["passed"])

    def test_missing_own_tests_is_rejected(self):
        correct = next(t for t in reserved_tasks() if t["id"] == "numbers-is_prime")["reference"]
        no_tests = plan(correct, "import unittest\n")
        self.assertFalse(grade(self.task, no_tests)["passed"])

    def test_invalid_json_and_empty_output_fail_contract(self):
        for raw in ("", "not json", "{}"):
            self.assertFalse(grade(self.task, raw)["contract_valid"])

    def test_cannot_occupy_hidden_test_file(self):
        raw = json.dumps({"assumptions": [], "operations": [
            {"tool": "create_file", "arguments": {"path": "test_independent.py", "content": "x = 1\n"}}]})
        result = grade(self.task, raw)
        self.assertTrue(result["unsafe"])
        self.assertFalse(result["passed"])

    def test_infinite_loop_is_killed(self):
        result = grade(self.task, plan("def is_prime(n):\n    while True:\n        pass\n", "import unittest\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(True)\n"))
        self.assertFalse(result["passed"])

    def test_mutating_arguments_is_rejected(self):
        task = {t["id"]: t for t in load_suite(DATASET, "reserved")}["lists-remove_value"]
        bad = plan("def remove_value(items, value):\n    while value in items:\n        items.remove(value)\n    return items\n")
        self.assertFalse(grade(task, bad)["hidden_tests_passed"])

    def test_type_strictness(self):
        task = {t["id"]: t for t in load_suite(DATASET, "reserved")}["lists-chunk"]
        bad = plan("def chunk(items, size):\n    if size < 1:\n        raise ValueError('s')\n    return tuple(tuple(items[i:i+size]) for i in range(0, len(items), size))\n")
        self.assertFalse(grade(task, bad)["hidden_tests_passed"])


class SummaryTests(unittest.TestCase):
    def rows(self, passing):
        out = []
        for domain in DOMAINS:
            for i in range(24):
                out.append({"domain": domain, "passed": i < passing, "unsafe": False, "contract_valid": True})
        return out

    def test_thresholds(self):
        self.assertTrue(summarize(SPEC, self.rows(24), DOMAINS)["meets_catalog_thresholds"])
        self.assertTrue(summarize(SPEC, self.rows(22), DOMAINS)["meets_catalog_thresholds"])
        self.assertFalse(summarize(SPEC, self.rows(21), DOMAINS)["meets_catalog_thresholds"])
        self.assertFalse(summarize(SPEC, [], DOMAINS)["meets_catalog_thresholds"])

    def test_one_unsafe_answer_blocks(self):
        rows = self.rows(24)
        rows[0]["unsafe"] = True
        self.assertFalse(summarize(SPEC, rows, DOMAINS)["meets_catalog_thresholds"])

    def test_too_few_cases_blocks(self):
        rows = [r for r in self.rows(24) if not (r["domain"] == "records")][:] + self.rows(24)[:10]
        self.assertFalse(summarize(SPEC, rows, DOMAINS)["meets_catalog_thresholds"])


if __name__ == "__main__":
    unittest.main()
