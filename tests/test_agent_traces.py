import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from agent_traces import TraceRecorder, export_planner_sft, report


class AgentTraceTests(unittest.TestCase):
    def test_trace_preserva_decisao_resultado_e_exporta_sft(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = root / "traces.jsonl"
            output_path = root / "planner.jsonl"
            recorder = TraceRecorder(trace_path)
            trace_id = recorder.start("Crie uma tela de login", "req-1")
            recorder.plan(trace_id, "Crie uma tela de login", {
                "tool": "create_web_page",
                "arguments": {"prompt": "login", "path": "preview/login.html"},
                "reason": "evidência",
                "planner": {"strategy": "contract-ranking", "candidates": ["create_web_page"]},
            }, 2.5)
            recorder.tool_result(trace_id, {"tool": "create_web_page", "ok": True, "data": {"path": "preview/login.html"}}, 1)
            recorder.completion(trace_id, {"backend": "agent-loop", "text": "Concluído.", "agent": {"status": "completed", "steps": 1}}, 5.0, 1)

            stats = report(trace_path)
            self.assertEqual(stats["plans"], 1)
            self.assertEqual(stats["tool_success_rate"], 1.0)
            exported = export_planner_sft(trace_path, output_path)
            self.assertEqual(exported["examples"], 1)
            row = json.loads(output_path.read_text(encoding="utf-8"))
            target = row["messages"][2]["tool_call"]
            self.assertEqual(target["name"], "create_web_page")
            self.assertEqual(target["arguments"]["path"], "preview/login.html")

    def test_trace_redige_segredos(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traces.jsonl"
            recorder = TraceRecorder(path)
            recorder.record("test", "trace-1", api_key="nao-vazar", nested={"password": "nao-vazar"})
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("nao-vazar", text)
            self.assertIn("[omitido]", text)


if __name__ == "__main__":
    unittest.main()
