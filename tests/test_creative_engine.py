import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from creative_engine import (CreativeConfig, creative_guidance, extract_constraints,
                             fullstack_guidance, interface_design_guidance,
                             is_fullstack_request, is_interface_request, profile_for,
                             score_candidate, select_candidate, wants_variations)
from generation_utils import sample_creative_token
from model_server import ModelService


class CreativeEngineTests(unittest.TestCase):
    def test_brief_distinguishes_variations_from_single_piece(self):
        self.assertTrue(wants_variations("Crie três ideias de campanha para café"))
        self.assertFalse(wants_variations("Escreva um poema para minha irmã"))
        self.assertIn("mecanismos realmente diferentes", creative_guidance("Dê ideias"))

    def test_profiles_and_constraints_are_explicit(self):
        self.assertEqual(profile_for("Faça um brainstorm radical").name, "divergent")
        self.assertEqual(profile_for("Revise este slogan e preserve o tom").name, "focused")
        self.assertEqual(extract_constraints("Crie algo com humor sem desconto"),
                         {"forbidden": ["desconto"], "required": ["humor"]})
        scored = score_candidate(
            "Crie uma frase com humor sem desconto",
            "Uma frase com humor sobre a manhã e o ritual do café.",
            CreativeConfig(),
        )
        self.assertEqual(scored["forbidden_hits"], 0)
        self.assertEqual(scored["required_hits"], 1)

    def test_interface_track_requires_product_and_engineering_states(self):
        question = "Crie uma interface de tarefas para celular"
        self.assertTrue(is_interface_request(question))
        guidance = interface_design_guidance(question)
        self.assertIn("estados vazio/carregando/erro/sucesso", guidance)
        self.assertIn("teclado", guidance)
        self.assertIn("dashboard", guidance)

    def test_fullstack_track_requires_one_coherent_contract(self):
        question = "Crie um frontend com API e banco para controlar pedidos"
        self.assertTrue(is_fullstack_request(question))
        guidance = fullstack_guidance(question)
        self.assertIn("contrato de cada API", guidance)
        self.assertIn("dados hardcoded", guidance)
        self.assertIn("testes de unidade e de integração", guidance)

    def test_selection_penalizes_explicit_exclusion(self):
        question = "Crie ideias para uma campanha de café sem desconto"
        candidates = [
            "Uma campanha de café com desconto especial e desconto para quem chegar primeiro.",
            "Uma campanha de café mostra a rotina do produtor em fotografias e convida clientes a contar seu ritual da manhã.",
        ]
        answer, index = select_candidate(question, candidates)
        self.assertEqual(index, 1)
        self.assertIn("produtor", answer)

    def test_nucleus_sampling_never_selects_masked_token(self):
        torch.manual_seed(7)
        logits = torch.tensor([8.0, 7.0, float("-inf")])
        samples = {sample_creative_token(logits) for _ in range(100)}
        self.assertEqual(samples, {0, 1})

    def test_service_selects_candidate_and_reports_generation(self):
        service = ModelService("benchmark-only", trace_path=None)
        answers = iter([
            "Uma campanha de café com desconto especial e desconto para quem chegar primeiro.",
            "Uma campanha de café mostra a rotina do produtor em fotografias e convida clientes a contar seu ritual da manhã.",
        ])
        def generate(*_args, **kwargs):
            self.assertTrue(kwargs["creative_mode"])
            service.last_generation = {"decoding": "nucleus"}
            return next(answers)
        deltas = []
        with patch.object(service, "local_reply", side_effect=generate) as mocked:
            response = service.creative_reply(
                [{"role": "user", "content": "Crie ideias para uma campanha de café sem desconto"}],
                "Crie ideias para uma campanha de café sem desconto",
                on_delta=deltas.append,
            )
        self.assertEqual(mocked.call_count, 2)
        self.assertIn("produtor", response)
        self.assertEqual(deltas, [response])
        self.assertEqual(service.last_generation["creative_candidates"], 2)
        self.assertEqual(service.last_generation["creative_selected"], 1)

    def test_reply_routes_creative_intent_to_engine(self):
        service = ModelService("benchmark-only", trace_path=None)
        with patch.object(service, "creative_reply", return_value="Uma resposta criativa específica.") as engine:
            result = service.reply([{"role": "user", "content": "Crie uma ideia de campanha para café."}])
        engine.assert_called_once()
        self.assertEqual(result["backend"], "local-creative")


if __name__ == "__main__":
    unittest.main()
