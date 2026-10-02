import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

from dialogue import route_intent
from model_server import ModelService, assess_generation_quality


class ConversationQualityTests(unittest.TestCase):
    def setUp(self):
        self.service = ModelService('benchmark-only')

    def assert_natural(self, question, *history):
        response = self.service.reply([*history, {'role': 'user', 'content': question}])
        self.assertIn(response['backend'], {'local-conversation', 'local-conversation-rules'}, response)
        self.assertNotEqual(response['backend'], 'quality-gate')
        self.assertNotEqual(response['backend'], 'local-neural')
        self.assertTrue(response['text'].strip())
        self.assertNotIn('evidência suficiente', response['text'].lower())
        self.assertNotIn('não consegui concluir', response['text'].lower())
        return response

    def test_natural_variations_are_conversation(self):
        cases = (
            'Como você está hoje?',
            'Obrigado, ajudou bastante.',
            'Estou confuso com o que você respondeu. Pode explicar de outro jeito?',
            'Você consegue me ajudar a pensar em uma decisão difícil?',
            'Quero conversar um pouco.',
        )
        for question in cases:
            with self.subTest(question=question):
                self.assertEqual(route_intent(question), 'conversation')
                response = self.assert_natural(question)
                if question == 'Como você está hoje?':
                    self.assertIn('funcionando bem', response['text'])

    def test_followup_uses_previous_turn(self):
        response = self.assert_natural(
            'Como assim?',
            {'role': 'user', 'content': 'Estou tentando entender o plano.'},
            {'role': 'assistant', 'content': 'O primeiro passo é validar o objetivo antes de alterar arquivos.'},
        )
        self.assertIn('resposta anterior', response['text'])
        self.assertIn('validar o objetivo', response['text'])

    def test_study_routine_is_not_routed_to_web_research(self):
        question = 'Me ajude a pensar em uma rotina simples para estudar programação sem me sobrecarregar.'
        self.assertEqual(route_intent(question), 'planning')
        response = self.service.reply([{'role': 'user', 'content': question}])
        self.assertEqual(response['backend'], 'local-planning')
        self.assertIn('uma tarefa pequena', response['text'].lower())
        self.assertFalse(response.get('tool_call'))

    def test_explicit_learning_request_can_still_research(self):
        response = self.service.reply([{
            'role': 'user',
            'content': 'Estude SQLx por meio de uma rotina semanal.',
        }])
        self.assertEqual(response['backend'], 'proactive-learning')
        self.assertEqual((response.get('tool_call') or {}).get('tool'), 'research_web')

    def test_recommendation_followup_uses_previous_options(self):
        first_question = 'Quero organizar as tarefas da equipe. Liste duas opções simples.'
        first_response = self.service.reply([{'role': 'user', 'content': first_question}])
        self.assertEqual(first_response['backend'], 'local-planning')
        self.assertIn('quadro kanban', first_response['text'].lower())
        response = self.assert_natural(
            'Qual você recomenda para eu começar e por quê?',
            {'role': 'user', 'content': first_question},
            {'role': 'assistant', 'content': first_response['text']},
        )
        self.assertIn('quadro kanban', response['text'].lower())
        self.assertNotIn('**', response['text'])

    def test_recommendation_does_not_treat_blocked_turn_as_options(self):
        response = self.assert_natural(
            'Qual você recomenda para eu começar e por quê?',
            {'role': 'user', 'content': 'Quero organizar as tarefas da equipe.'},
            {'role': 'assistant', 'content': 'Não consegui formular uma resposta confiável. Liste duas opções simples, em uma frase cada.'},
        )
        self.assertIn('não cheguei a listar', response['text'].lower())
        self.assertIn('quadro kanban', response['text'].lower())
        self.assertNotIn('em uma frase cada', response['text'].lower())

    def test_proposal_only_request_does_not_return_executable_tool_call(self):
        response = self.service.reply([{
            'role': 'user',
            'content': 'Crie uma tela de login no workspace, mas não execute nem grave nada ainda; só proponha a ação.',
        }], routing_context={'workspace_selected': True})
        self.assertEqual(response['backend'], 'action-proposal')
        self.assertIsNone(response.get('tool_call'))
        self.assertFalse(response['execution_allowed'])
        self.assertEqual(response['proposal']['tool'], 'create_web_page')
        self.assertEqual(response['proposal']['arguments']['path'], 'preview/login.html')
        self.assertIn('Nenhum arquivo foi alterado', response['text'])

    def test_open_idea_discussion_stays_in_conversation_without_tools(self):
        cases = [
            ([{'role': 'user', 'content': 'Tenho pensado que a conversa livre deveria poder discordar de mim sem começar a executar ferramentas. O que acha dessa ideia?'}], ('discordância', 'autorização')),
            ([{'role': 'user', 'content': 'E se, antes de agir, o agente dissesse em uma frase o que entendeu e me deixasse corrigir?'}], ('hipótese', 'risco')),
            ([{'role': 'user', 'content': 'Me ajuda a explorar a ideia de ensinar o agente a planejar os testes antes de mexer no código.'}], ('testes', 'hipótese')),
            ([{'role': 'user', 'content': 'Quero conversar sobre como uma IA aprende a entender alguém. Não estou pedindo para mudar o código.'}], ('como uma ia aprende', 'qual aspecto')),
            [
                {'role': 'user', 'content': 'A ideia é separar conversa e ação, mas compartilhar o histórico.'},
                {'role': 'assistant', 'content': 'Isso pode separar decisões de execução sem perder a continuidade da conversa.'},
                {'role': 'user', 'content': 'Qual o principal risco nessa separação?'},
            ],
        ]
        cases[-1] = (cases[-1], ('principal risco', 'histórico'))
        for messages, expected_terms in cases:
            with self.subTest(question=messages[-1]['content']):
                response = self.service.reply(messages)
                self.assertEqual(response['intent'], 'conversation')
                self.assertNotIn(response['backend'], {'quality-gate', 'local-product-planning'})
                self.assertFalse(response.get('tool_call'))
                self.assertTrue(response['text'].strip())
                self.assertTrue(all(term in response['text'].lower() for term in expected_terms))

    def test_memory_question_uses_explicit_context(self):
        response = self.service.reply([
            {'role': 'user', 'content': 'Meu projeto se chama Aurora e prefiro uma interface simples.'},
            {'role': 'assistant', 'content': 'Entendi.'},
            {'role': 'user', 'content': 'Você lembra do que eu disse?'},
        ])
        self.assertEqual(response['backend'], 'session-memory')
        self.assertIn('Aurora', response['text'])
        self.assertIn('interface simples', response['text'])

    def test_task_followup_stays_conversational_before_execution(self):
        response = self.assert_natural(
            'Qual é o primeiro passo?',
            {'role': 'user', 'content': 'Quero criar um assistente pessoal no workspace.'},
            {'role': 'assistant', 'content': 'Podemos começar definindo o contexto.'},
        )
        self.assertIn('workspace', response['text'])
        self.assertIn('inspeciono', response['text'])

    def test_technical_greeting_remains_technical(self):
        self.assertEqual(route_intent('Olá, explique tuplas em Python.'), 'programming')

    def test_operational_goal_does_not_become_conversation(self):
        response = self.service.reply([{
            'role': 'user',
            'content': 'Quero criar um assistente pessoal e integrar com o sistema operacional do meu computador Workspace local: /tmp/assistente',
        }])
        self.assertEqual(response['intent'], 'workspace')
        self.assertEqual((response.get('tool_call') or {}).get('tool'), 'set_workspace')

    def test_corrupted_generation_is_rejected(self):
        valid, reason = assess_generation_quality('Resposta quebrada' + chr(8) + ' com controle', 'Como você está?')
        self.assertFalse(valid)
        self.assertEqual(reason, 'control-character')


if __name__ == '__main__':
    unittest.main()
