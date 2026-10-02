import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from agent_planner import AgentPlanner
from tool_registry import ToolRegistry


class TerminalPlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = AgentPlanner(ToolRegistry())

    def test_maps_only_fixed_command_profiles(self):
        cases = {
            "Execute no terminal `git status --short`": {"operation": "git_status"},
            "Rode o comando `git diff --stat`": {"operation": "git_diff_stat"},
            "Execute no terminal `cargo test`": {"operation": "project_check", "check": "cargo-test"},
            "Rode o comando `pytest`": {"operation": "project_check", "check": "pytest"},
        }
        for request, expected in cases.items():
            with self.subTest(request=request):
                self.assertEqual(self.planner.arguments("terminal_run", request), expected)
        ranked = self.planner.plan("Execute no terminal `cargo test`")
        self.assertEqual(ranked["tool"], "terminal_run")
        self.assertEqual(ranked["arguments"], {"operation": "project_check", "check": "cargo-test"})
        contract = self.planner.registry.describe("terminal_run")
        self.assertTrue(contract["requires_approval"])
        self.assertTrue(contract["side_effects"])

    def test_rejects_shells_scripts_and_unlisted_programs(self):
        for command in ("sh -c 'echo unsafe'", "python script.py", "git status && rm -rf .", "ls -la"):
            with self.subTest(command=command):
                self.assertIsNone(self.planner.arguments("terminal_run", f"Execute no terminal `{command}`"))


if __name__ == "__main__":
    unittest.main()
