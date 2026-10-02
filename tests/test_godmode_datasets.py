import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_godmode_datasets import key


class GodModeDatasetTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "python/data/godmode_datasets_manifest_v1.json").read_text(encoding="utf-8"))
        self.train_paths = [ROOT / "python/data/godmode_knowledge_v1.jsonl", ROOT / "python/data/godmode_procedures_v1.jsonl"]
        self.train = [json.loads(line) for path in self.train_paths for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.heldout = [json.loads(line) for line in (ROOT / "model/training/godmode-heldout-v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        self.evaluation = [json.loads(line) for line in (ROOT / "corpus/eval/godmode_heldout_tasks_v1.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_manifest_counts_and_schema(self):
        self.assertEqual(self.manifest["schema"], "godmode-datasets/v1")
        self.assertEqual(len(self.train), 91)
        self.assertEqual(len(self.heldout), 30)
        self.assertEqual(self.manifest["training"]["knowledge"]["examples"], 60)
        self.assertEqual(self.manifest["training"]["procedures"]["examples"], 31)

    def test_train_and_heldout_are_disjoint(self):
        train_keys = {key(row) for row in self.train}
        heldout_keys = {key(row) for row in self.heldout}
        self.assertTrue(train_keys)
        self.assertTrue(heldout_keys)
        self.assertFalse(train_keys & heldout_keys)

    def test_all_rows_are_plain_conversations_with_provenance(self):
        for row in self.train + self.heldout:
            self.assertEqual([item["role"] for item in row["messages"]], ["user", "assistant"])
            self.assertTrue(row["messages"][0]["content"].strip())
            self.assertTrue(row["messages"][1]["content"].strip())
            self.assertEqual(row["provenance"], "godmode-local-authored-v1")

    def test_finetune_script_consumes_the_two_training_parts(self):
        source = (ROOT / "python/finetune_assistant.py").read_text(encoding="utf-8")
        self.assertIn("godmode_knowledge_v1.jsonl", source)
        self.assertIn("godmode_procedures_v1.jsonl", source)

    def test_heldout_tasks_have_executable_term_hints(self):
        self.assertEqual(len(self.evaluation), len(self.heldout))
        self.assertTrue(all(row.get("terms") for row in self.evaluation))
        self.assertTrue(all(row.get("acceptance") for row in self.evaluation))


if __name__ == "__main__":
    unittest.main()
