import json
import tempfile
import unittest
from pathlib import Path

from distill_hf_agent_concepts import distill


class DistillHFTests(unittest.TestCase):
    def test_destila_principios_sem_dependencia_de_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            output = root / "principles.json"
            cases = root / "cases.jsonl"
            source.write_text(json.dumps({"dataset": "x/y", "text": "tool result error schema validation"}) + "\n", encoding="utf-8")
            report = distill(source, output, cases)
            self.assertEqual(report["examples_examined"], 1)
            self.assertFalse(json.loads(output.read_text())["runtime_dependency"])
            self.assertEqual(len(cases.read_text().splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
