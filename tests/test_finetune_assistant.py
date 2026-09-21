import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from finetune_assistant import clean_rows, encode_rows, question_key, split_rows
from tokenizer import ByteBPETokenizer


def pair(question, answer='Uma resposta concreta.'):
    return {'messages': [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}]}


class FinetuningTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
