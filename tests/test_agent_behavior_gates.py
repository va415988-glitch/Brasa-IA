import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from model_server import ModelService


ROOT = Path(__file__).resolve().parents[1]


class AgentBehaviorGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = ModelService("behavior-gate-regression")

    def test_workflow_cases_keep_answer_grounded(self):
        path = ROOT / "model" / "eval_generation.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            with self.subTest(case=case["id"]):
                result = self.service.reply([{"role": "user", "content": case["prompt"]}])
                text = str(result.get("text") or "").lower()
                self.assertNotEqual(result.get("backend"), "proactive-learning")
                self.assertTrue(all(term.lower() in text for term in case.get("terms", [])))

    def test_heldout_agent_questions_do_not_become_unbounded_research(self):
        path = ROOT / "model" / "training" / "senior-creative-v1" / "agent-harness-heldout-v1.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            question = case["messages"][0]["content"]
            with self.subTest(question=question):
                result = self.service.reply([{"role": "user", "content": question}])
                self.assertIn(result.get("backend"), {"quality-gate", "curated-memory"})
                self.assertFalse(result.get("tool_call"))
                self.assertTrue(str(result.get("text") or "").strip())


if __name__ == "__main__":
    unittest.main()
