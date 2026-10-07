import unittest

from context_policy import inspect_context, resolve_generation_budget, runtime_context_window


class ContextPolicyTests(unittest.TestCase):
    def test_checkpoint_curto_e_bloqueado(self):
        policy = inspect_context({"context_length": 256, "generation_length": 128})
        self.assertFalse(policy["production_eligible"])
        self.assertFalse(policy["generation_policy"]["production_eligible"])
        self.assertEqual(policy["status"], "experimental")

    def test_orcamento_padrao_de_geracao_e_2048_sem_promover_contexto_curto(self):
        # A política oficial (model/README.md) define 2.048 tokens por rodada.
        policy = inspect_context({"context_length": 256})
        self.assertEqual(policy["generation_policy"]["configured_tokens"], 2048)
        self.assertFalse(policy["generation_policy"]["production_eligible"])
        self.assertFalse(policy["production_eligible"])

    def test_orcamento_adaptativo_e_no_minimo_2048_tokens(self):
        self.assertEqual(resolve_generation_budget(None, requested_tokens=128), 2048)
        self.assertEqual(resolve_generation_budget(4096, requested_tokens=3072), 3072)
        self.assertEqual(resolve_generation_budget(512, requested_tokens=3072), 512)
        self.assertEqual(resolve_generation_budget(4096, requested_tokens=3072, override="1500"), 1500)

    def test_checkpoint_longo_precisa_de_contexto_e_geracao(self):
        policy = inspect_context({"context_length": 8192, "generation_length": 4096})
        self.assertTrue(policy["production_eligible"])
        self.assertTrue(policy["generation_policy"]["production_eligible"])

    def test_janela_dobrada_respeita_limites_treinados_e_posicionais(self):
        config = {"context_length": 16384, "training_context_length": 512}
        window = runtime_context_window(config)
        self.assertEqual(window["native_context_tokens"], 16384)
        self.assertEqual(window["trained_context_tokens"], 512)
        self.assertEqual(window["runtime_context_tokens"], 1024)
        self.assertTrue(window["runtime_extension"])
        self.assertEqual(config["context_length"], 16384)
        policy = inspect_context(config)
        self.assertEqual(policy["effective_tokens"], 1024)
        self.assertFalse(policy["production_eligible"])

    def test_janela_configurada_de_32768_expande_sem_promover_qualidade(self):
        config = {"context_length": 16384, "training_context_length": 512,
                  "runtime_context_tokens": 32768, "generation_length": 4096}
        policy = inspect_context(config)
        self.assertEqual(policy["native_context_tokens"], 16384)
        self.assertEqual(policy["runtime_context_tokens"], 32768)
        self.assertTrue(policy["runtime_extension"])
        self.assertFalse(policy["production_eligible"])
        self.assertEqual(policy["promotion_stage"], 32768)
        self.assertEqual(policy["target_tokens"], 32768)
        self.assertIn("não verificada para execução", policy["reason"])

    def test_janela_32768_verificada_fica_disponivel_sem_fingir_treino_longo(self):
        policy = inspect_context({
            "context_length": 32768,
            "training_context_length": 512,
            "runtime_context_tokens": 32768,
            "runtime_context_verified_tokens": 32768,
            "generation_length": 4096,
        })
        self.assertEqual(policy["effective_tokens"], 32768)
        self.assertTrue(policy["runtime_ready"])
        self.assertTrue(policy["runtime_supported"])
        self.assertEqual(policy["trained_context_tokens"], 512)
        self.assertEqual(policy["runtime_policy"], "configured-runtime-verified-extension")
        self.assertFalse(policy["quality_validated_at_runtime_context"])
        self.assertFalse(policy["production_eligible"])
        self.assertEqual(policy["status"], "runtime-ready")

    def test_alvo_de_producao_e_16384_tokens(self):
        policy = inspect_context({"context_length": 16384, "generation_length": 4096})
        self.assertEqual(policy["effective_tokens"], 16384)
        self.assertEqual(policy["promotion_stage"], 16384)
        self.assertTrue(policy["production_eligible"])


if __name__ == "__main__":
    unittest.main()
