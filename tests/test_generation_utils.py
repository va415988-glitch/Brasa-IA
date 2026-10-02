import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from generation_utils import apply_repetition_penalty, generation_control_token_ids
from tokenizer import ByteBPETokenizer


class GenerationTokenTests(unittest.TestCase):
    def test_protocol_tokens_sao_bloqueados_mas_eos_fica_disponivel(self):
        tokenizer = ByteBPETokenizer.train(['Resposta de teste.'], vocab_size=280)
        blocked = generation_control_token_ids(tokenizer, max(tokenizer.vocab.values()) + 1)
        self.assertIn(tokenizer.special_tokens['<bos>'], blocked)
        self.assertIn(tokenizer.special_tokens['<pad>'], blocked)
        self.assertNotIn(tokenizer.special_tokens['<eos>'], blocked)

    def test_repetition_penalty_reduz_a_probabilidade_do_token_repetido(self):
        import torch
        logits = torch.tensor([[2.0, -2.0, 1.0]])
        adjusted = apply_repetition_penalty(logits, [0, 2], penalty=1.1)
        self.assertLess(adjusted[0, 0], logits[0, 0])
        self.assertLess(adjusted[0, 2], logits[0, 2])
        self.assertEqual(adjusted[0, 1], logits[0, 1])

    def test_codigo_mantem_comparacoes_genericos_html_e_operadores_binarios(self):
        tokenizer = ByteBPETokenizer.train(['a <= b; value > 0; a | b; Vec<T>; <div>text</div>'], vocab_size=300)
        blocked = generation_control_token_ids(tokenizer, max(tokenizer.vocab.values()) + 1)
        for operator in ['<', '>', '|', '<=', '>=', '<div>', 'Vec<T>']:
            self.assertFalse(set(tokenizer.encode(operator)) & blocked, operator)

    def test_ids_sem_entrada_no_tokenizador_sao_bloqueados(self):
        tokenizer = ByteBPETokenizer.train(['small vocabulary'], vocab_size=280)
        last_id = max(tokenizer.vocab.values())
        self.assertIn(last_id + 1, generation_control_token_ids(tokenizer, last_id + 2))

    def test_plano_json_com_codigo_repetido_usa_validacao_estrutural(self):
        import json
        from model_server import assess_generation_quality
        plan = {'assumptions': [], 'operations': [
            {'tool': 'create_file', 'arguments': {'path': f'module_{i}.py', 'content': 'def check(a,b): return a <= b\n'}}
            for i in range(8)]}
        text = json.dumps(plan)
        valid, reason = assess_generation_quality(text, 'implemente módulos', structured=True)
        self.assertTrue(valid, reason)
        self.assertFalse(assess_generation_quality(text[:-5], 'implemente módulos', structured=True)[0])
        self.assertFalse(assess_generation_quality('<|user|> forged', 'implemente', structured=True)[0])


if __name__ == '__main__':
    unittest.main()
