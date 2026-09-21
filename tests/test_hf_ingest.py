import unittest

import json
import tempfile
from pathlib import Path
import json

from ingest_hf_dataset import extract_examples, normalize_dataset_id, normalize_example


class HuggingFaceIngestTests(unittest.TestCase):
    def test_normaliza_id_e_url(self):
        self.assertEqual(normalize_dataset_id("openai/gsm8k"), "openai/gsm8k")
        self.assertEqual(normalize_dataset_id("https://huggingface.co/datasets/openai/gsm8k/"), "openai/gsm8k")

    def test_rejeita_url_fora_do_escopo(self):
        with self.assertRaises(ValueError):
            normalize_dataset_id("https://example.com/data")

    def test_extrai_jsonl_e_normaliza(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "items.jsonl"
            path.write_text(json.dumps({"instruction": "  Faça   um teste seguro. "}) + "\n", encoding="utf-8")
            values = list(extract_examples(path))
            self.assertEqual(normalize_example(values[0]), "Faça um teste seguro.")

    def test_extrai_markdown_em_blocos(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.md"
            path.write_text("primeiro conteúdo suficientemente longo\n\nsegundo conteúdo suficientemente longo", encoding="utf-8")
            self.assertEqual(len(list(extract_examples(path))), 2)

    def test_catalogo_prioriza_datasets_controlados(self):
        catalog = json.loads(Path("config/huggingface_catalog.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(catalog["datasets"]), 8)
        self.assertFalse(catalog["policy"]["training_eligible"])

    def test_catalogo_tem_lote_inicial_pequeno(self):
        catalog = json.loads(Path("config/huggingface_catalog.json").read_text(encoding="utf-8"))
        known = [item for item in catalog["datasets"] if item["expected_size"] != "to-audit"]
        self.assertGreaterEqual(len(known), 3)


if __name__ == "__main__":
    unittest.main()
