"""Routing uncertainty must not turn planning or dialogue into a file request."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from cognitive_actions import choose_action, consultation_intent, router_response, source_requested
from cognitive_dialogue import build_frame
from tool_registry import ToolRegistry


class ConversationRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tools = ToolRegistry()
        self.cognition = {'schema': 'agent-cognition/v1',
                          'available_tools': ['read_file', 'open_page', 'research_web', 'inspect_project']}
        self.bundle = (None, None, {'sha256': 'mock-selector'})

    def frame(self, goal, history=(), constraints=()):
        return build_frame(list(history) + [{'role': 'user', 'content': goal}],
                           {**self.cognition, 'constraints': list(constraints)}, self.tools)

    def service(self):
        return SimpleNamespace(
            tools=self.tools,
            _reply=Mock(return_value={'text': 'mock dialogue output', 'backend': 'test-dialogue'}),
            _agent=Mock(return_value={'status': 'blocked'}),
            # A planning skill's read candidate used to override even direct dialogue.
            plan_tool=Mock(return_value={'tool': 'read_file', 'arguments': {'path': 'README.md'},
                                        'skill_routing': {'selected_skills': [{'id': 'implementation'}]}}),
        )

    def routed(self, goal, prediction, history=(), constraints=()):
        service = self.service()
        messages = list(history) + [{'role': 'user', 'content': goal}]
        cognition = {**self.cognition, 'constraints': list(constraints)}
        with patch('cognitive_actions.predict_router', return_value=prediction):
            result = router_response(service, messages, cognition, self.bundle)
        return service, messages, result

    def assert_dialogue(self, goal, prediction, history=()):
        service, messages, result = self.routed(goal, prediction, history)
        service._reply.assert_called_once_with(messages, objective='conversation', cognition=None, on_delta=None)
        service.plan_tool.assert_not_called()
        self.assertNotIn('tool_call', result)
        self.assertEqual(result['backend'], 'test-dialogue')

    def test_uncertain_planning_returns_to_existing_dialogue(self):
        goals = ['Me ajuda a planejar um app para entrregadores autônomos?',
                 'Ajude a definir requisitos e escopo de um app sem escrever código.',
                 'Quero discutir uma proposta de projeto para minha equipe.',
                 'Me ajude a organizar as prioridades de uma associação.']
        for goal in goals:
            for label in ['direct', 'local', 'web', 'clarify']:
                with self.subTest(goal=goal, label=label):
                    self.assert_dialogue(goal, {'label': label, 'score': .45, 'accepted': False})

    def test_confident_local_or_web_cannot_authorize_unrequested_consultation(self):
        for label in ['local', 'web']:
            with self.subTest(label=label):
                self.assert_dialogue('Planeje um aplicativo para prestadores autônomos.',
                                     {'label': label, 'score': .99, 'accepted': True})

    def test_long_critique_keeps_history_and_does_not_request_a_source(self):
        history = [{'role': 'user', 'content': 'Planeje um app para entregadores.'},
                   {'role': 'assistant', 'content': 'Ainda precisamos definir a tarefa principal.'}]
        self.assert_dialogue(
            'Mesmo com todas essas ferramentas, não consegue começar a criação de um app para entregadores?',
            {'label': 'direct', 'score': .61, 'accepted': False}, history,
        )

    def test_uncertain_explicit_file_still_uses_the_named_file(self):
        service, _, result = self.routed('Leia config.json.', {'label': 'direct', 'accepted': False})
        self.assertEqual(result['tool_call']['tool'], 'read_file')
        self.assertEqual(result['tool_call']['arguments']['path'], 'config.json')
        service.plan_tool.assert_not_called()
        service._reply.assert_not_called()

    def test_uncertain_explicit_url_still_uses_the_named_source(self):
        service, _, result = self.routed('Consulte https://example.org/docs.',
                                         {'label': 'local', 'accepted': False})
        self.assertEqual(result['tool_call']['tool'], 'open_page')
        self.assertEqual(result['tool_call']['arguments']['url'], 'https://example.org/docs')
        service.plan_tool.assert_not_called()

    def test_current_factual_questions_can_consult_the_web(self):
        for goal in ['Qual é a versão atual do Python?', 'Qual é o preço do dólar hoje?',
                     'Quem é o presidente atual dessa organização?']:
            with self.subTest(goal=goal):
                service, _, result = self.routed(goal, {'label': 'direct', 'accepted': False})
                self.assertEqual(result['tool_call']['tool'], 'research_web')
                self.assertEqual(result['tool_call']['arguments']['query'], goal)
                service._reply.assert_not_called()
                service.plan_tool.assert_not_called()

    def test_negated_web_does_not_allow_a_planner_bypass(self):
        for goal, constraints in [('Pesquise Python sem acessar a internet.', ()),
                                  ('Qual é a versão atual do Python?', ('Não consulte a web.',)),
                                  ('Qual é a versão atual do Python?', ({'text': 'Não consulte a web.',
                                                                       'source': 'user', 'mandatory': True},)),
                                  ('Consulte https://example.org/docs sem usar a web.', ())]:
            with self.subTest(goal=goal):
                service, _, result = self.routed(goal, {'label': 'web', 'accepted': True},
                                                 constraints=constraints)
                self.assertEqual(result['cognition']['decision'], 'blocked')
                self.assertIsNone(result.get('tool_call'))
                service.plan_tool.assert_not_called()
                service._reply.assert_not_called()

    def test_unnamed_file_keeps_its_guard_and_does_not_guess_readme(self):
        service, _, result = self.routed('Leia o arquivo do projeto.', {'label': 'local', 'accepted': True})
        self.assertEqual(result['cognition']['decision'], 'blocked')
        self.assertIn('Qual arquivo', result['text'])
        self.assertIsNone(result['tool_call'])
        service.plan_tool.assert_not_called()
        service._reply.assert_not_called()

    def test_bare_reference_can_clarify_without_presuming_a_file(self):
        frame = self.frame('Qual deles?')
        result = choose_action(frame, {'label': 'clarify', 'accepted': False})
        self.assertEqual(result['decision'], 'blocked')
        self.assertIn('referente', result['gap'])
        self.assertNotIn('arquivo', result['text'])

    def test_reference_with_prior_options_returns_to_contextual_dialogue(self):
        history = [{'role': 'user', 'content': 'Quero comparar duas formas de começar.'},
                   {'role': 'assistant', 'content': 'Há duas opções: um protótipo ou um levantamento de requisitos.'}]
        self.assert_dialogue('Qual deles?', {'label': 'clarify', 'score': .99, 'accepted': True}, history)

    def test_planning_mentions_without_a_source_do_not_become_searches(self):
        self.assertIsNone(consultation_intent(self.frame('Escreva a documentação do app.'))['kind'])
        self.assertIsNone(consultation_intent(self.frame('Planeje um app que mostre cotações atuais.'))['kind'])
        self.assertIsNone(consultation_intent(self.frame('Me ajude a planejar um app de preços atuais?'))['kind'])

    def test_public_guard_distinguishes_positive_reads_and_negated_planning(self):
        for goal in ['Não pesquise nem leia arquivos, só planeje um app.',
                     'Não quero que você pesquise na internet; apenas defina os requisitos.',
                     'Sem pesquisar na web, ajude a organizar as prioridades.',
                     'Planeje um app para acompanhar preços atuais.',
                     'Qual nome usar num app de preços atuais?',
                     'Me ajude a planejar um app de preços atuais?']:
            with self.subTest(goal=goal):
                self.assertFalse(source_requested(goal))
        for goal in ['Pesquise os preços atuais sem usar a web.',
                     'Qual é a versão atual do Python?',
                     'Leia os arquivos do projeto.',
                     'Não altere os arquivos, mas inspecione o projeto.',
                     'Não pesquise na web; leia os arquivos do projeto.']:
            with self.subTest(goal=goal):
                self.assertTrue(source_requested(goal))

    def test_negated_consultation_and_positive_planning_keep_dialogue(self):
        self.assert_dialogue('Não pesquise nem leia arquivos, só planeje um app.',
                             {'label': 'web', 'accepted': False})

    def test_unavailable_reader_is_not_bypassed_by_a_different_skill(self):
        service, _, result = self.routed('Leia manual.pdf.', {'label': 'direct', 'accepted': False})
        self.assertEqual(result['cognition']['decision'], 'blocked')
        self.assertIsNone(result['tool_call'])
        service.plan_tool.assert_not_called()

    def test_explicit_project_consultation_can_use_a_read_skill(self):
        service = self.service()
        service.plan_tool.return_value = {
            'tool': 'inspect_project', 'arguments': {},
            'skill_routing': {'selected_skills': [{'id': 'workspace-inspection'}]},
        }
        messages = [{'role': 'user', 'content': 'Inspecione o projeto.'}]
        with patch('cognitive_actions.predict_router', return_value={'label': 'local', 'accepted': False}):
            result = router_response(service, messages, self.cognition, self.bundle)
        self.assertEqual(result['tool_call']['tool'], 'inspect_project')
        service.plan_tool.assert_called_once()
        service._reply.assert_not_called()

    def test_conversation_cannot_be_salvaged_as_implementation(self):
        service = self.service()
        service.plan_tool.return_value['tool'] = 'inspect_project'
        service.plan_tool.return_value['arguments'] = {}
        messages = [{'role': 'user', 'content': 'Inspecione o projeto.'}]
        with patch('cognitive_actions.predict_router', return_value={'label': 'local', 'accepted': False}):
            result = router_response(service, messages, self.cognition, self.bundle)
        self.assertEqual(result['cognition']['decision'], 'blocked')
        self.assertIsNone(result['tool_call'])


if __name__ == '__main__':
    unittest.main()
