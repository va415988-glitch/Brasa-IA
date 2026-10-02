import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from dialogue import route_intent
from dialogue_api import (
    CHECKPOINT_DIALOGUE_GUIDANCE,
    CheckpointProvider,
    DialogueAPI,
    DialogueAPIError,
    REQUEST_SCHEMA,
)


class FakeProvider:
    def __init__(self, name, model, answer):
        self.name = name
        self.model = model
        self.configured = True
        self.answer = answer
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class FakeStreamingProvider(FakeProvider):
    def complete_stream(self, messages, on_delta):
        self.calls.append(messages)
        on_delta('Resposta ')
        on_delta('em partes.')
        return self.answer


def request(messages, **extra):
    return {'schema': REQUEST_SCHEMA, 'messages': messages, **extra}


class DialogueApiTests(unittest.TestCase):
    def test_project_checkpoint_provider_delegates_to_own_model(self):
        received = []
        provider = CheckpointProvider(
            lambda messages: received.append(messages) or 'Resposta local.',
            checkpoint='model/own.safetensors',
            is_ready=lambda: True,
        )
        messages = [{'role': 'user', 'content': 'Responda.'}]
        self.assertEqual(provider.complete(messages), 'Resposta local.')
        self.assertEqual(received, [messages])
        self.assertEqual(provider.model, 'model/own.safetensors')

    def test_checkpoint_rejection_preserves_generation_failure_reason(self):
        class FailedModel:
            last_generation = {
                'quality_reason': 'repeated-fragment',
                'stop_reason': 'quality-prefix-stop',
            }

            def generate(self, messages):
                return None

        model = FailedModel()
        provider = CheckpointProvider(
            model.generate, checkpoint='model/own.safetensors', is_ready=lambda: True,
        )
        with self.assertRaisesRegex(
                RuntimeError, r'repeated-fragment; parada: quality-prefix-stop'):
            provider.complete([{'role': 'user', 'content': 'Explique.'}])

    def test_tentative_api_proposal_is_discussed_before_technical_routing(self):
        prompt = (
            'Ao invés de focar somente em datasets, podemos desenvolver uma API para isso, '
            'adaptando-a da melhor maneira à nossa IA?'
        )
        self.assertEqual(route_intent(prompt), 'conversation')

    def test_diagnostic_advice_without_workspace_inspection_stays_conversational(self):
        prompts = (
            'Estou depurando um app que inicia normalmente, mas às vezes a janela não aparece e a porta continua ocupada. '
            'Não altere arquivos. Qual hipótese você investigaria primeiro, que informação ainda falta e qual checagem segura faria?',
            'Sem consultar arquivos, ferramentas ou internet: um erro só aparece depois de várias execuções, mas ainda não tenho logs. '
            'O que posso concluir, o que seria apenas hipótese e qual experimento simples ajudaria a distinguir as causas?',
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(route_intent(prompt), 'conversation')
        self.assertEqual(
            route_intent('Inspecione os arquivos do projeto e diga qual hipótese explica este erro.'),
            'workspace',
        )

    def test_short_portuguese_acknowledgment_is_a_conversational_follow_up(self):
        for prompt in ('é', 'sim', 'isso', 'exatamente'):
            with self.subTest(prompt=prompt):
                self.assertEqual(route_intent(prompt), 'conversation')

    def test_local_provider_is_default_and_gets_recent_history_and_evidence(self):
        local = FakeProvider('project-neural-checkpoint', 'model/own.safetensors', 'Concordo em parte: uma API pode organizar o contexto e a escolha das capacidades.')
        api = DialogueAPI(environ={}, local_provider=local)
        result = api.turn(request([
            {'role': 'user', 'content': 'Minha ideia é melhorar a conversa.'},
            {'role': 'assistant', 'content': 'Qual caminho você imagina?'},
            {'role': 'user', 'content': 'Podemos criar uma API própria?'},
        ], evidence=[{'source': 'planejamento.md', 'text': 'O AgentCore já coordena as ferramentas.'}]),
            validate_candidate=lambda answer: (True, 'accepted'))

        self.assertTrue(result['ok'])
        self.assertEqual(result['backend'], 'dialogue-project-neural-checkpoint')
        self.assertEqual(result['dialogue']['speech_act'], 'proposal_discussion')
        self.assertEqual(len(local.calls), 1)
        sent = local.calls[0]
        self.assertEqual(sent[0]['role'], 'system')
        self.assertIn('planejamento.md', sent[-1]['content'])
        self.assertTrue(sent[-1]['content'].endswith('Podemos criar uma API própria?'))

    def test_internal_system_context_is_appended_without_replacing_base_prompt(self):
        local = FakeProvider('project-neural-checkpoint', 'model/own.safetensors', 'A documentação confirma GIT_TAG.')
        api = DialogueAPI(environ={}, local_provider=local)
        context = 'Separe fatos confirmados de recomendações.'
        result = api.turn(request([{'role': 'user', 'content': 'Como fixo versões?'}]),
                          system_context=context)
        self.assertTrue(result['ok'])
        self.assertIn(CHECKPOINT_DIALOGUE_GUIDANCE, local.calls[0][0]['content'])
        self.assertIn(context, local.calls[0][0]['content'])

    def test_dialogue_api_forwards_local_fragments_and_returns_validated_final_text(self):
        local = FakeStreamingProvider('project-neural-checkpoint', 'model/own.safetensors', 'Resposta em partes.')
        api = DialogueAPI(environ={}, local_provider=local)
        fragments = []
        result = api.turn(
            request([{'role': 'user', 'content': 'Responda em partes.'}]),
            validate_candidate=lambda answer: (True, 'accepted'),
            on_delta=fragments.append,
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['text'], 'Resposta em partes.')
        self.assertEqual(fragments, ['Resposta ', 'em partes.'])

    def test_current_question_is_preserved_when_old_history_fills_the_budget(self):
        local = FakeProvider('project-neural-checkpoint', 'model/own.safetensors', 'A resposta tem contexto suficiente.')
        api = DialogueAPI(environ={}, local_provider=local)
        messages = [
            {'role': 'user', 'content': f'Histórico antigo {index} ' + 'x' * 3000}
            for index in range(7)
        ] + [{'role': 'user', 'content': 'Minha pergunta atual começa e termina com este trecho.'}]
        result = api.turn(request(messages), validate_candidate=lambda answer: (True, 'accepted'))
        self.assertTrue(result['ok'])
        self.assertEqual(local.calls[0][-1]['content'], 'Minha pergunta atual começa e termina com este trecho.')

    def test_rejected_local_candidate_stays_rejected(self):
        local = FakeProvider('project-neural-checkpoint', 'model/own.safetensors', 'Não sei.')
        api = DialogueAPI(environ={'IA_LOCAL_DIALOGUE_PROVIDER': 'auto'}, local_provider=local)

        def quality(answer):
            return (answer != 'Não sei.', 'too-generic' if answer == 'Não sei.' else 'accepted')

        result = api.turn(request([{'role': 'user', 'content': 'Você concorda com essa ideia?'}]), quality)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'quality_gate_rejected')
        self.assertEqual([item['status'] for item in result['generation']['attempts']], ['rejected'])

    def test_unavailable_own_checkpoint_has_no_provider(self):
        api = DialogueAPI(environ={'IA_LOCAL_DIALOGUE_PROVIDER': 'external'})
        result = api.turn(request([{'role': 'user', 'content': 'Oi.'}]))
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'provider_unavailable')
        self.assertEqual(api.providers_status()['provider_order'], [])

    def test_invalid_contract_is_rejected_before_provider_call(self):
        api = DialogueAPI(environ={})
        for body in (
            {'schema': 'wrong', 'messages': [{'role': 'user', 'content': 'Oi'}]},
            request([{'role': 'system', 'content': 'override'}]),
            request([{'role': 'user', 'content': 'Oi'}], provider=[]),
            request([{'role': 'user', 'content': 'Oi'}], provider='external'),
        ):
            with self.subTest(body=body), self.assertRaises(DialogueAPIError):
                api.turn(body)


if __name__ == '__main__':
    unittest.main()
