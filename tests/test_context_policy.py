import unittest

from context_policy import inspect_context


class ContextPolicyTests(unittest.TestCase):
    def test_checkpoint_curto_e_bloqueado(self):
        policy = inspect_context({"context_length": 256, "generation_length": 128})
        self.assertFalse(policy["production_eligible"])
        self.assertFalse(policy["generation_policy"]["production_eligible"])
        self.assertEqual(policy["status"], "experimental")

    def test_checkpoint_longo_precisa_de_contexto_e_geracao(self):
        policy = inspect_context({"context_length": 8192, "generation_length": 4096})
        self.assertTrue(policy["production_eligible"])
        self.assertTrue(policy["generation_policy"]["production_eligible"])


if __name__ == "__main__":
    unittest.main()
