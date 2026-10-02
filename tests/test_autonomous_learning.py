import json
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from agent_state import AgentState
from autonomous_learning import AutonomousLearning, LearningBudget
from portfolio import portfolio_manifest, portfolio_topics


class AutonomousLearningTests(unittest.TestCase):
    def test_portfolio_has_twenty_tracks_and_scientific_variants(self):
        manifest = portfolio_manifest()
        self.assertEqual(manifest["schema"], "learning-portfolio/v1")
        self.assertEqual(len(manifest["tracks"]), 20)
        self.assertIn("TypeScript", portfolio_topics())
        self.assertIn("MATLAB", portfolio_topics())
        self.assertIn("Julia", portfolio_topics())
        self.assertEqual(len({track["id"] for track in manifest["tracks"]}), 20)

    def test_interrupted_cycle_is_prioritized_for_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / "skills.json")
            state.learning_started("Go")
            orchestrator = AutonomousLearning(state=state, control_path=Path(directory) / "control.json",
                                              audit_path=Path(directory) / "audit.jsonl")
            plan = orchestrator.plan_cycle()
            self.assertEqual(plan["selected"]["topic"], "Go")
            self.assertEqual(plan["selected"]["reason"], "retomar ciclo interrompido")

    def test_plan_continues_partially_practiced_track_before_new_track(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / "skills.json")
            state.record_practice("Go", task="foundation-control-flow", passed=True, level="foundation",
                                  evidence="go test; aprovado")
            orchestrator = AutonomousLearning(state=state, control_path=Path(directory) / "control.json")
            plan = orchestrator.plan_cycle()
            self.assertEqual(plan["selected"]["topic"], "Go")
            self.assertIn("sources", plan["selected"]["missing_criteria"])
            self.assertIn("continuar fechando critérios", plan["selected"]["reason"])

    def test_plan_surfaces_missing_executor_as_recovery_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / "skills.json")
            state.learning_started("Go")
            orchestrator = AutonomousLearning(state=state, control_path=Path(directory) / "control.json")
            with patch("autonomous_learning.shutil.which", return_value=None):
                plan = orchestrator.plan_cycle()
            self.assertFalse(plan["selected"]["executor"]["available"])
            self.assertIn("executor local ausente", plan["selected"]["blocker"])
            self.assertEqual(plan["steps"][2]["id"], "recover")

    def test_plan_prioritizes_unstarted_track_without_touching_the_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / "skills.json")
            control = Path(directory) / "control.json"
            orchestrator = AutonomousLearning(state=state, control_path=control,
                                               budget=LearningBudget(max_source_pages=99, max_context_tokens=999999))
            before = state.all()
            plan = orchestrator.plan_cycle()
            after = state.all()
            self.assertEqual(plan["schema"], "autonomous-learning-cycle/v1")
            self.assertEqual(plan["selected"]["status"], "not_started")
            self.assertEqual(before, after)
            self.assertLessEqual(plan["budget"]["max_source_pages"], 12)
            self.assertLessEqual(plan["budget"]["max_context_tokens"], 48000)

    def test_mastered_recent_skill_is_skipped_in_favor_of_a_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / "skills.json")
            state.record_research("Python", sources=[{"url": "https://python.org"}] * 8,
                                  independent_hosts=2, documents=8, status="corroborated",
                                  curriculum={"levels": [
                                      {"id": "foundation", "concepts": [{"id": "fundamentos", "status": "covered"}]},
                                      {"id": "practice", "concepts": [{"id": "dados-e-controle", "status": "covered"}]},
                                      {"id": "transfer", "concepts": [{"id": "problema-novo", "status": "covered"}]},
                                  ], "completion": {"required_levels": 3, "required_practice_tasks": 1,
                                                    "minimum_practice_tasks": 1, "minimum_transfer_tasks": 1,
                                                    "required_independent_hosts": 2, "minimum_document_count": 8,
                                                    "requires_integration_task": False}},
                                  learning_plan={"practice": {"tasks": []}})
            state.record_practice("Python", task="integration-project", passed=True, level="integration")
            orchestrator = AutonomousLearning(state=state, control_path=Path(directory) / "control.json")
            plan = orchestrator.plan_cycle()
            self.assertNotEqual(plan["selected"]["topic"], "Python")

    def test_daily_cycle_budget_stops_repeated_autonomous_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / "skills.json")
            control = Path(directory) / "control.json"
            control.write_text(json.dumps({
                "schema": "autonomous-learning-control/v1",
                "active": None,
                "history": [{"cycle_id": "old", "finished_at": datetime.now(timezone.utc).isoformat(), "outcome": {"status": "completed"}}],
            }), encoding="utf-8")
            orchestrator = AutonomousLearning(state=state, control_path=control,
                                              audit_path=Path(directory) / "audit.jsonl",
                                              budget=LearningBudget(max_cycles_per_day=1))
            with patch("learning.start", return_value={"id": "never", "status": "running", "topic": "Python"}) as start:
                result = orchestrator.tick()
            self.assertEqual(result["status"], "cooldown")
            start.assert_not_called()

    def test_status_exposes_next_plan_history_and_audit_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = AgentState(root / "skills.json")
            control = root / "control.json"
            control.write_text(json.dumps({
                "schema": "autonomous-learning-control/v1",
                "active": None,
                "history": [{"cycle_id": "cycle-old", "finished_at": datetime.now(timezone.utc).isoformat(),
                             "outcome": {"status": "completed", "topic": "Bash", "skill": {"status": "studying", "evaluation": {"progress": 0.4, "ready": False}},
                                         "laboratory": {"status": "practice_unavailable", "laboratories": {"Bash": {"status": "practice_unavailable", "message": "executor seguro ausente"}}}}}],
            }), encoding="utf-8")
            audit = root / "audit.jsonl"
            audit.write_text(json.dumps({"event": "cycle_finished", "cycle_id": "cycle-old"}) + "\n", encoding="utf-8")
            status = AutonomousLearning(state=state, control_path=control, audit_path=audit).status()
            self.assertEqual(status["schema"], "autonomous-learning-status/v1")
            self.assertEqual(status["control"]["history"][0]["cycle_id"], "cycle-old")
            self.assertEqual(status["audit"][0]["event"], "cycle_finished")
            self.assertNotIn("sources", status["control"]["history"][0]["outcome"])
            self.assertIn("skill_progress", status["control"]["history"][0]["outcome"])
            self.assertEqual(status["control"]["history"][0]["outcome"]["skill_status"], "studying")
            self.assertEqual(status["control"]["history"][0]["outcome"]["practice_blocker"], "executor seguro ausente")
            self.assertIn("selected", status["next"])
            self.assertIn("budget", status["next"])

    def test_audit_laboratory_records_current_local_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit.jsonl"
            orchestrator = AutonomousLearning(state=AgentState(Path(directory) / "skills.json"),
                                              control_path=Path(directory) / "control.json",
                                              audit_path=audit)
            orchestrator.audit_laboratory("Go", {"status": "verified", "total": 12, "passed": 12, "tasks": []})
            row = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(row["event"], "laboratory_verified")
            self.assertEqual(row["topic"], "Go")
            self.assertEqual(row["passed"], 12)

    def test_missing_active_job_is_recorded_as_interrupted_and_counts_toward_daily_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = root / "control.json"
            control.write_text(json.dumps({
                "schema": "autonomous-learning-control/v1",
                "active": {"cycle_id": "cycle-lost", "job_id": "gone", "topic": "Go"},
                "history": [],
            }), encoding="utf-8")
            orchestrator = AutonomousLearning(
                state=AgentState(root / "skills.json"), control_path=control,
                audit_path=root / "audit.jsonl", budget=LearningBudget(max_cycles_per_day=1),
            )
            with patch("learning.snapshot", return_value=None), patch("learning.start") as start:
                result = orchestrator.tick()
                next_result = orchestrator.tick()
            self.assertEqual(result["status"], "interrupted")
            self.assertEqual(result["outcome"]["status"], "interrupted")
            self.assertEqual(next_result["status"], "cooldown")
            start.assert_not_called()
            stored = json.loads(control.read_text(encoding="utf-8"))
            self.assertIsNone(stored["active"])
            self.assertEqual(stored["history"][-1]["outcome"]["status"], "interrupted")
            events = [json.loads(line)["event"] for line in (root / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events, ["cycle_interrupted"])

    def test_concurrent_ticks_start_only_one_learning_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = AgentState(root / "skills.json")
            control = root / "control.json"
            audit = root / "audit.jsonl"
            orchestrators = [
                AutonomousLearning(state=state, control_path=control, audit_path=audit),
                AutonomousLearning(state=state, control_path=control, audit_path=audit),
            ]
            calls = []
            calls_lock = threading.Lock()

            def start(topic, *, budget):
                with calls_lock:
                    calls.append(topic)
                time.sleep(0.05)
                return {"id": "job-shared", "status": "running", "topic": topic}

            with patch("learning.start", side_effect=start), patch(
                "learning.snapshot", return_value={"id": "job-shared", "status": "running", "topic": "Go"}
            ):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda item: item.tick(), orchestrators))
            self.assertEqual(len(calls), 1)
            self.assertEqual(sorted(item["status"] for item in results), ["running", "started"])
            events = [json.loads(line)["event"] for line in audit.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events, ["cycle_starting", "cycle_started"])

    def test_start_failure_is_persisted_and_clears_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = root / "control.json"
            audit = root / "audit.jsonl"
            orchestrator = AutonomousLearning(state=AgentState(root / "skills.json"),
                                              control_path=control, audit_path=audit)
            with patch("learning.start", side_effect=RuntimeError("executor indisponível")):
                result = orchestrator.tick()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["outcome"]["error"], "executor indisponível")
            stored = json.loads(control.read_text(encoding="utf-8"))
            self.assertIsNone(stored["active"])
            self.assertEqual(stored["history"][-1]["outcome"]["status"], "failed")
            events = [json.loads(line)["event"] for line in audit.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events, ["cycle_starting", "cycle_failed"])

    def test_tick_starts_one_bounded_job_and_persists_control(self):
        with tempfile.TemporaryDirectory() as directory:
            state = AgentState(Path(directory) / "skills.json")
            control = Path(directory) / "control.json"
            with patch("learning.start", return_value={"id": "job-1", "status": "running", "topic": "Python"}) as start:
                result = AutonomousLearning(state=state, control_path=control,
                                            audit_path=Path(directory) / "audit.jsonl").tick()
            self.assertEqual(result["status"], "started")
            start.assert_called_once()
            self.assertLessEqual(start.call_args.kwargs["budget"]["max_source_pages"], 12)
            self.assertLessEqual(start.call_args.kwargs["budget"]["max_practice_tasks"], 24)
            stored = json.loads(control.read_text(encoding="utf-8"))
            self.assertEqual(stored["active"]["job_id"], "job-1")
            self.assertEqual(stored["active"]["topic"], result["plan"]["selected"]["topic"])


if __name__ == "__main__":
    unittest.main()
