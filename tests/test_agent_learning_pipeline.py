import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from agent_learning_pipeline import (
    TrainingExample,
    _split,
    evaluate_index,
    preflight,
    report_only,
    run_pipeline,
    verified_trace_examples,
)
from agent_traces import TraceRecorder


class AgentLearningPipelineTests(unittest.TestCase):
    def test_only_completed_successful_traces_become_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "traces.jsonl"
            recorder = TraceRecorder(path)

            valid = recorder.start("Liste os arquivos do workspace", "req-valid")
            recorder.plan(valid, "Liste os arquivos do workspace", {"tool": "list_files", "arguments": {}}, 1)
            recorder.tool_result(valid, {"tool": "list_files", "ok": True}, 1)
            recorder.completion(valid, {"agent": {"status": "completed"}, "text": "ok"}, 2, 1)
            recorder.record("turn_completed", valid, status="completed", review_status="approved")

            failed = recorder.start("Leia o arquivo README.md", "req-failed")
            recorder.plan(failed, "Leia o arquivo README.md", {"tool": "read_file", "arguments": {}}, 1)
            recorder.tool_result(failed, {"tool": "read_file", "ok": False}, 1)
            recorder.completion(failed, {"agent": {"status": "completed"}, "text": "falhou"}, 2, 1)

            blocked = recorder.start("Crie um arquivo", "req-blocked")
            recorder.plan(blocked, "Crie um arquivo", {"tool": "create_file", "arguments": {}}, 1)
            recorder.tool_result(blocked, {"tool": "create_file", "ok": True}, 1)
            recorder.completion(blocked, {"agent": {"status": "blocked"}, "text": "aguardando"}, 2, 1)

            examples, stats = verified_trace_examples(path)
            self.assertEqual([example.tool for example in examples], ["list_files"])
            self.assertEqual(stats["accepted"], 1)
            self.assertEqual(stats["failed_result"], 1)
            self.assertEqual(stats["unfinished"], 1)

    def test_training_canonicalizes_obvious_first_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "traces.jsonl"
            recorder = TraceRecorder(path)
            trace_id = recorder.start("Crie uma tela de login e rode os testes", "req-page")
            recorder.plan(trace_id, "Crie uma tela de login e rode os testes", {"tool": "project_checks"}, 1)
            recorder.tool_result(trace_id, {"tool": "project_checks", "ok": True}, 1)
            recorder.completion(trace_id, {"agent": {"status": "completed"}, "text": "ok"}, 2, 1)
            recorder.record("turn_completed", trace_id, status="completed", review_status="approved")
            examples, _ = verified_trace_examples(path)
            self.assertEqual(examples[0].tool, "create_web_page")
            self.assertIn("canonicalized", examples[0].source)

    def test_split_is_deterministic_and_keeps_train_and_validation(self):
        examples = [TrainingExample(f"pedido {index}", "tool_a", "fixture") for index in range(12)]
        first = _split(examples, 0.25)
        second = _split(examples, 0.25)
        self.assertEqual(first, second)
        self.assertTrue(first[0])
        self.assertTrue(first[1])
        self.assertEqual(len(first[0]) + len(first[1]), len(examples))

    def test_split_keeps_each_tool_in_training_when_it_has_multiple_examples(self):
        examples = [
            TrainingExample("pagina um", "create_web_page", "fixture"),
            TrainingExample("pagina dois", "create_web_page", "fixture"),
            TrainingExample("arquivo um", "create_file", "fixture"),
            TrainingExample("arquivo dois", "create_file", "fixture"),
            TrainingExample("workspace um", "list_files", "fixture"),
        ]
        training, validation = _split(examples, 0.4)
        self.assertEqual({item.tool for item in training}, {"create_web_page", "create_file", "list_files"})
        self.assertTrue(validation)
        self.assertEqual(len(training) + len(validation), len(examples))

    def test_candidate_is_created_without_mutating_active_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "examples.jsonl").write_text(
                "\n".join(json.dumps({"instruction": f"pedido {i}", "output": "list_files", "tool": "list_files"}) for i in range(8)) + "\n",
                encoding="utf-8",
            )
            active = root / "active.json"
            active.write_text(json.dumps({"schema": "old"}), encoding="utf-8")
            report = run_pipeline(
                root=root,
                trace_path=root / "missing-traces.jsonl",
                corpus_dir=corpus,
                active_index=active,
                runs_dir=root / "runs",
                python=Path(sys.executable),
                skip_batteries=True,
            )
            self.assertEqual(report["status"], "candidate-blocked")
            self.assertEqual(json.loads(active.read_text(encoding="utf-8"))["schema"], "old")
            candidate = Path(report["candidate"]["path"])
            self.assertTrue(candidate.exists())
            self.assertTrue(report["gates"]["candidate_integrity"])
            self.assertFalse(report["gates"]["training_quality"])

    def test_preflight_is_read_only_and_exposes_actionable_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "examples.jsonl").write_text(
                '{"instruction":"pedido","output":"list_files","tool":"list_files"}\n',
                encoding="utf-8",
            )
            result = preflight(
                root=root,
                trace_path=root / "missing-traces.jsonl",
                corpus_dir=corpus,
                active_index=root / "active.json",
                runs_dir=root / "runs",
                report_path=None,
                python=Path(sys.executable),
                require_batteries=False,
            )
            self.assertEqual(result["status"], "ready-with-warnings")
            self.assertFalse(result["errors"])
            self.assertIn("trace de execução ainda não existe", result["warnings"])
            self.assertFalse((root / "runs").exists())

    def test_report_contains_quality_rates_and_recommendations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "examples.jsonl").write_text(
                '{"instruction":"pedido","output":"list_files","tool":"list_files"}\n',
                encoding="utf-8",
            )
            result = report_only(root / "missing-traces.jsonl", corpus)
            quality = result["quality"]
            self.assertEqual(quality["verified_trace_examples"], 0)
            self.assertEqual(quality["accepted_trace_rate"], 0.0)
            self.assertEqual(quality["status"], "needs-attention")
            self.assertTrue(quality["recommendations"])

    def test_run_stops_before_writing_when_preflight_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "examples.jsonl").write_text(
                '{"instruction":"pedido","output":"list_files","tool":"list_files"}\n',
                encoding="utf-8",
            )
            runs = root / "runs"
            result = run_pipeline(
                root=root,
                trace_path=root / "missing-traces.jsonl",
                corpus_dir=corpus,
                active_index=root / "active.json",
                runs_dir=runs,
                report_path=None,
                python=root / "missing-python",
                skip_batteries=True,
            )
            self.assertEqual(result["status"], "blocked-preflight")
            self.assertFalse(runs.exists())

    def test_index_evaluation_reports_accuracy(self):
        artifact = {
            "tools": {
                "list_files": {"features": {"workspace": 2}},
                "read_file": {"features": {"readme": 2}},
            }
        }
        result = evaluate_index(artifact, [TrainingExample("Liste o workspace", "list_files", "test")])
        self.assertEqual(result["accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
