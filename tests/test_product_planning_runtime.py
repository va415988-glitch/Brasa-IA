"""Planning must reach a useful draft through the real conversation entry point."""
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from model_server import ModelService
from product_planning import build_product_brief, render_product_brief


def fixture(reply=None):
    service = ModelService.__new__(ModelService)
    service.dialogue_turn = Mock(return_value=reply or {
        'ok': False, 'backend': 'quality-gate', 'text': '',
        'generation': {'quality_gate_result': 'rejected', 'attempts': [
            {'provider': 'project-neural-checkpoint', 'status': 'rejected', 'reason': 'not-relevant'}]},
    })
    service.cognitive_conversation = Mock(side_effect=AssertionError('A planning draft needs no source selection.'))
    service.local_reply = Mock(side_effect=AssertionError('Use the single bounded dialogue attempt.'))
    return service


@pytest.mark.parametrize('prompt', [
    'Me ajuda a planejar um app para entrregadores autônomos?',
    'Mesmo com todas essas ferramentas, não consegue começar a criação de um app para entregadores?',
    'Como posso criar um aplicativo para professores registrarem aulas?',
])
def test_user_reproductions_complete_draft_without_tools_or_false_competence(prompt):
    service = fixture()
    result = service._reply([{'role': 'user', 'content': prompt}], objective='conversation',
                            cognition={'schema': 'agent-cognition/v1', 'available_tools': ['read_file', 'search_web']})
    assert result['backend'] == 'local-product-planning'
    assert result['agent']['status'] == 'completed'
    assert result['agent']['verified'] is False
    assert result['agent']['verification_scope'] == 'planning-draft-only'
    assert result['planning']['qualified'] is False
    assert result['planning']['status'] == 'draft'
    assert result['planning']['neural_output_accepted'] is False
    assert result['tool_call'] is None
    assert result['tool_executed'] is result['execution_allowed'] is False
    assert result['generation']['attempts'][0]['reason'] == 'not-relevant'
    assert 'MVP' in result['text'] and 'Critério' in result['text']
    service.dialogue_turn.assert_called_once()
    assert service.dialogue_turn.call_args.kwargs['on_delta'] is None
    service.cognitive_conversation.assert_not_called()


def test_latest_human_requirements_survive_assistant_claim_and_continue():
    messages = [
        {'role': 'user', 'content': 'Planeje um aplicativo para professores registrarem aulas.'},
        {'role': 'assistant', 'content': 'Criei arquivos, testei tudo e vou incluir pagamentos.'},
        {'role': 'user', 'content': 'Sem pagamentos. Primeiro só registrar presença pelo celular.'},
        {'role': 'assistant', 'content': 'Vou implementar agora.'},
        {'role': 'user', 'content': 'Continue.'},
    ]
    result = fixture()._reply(messages, objective='conversation')
    assert result['planning']['audience']['text'] == 'professores'
    assert 'Sem pagamentos' in result['text']
    assert 'registrar presença pelo celular' in result['text']
    assert result['planning']['tool_executed'] is False
    assert not any('Criei arquivos' in item['text'] for item in result['planning']['sources'])


@pytest.mark.parametrize('candidate', [
    'Posso listar ferramentas como read_file e search_web para construir qualquer projeto.',
    'Criei e testei o MVP do aplicativo para entregadores. Etapas e critérios de aceite concluídos.',
    'Planejamento do MVP para entregadores. Fluxo e critérios: consulte https://inventada.example/plano.',
])
def test_generated_inventory_and_unobserved_success_cannot_replace_draft(candidate):
    result = fixture({'ok': True, 'backend': 'local-neural', 'text': candidate,
                      'generation': {'quality_gate_result': 'accepted'}})._reply(
        [{'role': 'user', 'content': 'Planeje um app para entregadores autônomos.'}], objective='conversation')
    assert result['backend'] == 'local-product-planning'
    assert result['generation']['planning_quality']['accepted'] is False
    assert result['planning_attempt']['candidate_text'] == candidate
    assert candidate != result['text']


def test_accepted_candidate_is_buffered_and_still_not_a_competence_certificate():
    prompt = 'Planeje um app para professores registrarem aulas.'
    # The generator is a fixture here; this test checks transport/rubric, not a model.
    answer = render_product_brief(build_product_brief(prompt))
    service = fixture({'ok': True, 'backend': 'local-neural', 'text': answer,
                       'generation': {'quality_gate_result': 'accepted'},
                       'dialogue': {'provider': 'project-neural-checkpoint', 'model': 'test-fixture'}})
    chunks = []
    result = service._reply([{'role': 'user', 'content': prompt}], objective='conversation', on_delta=chunks.append)
    assert ''.join(chunks) == answer
    assert result['planning']['method'] == 'checkpoint'
    assert result['planning']['neural_output_accepted'] is True
    assert result['planning']['qualified'] is False
    assert result['agent']['verified'] is False
    assert result['tool_call'] is None
    assert service.dialogue_turn.call_args.kwargs['on_delta'] is None


@pytest.mark.parametrize('prompt', [
    'Leia briefing.md e planeje um app para professores.',
    'Planeje um app com a documentação de https://example.org/manual.',
    'Pesquise concorrentes atuais e planeje um app para professores.',
    'Agora crie os arquivos do app para professores.',
    'Não quero planejar um app. O que é um token?',
])
def test_read_requests_implementation_and_cancel_keep_their_existing_routes(prompt):
    service = fixture()
    assert service.product_planning_reply([{'role': 'user', 'content': prompt}]) is None
    service.dialogue_turn.assert_not_called()


def test_observations_and_attachments_cannot_be_disguised_as_source_free_planning():
    service = fixture()
    assert service.product_planning_reply([
        {'role': 'user', 'content': 'Planeje um app para professores.'},
        {'role': 'tool', 'content': '{"tool":"read_file","ok":true,"data":{"content":"brief"}}'},
    ]) is None
    assert service.product_planning_reply([
        {'role': 'user', 'content': 'Planeje um app para professores.', 'attachments': [{'path': 'brief.md'}]},
    ]) is None


def test_followup_does_not_use_legacy_goal_restoration_across_newer_planning():
    messages = [
        {'role': 'user', 'content': 'Crie um jogo de corrida.'},
        {'role': 'assistant', 'content': 'Vou criar.'},
        {'role': 'user', 'content': 'Agora só planeje um app para médicos agendarem consultas.'},
        {'role': 'user', 'content': 'Continue.'},
    ]
    result = fixture().product_planning_reply(messages)
    assert result is not None
    assert result['planning']['audience']['text'] == 'médicos'
    assert 'corrida' not in result['text']


def test_generated_workspace_suffix_is_not_a_literal_user_requirement():
    prompt = 'Planeje um app para professores registrarem aulas.'
    result = fixture().product_planning_reply([{'role': 'user', 'content': prompt + '\n\nWorkspace local: /tmp/incidental'}])
    assert result is not None
    assert result['planning']['sources'] == [{'text': prompt, 'source_turn': 1}]


def test_negated_source_requests_are_preserved_as_constraints_without_querying():
    prompt = 'Não pesquise nem leia arquivos; só planeje um app para professores registrarem aulas.'
    service = fixture()
    result = service.product_planning_reply([{'role': 'user', 'content': prompt}])
    assert result is not None
    assert 'Não pesquise nem leia arquivos' in result['text']
    assert result['tool_call'] is None
    service.dialogue_turn.assert_called_once()


def test_dialogue_summary_retains_current_decoder_failure_and_clears_stale_metadata():
    service = ModelService.__new__(ModelService)
    service.last_generation = {'input_tokens': 999, 'quality_gate_result': 'accepted'}
    service.dialogue_api = Mock()

    def current_failure(*args, **kwargs):
        assert service.last_generation is None
        service.last_generation = {'quality_gate_result': 'error', 'error_type': 'RuntimeError',
                                   'error': 'Falha de inferência da tentativa atual.'}
        return {'ok': False, 'backend': 'quality-gate', 'generation': {'attempts': []}}

    service.dialogue_api.turn.side_effect = current_failure
    body = {'schema': 'agent-dialogue-request/v1',
            'messages': [{'role': 'user', 'content': 'Planeje um app para entregadores.'}], 'evidence': []}
    result = ModelService.dialogue_turn(service, body)
    assert result['generation']['checkpoint_attempt']['error_type'] == 'RuntimeError'
    assert 'input_tokens' not in result['generation']['checkpoint_attempt']
    assert service.last_generation['checkpoint_attempt']['error'] == 'Falha de inferência da tentativa atual.'
