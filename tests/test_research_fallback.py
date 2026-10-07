import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from model_server import ModelService


def research_message(pages, answer='', grounded=False):
    return {'role': 'tool', 'content': json.dumps({'tool': 'research_web', 'ok': True, 'data': {
        'query': 'q', 'pages': pages, 'answer': answer, 'grounded': grounded,
        'citation_ids': [page['source_id'] for page in pages]}})}


class ResearchFallbackTests(unittest.TestCase):
    def setUp(self):
        self.service = ModelService('benchmark-only', trace_path=None)

    def test_grounded_registry_result_is_delivered_with_its_url(self):
        page = {'source_id': 'web-1', 'url': 'https://www.npmjs.com/package/react', 'title': 'react 19.3.0 — npm',
                'text': 'A versão mais recente do pacote react no registro npm é 19.3.0 (consultada agora).'}
        answer = 'Com base nas fontes consultadas:\n\n- A versão mais recente do pacote react no registro npm é 19.3.0 (consultada agora). [web-1]'
        response = self.service.reply([{'role': 'user', 'content': 'Qual a versão mais nova do React?'},
                                       research_message([page], answer, True)], objective='research')
        self.assertEqual(response['backend'], 'research-evidence-fallback')
        self.assertEqual(response['agent']['status'], 'completed')
        self.assertIn('19.3.0', response['text'])
        self.assertIn('https://www.npmjs.com/package/react', response['text'])
        self.assertIn('citação literal', response['text'])

    def test_extractive_answer_quotes_matching_sentences_only(self):
        evidence = [{'source': 'https://docs.example.test/tokio', 'text': (
            'Menu Home Blog. Tokio é um runtime assíncrono para Rust com agendador multithread. '
            'Este parágrafo fala de outra coisa totalmente diferente e sem relação com a pergunta feita.')}]
        text = ModelService.extractive_research_answer('O que é o runtime Tokio para Rust?', {}, evidence)
        self.assertIn('Tokio é um runtime assíncrono para Rust', text)
        self.assertNotIn('outra coisa', text)
        self.assertIsNone(ModelService.extractive_research_answer('Qual a capital da França?', {}, evidence))


if __name__ == '__main__':
    unittest.main()
