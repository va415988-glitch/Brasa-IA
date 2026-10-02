import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_agent_tool_eval_top5 import inspect_case, observed_tools


class AgentToolEventParsingTests(unittest.TestCase):
    def test_workspace_inspection_counts_runtime_inspect_project_call(self):
        self.assertEqual(observed_tools([{"kind": "workspace.inspected", "payload": {}}]), ["inspect_project"])

    def test_brain_state_events_count_tool_calls_including_failed_attempts(self):
        events = [
            {"kind": "brain.state", "payload": {"state": "executing", "tool": "list_files"}},
            {"kind": "brain.state", "payload": {"state": "verifying", "tool": "list_files", "ok": False}},
            {"kind": "brain.state", "payload": {"state": "executing", "tool": "read_file"}},
            {"kind": "brain.state", "payload": {"state": "verifying", "tool": "read_file", "ok": True}},
        ]
        self.assertEqual(observed_tools(events), ["list_files", "read_file"])

    def test_tool_gym_case_passes_with_agentcore_event_envelope(self):
        case = {
            "id": "toolgym-exact-file-read",
            "source_dataset": "ToolGym/short-horizon-traj",
            "read_only": True,
            "expect": {
                "status": "completed",
                "tools_called_any": ["read_file"],
                "read_paths_all": ["agent-core/src/planner.ts"],
                "text_all": ["agent-core/src/planner.ts", "/generate"],
            },
        }
        response = {"report": {
            "status": "completed",
            "finalText": "Li agent-core/src/planner.ts; ele chama /generate.",
            "events": [
                {"kind": "brain.state", "payload": {"state": "executing", "tool": "read_file"}},
                {"kind": "brain.state", "payload": {
                    "state": "verifying", "tool": "read_file", "ok": True,
                    "evidence": ["read_file: agent-core/src/planner.ts"],
                }},
            ],
        }}
        result = inspect_case(case, response)
        self.assertTrue(result["passed"], result["checks"])
        self.assertEqual(result["tools_called"], ["read_file"])

    def test_structured_code_and_media_tools_report_the_observed_path(self):
        case = {
            "id": "inspect-code-path",
            "source_dataset": "local-routing-regression",
            "read_only": True,
            "expect": {"read_paths_all": ["src/main.py"]},
        }
        response = {"report": {
            "status": "completed",
            "finalText": "Inspecionei src/main.py.",
            "events": [{"kind": "brain.state", "payload": {
                "state": "verifying", "tool": "inspect_code", "ok": True,
                "evidence": ["inspect_code: src/main.py"],
            }}],
        }}
        result = inspect_case(case, response)
        self.assertTrue(result["passed"], result["checks"])
        self.assertEqual(result["read_paths"], ["src/main.py"])


if __name__ == "__main__":
    unittest.main()
