import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pretrain"))
from build_code_sft import convert, passes, qualification_functions  # noqa: E402

ROW = {"task_id": 1, "text": "Write a function to sum the unique items of a list.",
       "code": "def unique_sum(items):\r\n\treturn sum(set(items))",
       "test_list": ["assert set([unique_sum([1, 1, 2])]) == {3}", "assert unique_sum([]) == 0"],
       "test_setup_code": ""}


class ConvertTest(unittest.TestCase):
    def test_builds_a_valid_plan_that_passes_its_tests(self):
        function, request, plan = convert(ROW)
        self.assertEqual(function, "unique_sum")  # ignora set(), que vem antes no assert
        self.assertIn("unique_sum(items)", request)
        files = {op["arguments"]["path"]: op["arguments"]["content"] for op in json.loads(plan)["operations"]}
        self.assertEqual(set(files), {"app.py", "test_app.py"})
        self.assertNotIn("\t", files["app.py"])
        self.assertTrue(passes(plan))

    def test_wrong_solution_is_rejected(self):
        _, _, plan = convert({**ROW, "code": "def unique_sum(items):\n    return 0"})
        self.assertFalse(passes(plan))

    def test_untested_function_is_skipped(self):
        self.assertIsNone(convert({**ROW, "code": "def other(items):\n    return 0"}))

    def test_knows_the_qualification_functions(self):
        self.assertIn("caesar", qualification_functions())


if __name__ == "__main__":
    unittest.main()
