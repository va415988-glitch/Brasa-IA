import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from cognitive_actions import choose_action, router_response
from cognitive_dialogue import build_frame, usable_observation
from tool_registry import ToolRegistry
from model_server import ModelService


class OperationalRecoveryTests(unittest.TestCase):
    def frame(self, goal, rows=(), tools=('read_file', 'find_paths', 'research_web', 'search_web', 'open_page')):
        return build_frame([{'role': 'user', 'content': goal}] + [
            {'role': 'tool', 'content': json.dumps(row)} for row in rows],
            {'available_tools': list(tools)}, ToolRegistry())

    def action(self, frame):
        return choose_action(frame, {'label': 'direct', 'accepted': False})

    def test_explicit_file_overrides_uncertain_classifier(self):
        self.assertEqual(self.action(self.frame('Leia config.json'))['tool_call']['tool'], 'read_file')

    def test_node_js_is_not_a_local_filename(self):
        call = self.action(self.frame('Pesquise a documentação de Node.js'))['tool_call']
        self.assertEqual(call['tool'], 'research_web')

    def test_failed_research_uses_another_api(self):
        value = self.action(self.frame('Pesquise TaskGroup', [
            {'tool': 'research_web', 'ok': False, 'error': 'HTTP 503'}]))
        self.assertEqual(value['tool_call']['tool'], 'search_web')

    def test_search_opens_source_and_failed_page_advances(self):
        search = {'tool': 'search_web', 'ok': True, 'data': {'results': [
            {'url': 'https://example.org/a', 'snippet': 'Resumo'}, {'url': 'https://example.org/b'}]}}
        frame = self.frame('Pesquise TaskGroup', [search])
        self.assertEqual(self.action(frame)['tool_call']['arguments']['url'], 'https://example.org/a')
        frame = self.frame('Pesquise TaskGroup', [search, {'tool': 'open_page', 'ok': False,
                            'data': {'url': 'https://example.org/a'}, 'error': '403'}])
        self.assertEqual(self.action(frame)['tool_call']['arguments']['url'], 'https://example.org/b')

    def test_missing_file_is_located_before_answering(self):
        failed = {'tool': 'read_file', 'ok': False, 'error': 'arquivo ausente'}
        self.assertEqual(self.action(self.frame('Leia config.json', [failed]))['tool_call']['tool'], 'find_paths')
        found = {'tool': 'find_paths', 'ok': True, 'data': {
            'matches': [{'kind': 'file', 'path': 'nested/config.json'}], 'truncated': False}}
        value = self.action(self.frame('Leia config.json', [failed, found]))
        self.assertEqual(value['tool_call']['arguments']['path'], 'nested/config.json')
        found['data']['matches'].append({'kind': 'file', 'path': 'other/config.json'})
        self.assertEqual(self.action(self.frame('Leia config.json', [failed, found]))['decision'], 'blocked')

    def test_large_research_preserves_pages_and_urls(self):
        row = {'tool': 'research_web', 'ok': True, 'data': {'pages': [
            {'text': 'conteúdo ' * 1000, 'url': f'https://example.org/{i}'} for i in range(3)],
            'search_results': []}}
        frame = self.frame('Pesquise TaskGroup', [row])
        data = frame['observations'][0]['data']
        self.assertIn('pages', data)
        self.assertEqual(data['pages'][2]['url'], 'https://example.org/2')
        self.assertTrue(usable_observation(frame['observations'][0]))

    def test_prior_turn_evidence_does_not_answer_new_request(self):
        messages = [{'role': 'user', 'content': 'Leia antigo.md'}, {'role': 'tool', 'content': json.dumps({
            'tool': 'read_file', 'ok': True, 'data': {'content': 'antigo'}})},
            {'role': 'user', 'content': 'Leia novo.md'}]
        frame = build_frame(messages, {'available_tools': ['read_file']}, ToolRegistry())
        self.assertEqual(frame['observations'], [])
        self.assertEqual(self.action(frame)['tool_call']['arguments']['path'], 'novo.md')

    def test_evidence_answer_emits_complete_stream(self):
        messages = [{'role': 'user', 'content': 'Leia novo.md'}, {'role': 'tool', 'content': json.dumps({
            'tool': 'read_file', 'ok': True, 'data': {'path': 'novo.md', 'content': 'ação 🚀 ' * 100}})}]
        deltas = []
        response = router_response(SimpleNamespace(tools=ToolRegistry()), messages,
                                   {'available_tools': ['read_file']}, (None, None, {'sha256': 'test'}),
                                   on_delta=deltas.append)
        self.assertEqual(''.join(deltas), response['text'])
        self.assertGreater(len(deltas), 1)

    def test_skill_candidates_can_mix_service_apis_and_tools(self):
        service = ModelService('benchmark-only', trace_path=None)
        route = {'selected_skills': [{'id': 'research', 'label': 'Pesquisa'}],
                 'needs_clarification': False, 'confidence': .9, 'network_requested': True,
                 'api_candidates': [
                     {'id': 'service-api', 'kind': 'service-api'},
                     {'id': 'research.web', 'kind': 'tool', 'tool': 'research_web', 'network_policy': 'external'}]}
        call = {'tool': 'research_web', 'arguments': {'query': 'TaskGroup'}, 'planner': {'strategy': 'rank'}}
        with patch.object(service.skill_router, 'route', return_value=route), \
                patch.object(service.planner, 'plan', return_value=call):
            value = service.plan_tool('Compare fontes técnicas sobre concorrência')
        self.assertEqual(value['skill_routing']['selected_api']['id'], 'research.web')


if __name__ == '__main__':
    unittest.main()
