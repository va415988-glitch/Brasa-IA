import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from build_research import build_research_queries, research_evidence, should_research_build
from model_server import ModelService


class BuildWebResearchTests(unittest.TestCase):
    def setUp(self):
        self.service = ModelService('benchmark-only', trace_path=None)
        self.question = 'Crie uma ferramenta CLI em Zig para converter documentos de texto. Workspace: /home/victor/Projetos/Segredo Local'
        self.inspection = {'workspace': '/tmp/build-research-test', 'files': [], 'directories': [],
                           'manifests': [], 'entrypoints': [], 'test_files': []}
        self.messages = [{'role': 'user', 'content': self.question}, {'role': 'tool', 'content': json.dumps({
            'tool': 'inspect_project', 'ok': True, 'data': self.inspection,
        })}]

    def test_build_research_uses_auto_provider_without_brave_key(self):
        with patch.dict('os.environ', {'IA_LOCAL_BRAVE_SEARCH_API_KEY': ''}):
            research = self.service.reply(self.messages, objective='build').get('tool_call') or {}
        self.assertEqual(research.get('tool'), 'research_web')
        self.assertEqual(research['arguments']['provider'], 'auto')

    @patch.dict('os.environ', {'IA_LOCAL_BRAVE_SEARCH_API_KEY': 'chave-de-teste'})
    def test_build_searches_brave_then_grounds_proposal_in_opened_page(self):
        first = self.service.reply(self.messages, objective='build')
        research = first.get('tool_call') or {}
        self.assertEqual(research.get('tool'), 'research_web')
        self.assertEqual(research['arguments']['provider'], 'brave')
        self.assertFalse(research['arguments']['save_to_corpus'])
        self.assertIn('Zig', research['arguments']['query'])
        self.assertEqual(len(research['arguments']['queries']), 3)
        self.assertNotIn('/home/victor', research['arguments']['query'])
        self.messages.append({'role': 'tool', 'content': json.dumps({
            'tool': 'research_web', 'ok': True, 'data': {'query': research['arguments']['query'],
                'providers_used': ['brave-llm-context-api'], 'pages': [
                    {'title': 'Zig Documentation', 'url': 'https://ziglang.org/documentation/',
                     'text': 'Documented standard library and file APIs.'},
                ], 'grounded': True},
        })})
        plan = {'assumptions': ['Use Zig e a API padrão para ler e converter texto.'],
                'operations': [{'tool': 'create_file', 'arguments': {
                    'path': 'main.zig', 'content': 'pub fn main() void {}\n'}}]}
        captured = {}
        def answer(_messages, knowledge=None):
            captured['knowledge'] = knowledge
            return json.dumps(plan)
        with patch.object(self.service, 'local_reply', side_effect=answer):
            response = self.service.reply(self.messages, objective='build')
        self.assertEqual(response.get('tool_call', {}).get('tool'), 'apply_batch', response)
        self.assertTrue(response['tool_call']['requires_approval'])
        self.assertIn('https://ziglang.org/documentation/', captured['knowledge'])
        self.assertIn('Documented standard library', captured['knowledge'])
        self.assertIn('https://ziglang.org/documentation/', response['text'])
        self.assertEqual(response['agent']['research_sources'], ['https://ziglang.org/documentation/'])
        self.messages.append({'role': 'tool', 'content': json.dumps({
            'tool': 'apply_batch', 'ok': True, 'data': {'count': 1, 'operations': [
                {'tool': 'create_file', 'result': {'path': 'main.zig'}}]},
        })})
        check_call = self.service.reply(self.messages, objective='build').get('tool_call') or {}
        self.assertEqual(check_call.get('tool'), 'project_checks')
        self.messages.append({'role': 'tool', 'content': json.dumps({
            'tool': 'project_checks', 'ok': True, 'data': {
                'check': 'auto', 'command': 'zig test main.zig', 'executed': True,
                'passed': True, 'exit_code': 0, 'stdout': '1 test passed', 'stderr': ''},
        })})
        delivered = self.service.reply(self.messages, objective='build')
        self.assertEqual(delivered['agent']['status'], 'completed')
        self.assertIn('https://ziglang.org/documentation/', delivered['text'])

    def test_research_scope_and_provenance(self):
        self.assertTrue(should_research_build(self.question, [], recipe=None))
        self.assertFalse(should_research_build(self.question, [{'tool': 'research_web'}], recipe=None))
        self.assertFalse(should_research_build(self.question, [], recipe={'operations': []}))
        self.assertFalse(should_research_build('Crie sistema local sem internet', [], recipe=None))
        self.assertTrue(should_research_build('Crie uma aplicação em Phoenix.', [], recipe=None))
        self.assertFalse(should_research_build('Crie uma aplicação sem usar internet.', [], recipe=None))
        query = build_research_queries(self.question + ' ' + 'x' * 80, self.inspection)[0]
        self.assertLessEqual(len(query), 600)
        self.assertNotIn('/home/victor', query)
        self.assertNotIn('x' * 80, query)
        self.assertEqual(research_evidence([{'tool': 'research_web', 'ok': True,
            'data': {'pages': [{'title': 'Sem fonte', 'text': 'texto'},
                               {'title': 'Sem texto', 'url': 'https://example.test'}]}}]), [])

    def test_brave_results_are_not_persisted_without_storage_rights(self):
        question = 'Aprenda Zig e use fontes oficiais.'
        messages = [{'role': 'user', 'content': question}, {'role': 'tool', 'content': json.dumps({
            'tool': 'research_web', 'ok': True, 'data': {
                'providers_used': ['brave-llm-context-api'], 'query': 'Zig docs',
                'pages': [{'title': 'Zig Documentation', 'url': 'https://ziglang.org/documentation/',
                           'text': 'Documented Zig APIs.'}]},
        })}]
        for setting in ('false', 'true'):
            with self.subTest(storage_setting=setting), \
                 patch.dict('os.environ', {'IA_LOCAL_BRAVE_ALLOW_STORAGE': setting}), \
                 patch('model_server.learning.persist_pages') as persist:
                self.service.reply(messages, objective='learn')
                persist.assert_not_called()

    def test_brave_failure_is_actionable_and_never_claims_a_source(self):
        self.messages.append({'role': 'tool', 'content': json.dumps({
            'tool': 'research_web', 'ok': False,
            'error': 'a chave da API Brave não está configurada neste runtime',
        })})
        response = self.service.reply(self.messages, objective='build')
        self.assertEqual(response['agent']['status'], 'blocked')
        self.assertIn('chave da API Brave', response['text'])
        self.assertNotIn('Fontes consultadas', response['text'])
        self.assertFalse(response.get('tool_call'))


if __name__ == '__main__':
    unittest.main()
