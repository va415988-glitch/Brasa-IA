import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from agent_planner import AgentPlanner


class AgentPlannerRoutingTests(unittest.TestCase):
    def test_inspect_code_extracts_unquoted_workspace_path(self):
        call = AgentPlanner.workspace_read_request(
            "Inspecione os símbolos e imports de python/agent_planner.py."
        )

        self.assertEqual(call, ("inspect_code", {"path": "python/agent_planner.py"}))


if __name__ == "__main__":
    unittest.main()
