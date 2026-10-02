import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from fullstack_gate import assess_fullstack_plan


class FullstackGateTests(unittest.TestCase):
    def test_accepts_coherent_fullstack_plan(self):
        plan = {"operations": [
            {"tool": "create_file", "arguments": {"path": "index.html", "content": "frontend form loading empty error"}},
            {"tool": "create_file", "arguments": {"path": "server.py", "content": "backend API /api/tasks request response status code sqlite repository except error"}},
            {"tool": "create_file", "arguments": {"path": "tests/test_api.py", "content": "unittest assert test response error"}},
        ]}
        result = assess_fullstack_plan(plan)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["missing"], [])

    def test_rejects_a_frontend_only_mockup(self):
        result = assess_fullstack_plan({"operations": [
            {"tool": "create_file", "arguments": {"path": "index.html", "content": "render a dashboard with hardcoded tasks"}},
        ]})
        self.assertFalse(result["passed"])
        self.assertIn("backend", result["missing"])
        self.assertIn("contract", result["missing"])
        self.assertIn("tests", result["missing"])


if __name__ == "__main__":
    unittest.main()
