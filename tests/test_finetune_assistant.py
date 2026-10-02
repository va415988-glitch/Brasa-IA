import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from finetune_assistant import clean_rows, encode_rows, question_key, split_rows, training_views
from tokenizer import ByteBPETokenizer


def pair(question, answer='Uma resposta concreta.'):
    return {'messages': [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}]}


class FinetuningTests(unittest.TestCase):
    def test_code_windows_preserve_request_and_every_target_exactly_once(self):
        question = 'ORIGINAL_REQUEST_42 descreva o cálculo'
        answer = '\n'.join(f'def operation_{i}(value): return value + {i}' for i in range(40))
        tokenizer = ByteBPETokenizer.train([question, answer], vocab_size=350)
        x, y = encode_rows([pair(question, answer)], tokenizer, 128, preserve_prompt=True)
        self.assertGreater(len(x), 3)
        for window in x: self.assertIn('ORIGINAL_REQUEST_42', tokenizer.decode(window.tolist()))
        self.assertEqual(y[y != -100].tolist(), tokenizer.encode_fast(answer + '\n', add_eos=True))

    def test_code_window_budget_refuses_artificial_eos_in_incomplete_answer(self):
        tokenizer = ByteBPETokenizer.train(['pedido resposta'], vocab_size=280)
        with self.assertRaisesRegex(ValueError, 'EOS artificial'):
            encode_rows([pair('pedido', 'resposta ' * 100)], tokenizer, 128,
                        max_answer_tokens=20, preserve_prompt=True)
    def test_deduplication_and_evaluation_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.jsonl'
            records = [pair('Olá?'), pair('ola!'), pair('Teste reservado'),
                       {'messages': [{'role': 'assistant', 'tool_call': {}}]}]
            path.write_text('\n'.join(json.dumps(row) for row in records), encoding='utf-8')
            rows, rejected = clean_rows([path], {question_key(pair('Teste reservado'))})
            self.assertEqual(len(rows), 1)
            self.assertEqual(rejected, {'duplicate_question': 1, 'evaluation_question': 1, 'not_plain_pair': 1})

    def test_split_is_stable_and_disjoint(self):
        rows = [pair(f'Pergunta {i}') for i in range(100)]
        train, val = split_rows(rows)
        train_keys = {question_key(row) for row in train}
        val_keys = {question_key(row) for row in val}
        self.assertFalse(train_keys & val_keys)
        self.assertEqual(train_keys, {question_key(row) for row in split_rows(list(reversed(rows)))[0]})

    def test_multi_turn_oversampling_leaves_single_turn_rows_once(self):
        single = pair('Uma pergunta')
        multi = {'messages': [
            {'role': 'user', 'content': 'A'},
            {'role': 'assistant', 'content': 'B'},
            {'role': 'user', 'content': 'C'},
            {'role': 'assistant', 'content': 'D'},
        ]}
        views = training_views([single, multi], multi_turn_repeat=4)
        self.assertEqual(views.count(single), 1)
        self.assertEqual(views.count(multi), 4)

    def test_fast_encoder_matches_reference_encoder(self):
        tokenizer = ByteBPETokenizer.train(
            ['Pergunta Resposta repetida ' * 30, 'Olá, mundo! 😊 café', 'abcabcabc'],
            vocab_size=340,
            min_frequency=2,
        )
        samples = [
            '', 'a', 'abcabcabc', 'Pergunta Resposta repetida ' * 8,
            'Olá, mundo! 😊 café', '東京 — тест — العربية', '\n'.join(str(i) for i in range(50)),
        ]
        for text in samples:
            for add_bos, add_eos in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(text=text[:24], add_bos=add_bos, add_eos=add_eos):
                    self.assertEqual(
                        tokenizer.encode_fast(text, add_bos=add_bos, add_eos=add_eos),
                        tokenizer.encode(text, add_bos=add_bos, add_eos=add_eos),
                    )

    def test_only_answer_and_eos_have_loss(self):
        tokenizer = ByteBPETokenizer.train(['Pergunta Resposta'], vocab_size=260)
        row = pair('Pergunta', 'Resposta')
        x, y = encode_rows([row], tokenizer, 128)
        expected = tokenizer.encode('Resposta\n', add_eos=True)
        self.assertEqual(y[y != -100].tolist(), expected)
        self.assertEqual(x.shape, y.shape)
        self.assertEqual(x.shape[1], 128)
        # A quebra de janela não elimina nem duplica tokens de resposta.
        _, short_y = encode_rows([row], tokenizer, 16)
        self.assertEqual(short_y[short_y != -100].tolist(), expected)

    def test_multi_turn_history_is_context_and_only_final_answer_has_loss(self):
        tokenizer = ByteBPETokenizer.train(
            ['Primeiro assunto resposta intermediaria pergunta final resposta final'],
            vocab_size=340, min_frequency=1,
        )
        row = {'messages': [
            {'role': 'user', 'content': 'Primeiro assunto'},
            {'role': 'assistant', 'content': 'resposta intermediaria'},
            {'role': 'user', 'content': 'pergunta final'},
            {'role': 'assistant', 'content': 'resposta final'},
        ]}
        x, y = encode_rows([row], tokenizer, 128)
        self.assertEqual(
            y[y != -100].tolist(),
            tokenizer.encode('resposta final\n', add_eos=True),
        )
        context = tokenizer.decode(x[0].tolist())
        self.assertIn('Primeiro assunto', context)
        self.assertIn('resposta intermediaria', context)
        self.assertIn('pergunta final', context)

    def test_blind_heldout_user_turn_uses_separate_reference_answer(self):
        tokenizer = ByteBPETokenizer.train(
            ['contexto pergunta resposta de referencia'], vocab_size=340, min_frequency=1,
        )
        row = {
            'messages': [
                {'role': 'user', 'content': 'contexto'},
                {'role': 'assistant', 'content': 'resposta breve'},
                {'role': 'user', 'content': 'pergunta'},
            ],
            'reference_answer': 'resposta de referencia',
        }
        _, y = encode_rows([row], tokenizer, 128)
        self.assertEqual(
            y[y != -100].tolist(),
            tokenizer.encode('resposta de referencia\n', add_eos=True),
        )

    def test_long_answer_target_continues_across_multiple_context_windows(self):
        answer = ' '.join(f'item{i},' for i in range(300))
        tokenizer = ByteBPETokenizer.train(
            ['Pergunta ' + answer], vocab_size=340, min_frequency=2,
        )
        row = pair('Pergunta', answer)
        target = tokenizer.encode_fast(answer[:100 * 8] + '\n')[:100]
        target.append(tokenizer.special_tokens['<eos>'])
        x, y = encode_rows([row], tokenizer, 16, max_answer_tokens=100)
        self.assertGreater(len(x), 2)
        self.assertEqual(y[y != -100].tolist(), target)


if __name__ == '__main__':
    unittest.main()
