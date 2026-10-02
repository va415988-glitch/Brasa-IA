import sys
import unittest
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from competency import infer_domain, learning_contract, learning_topic_from_question, research_query
from curriculum import build


class CompetencyTests(unittest.TestCase):
    def test_domain_policy_is_not_limited_to_programming(self):
        self.assertEqual(infer_domain("TypeScript"), "programming")
        self.assertEqual(infer_domain("redação argumentativa"), "writing")
        self.assertEqual(infer_domain("planejamento de projetos"), "planning")
        self.assertEqual(infer_domain("história da arte"), "general")

    def test_generic_learning_topic_is_extracted_without_hijacking_greeting(self):
        self.assertEqual(learning_topic_from_question("Como escrever uma redação argumentativa?"), "uma redação argumentativa")
        self.assertEqual(learning_topic_from_question("Me ajude a planejar um projeto"), "planejar um projeto")
        self.assertIsNone(learning_topic_from_question("Olá, tudo bem?"))

    def test_local_executor_policy_tracks_available_toolchains(self):
        bash = learning_contract('Bash')
        self.assertEqual(bash['practice']['tasks'][0]['mode'], 'local-executor')
        go = learning_contract('Go')
        expected = 'local-executor' if shutil.which('go') else 'bounded-rubric'
        self.assertEqual(go['practice']['tasks'][0]['mode'], expected)

    def test_research_policy_changes_by_domain(self):
        query = research_query("planejamento de projetos")
        self.assertIn("case studies", query)
        self.assertNotIn("official documentation", query)

    def test_contract_requires_evidence_practice_transfer_and_integration(self):
        contract = learning_contract("redação argumentativa")
        self.assertEqual(contract["domain"], "writing")
        self.assertTrue(contract["research"]["requires_primary_sources"])
        ids = {task["id"] for task in contract["practice"]["tasks"]}
        self.assertIn("unseen-transfer", ids)
        self.assertIn("integration-deliverable", ids)
        self.assertTrue(all(task["mode"] == "model-review" for task in contract["practice"]["tasks"]))

    def test_non_programming_curriculum_has_domain_specific_gaps(self):
        curriculum = build("planejamento de projetos")
        ids = {item["id"] for level in curriculum["levels"] for item in level["concepts"]}
        self.assertIn("objetivo-e-escopo", ids)
        self.assertNotIn("dados-e-controle", ids)


if __name__ == "__main__":
    unittest.main()
