import json
import tempfile
import unittest
from pathlib import Path

from multimodal import analyze_visual, capability_profile, inspect_media, ocr_available
from neural_adapter import LocalCompetenceAdapter


ROOT = Path(__file__).resolve().parents[1]


class LocalAdapterTests(unittest.TestCase):
    def test_competence_adapter_retorna_apenas_intencao_autoral_conhecida(self):
        adapter = LocalCompetenceAdapter(ROOT / "python" / "data" / "neural_professional_v1.jsonl")
        answer = adapter.answer([{"role": "user", "content": "O que é uma variável em Python?"}])
        self.assertIsNotNone(answer)
        self.assertIn("variável", answer[0])
        self.assertEqual(answer[1]["match"], "normalized-exact-intent")
        self.assertIsNone(adapter.answer([{"role": "user", "content": "Pergunta não cadastrada"}]))

    def test_adaptador_multimodal_expõe_limites_e_observações(self):
        self.assertTrue(ocr_available())
        profile = capability_profile()
        self.assertEqual(profile["schema"], "local-multimodal-capabilities/v1")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.png"
            # PNG mínimo com IHDR suficiente para inspecionar dimensões.
            path.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + b"\x00\x00\x00\x0dIHDR"
                + (2).to_bytes(4, "big") + (3).to_bytes(4, "big")
                + b"\x08\x02\x00\x00\x00"
            )
            inspection = inspect_media(path)
            visual = analyze_visual(path)
            self.assertEqual(inspection["dimensions"], {"width": 2, "height": 3})
            self.assertEqual(visual["semantic_status"], "bounded-observations")
            self.assertIn("não é um VLM", visual["limitation"])


if __name__ == "__main__":
    unittest.main()
