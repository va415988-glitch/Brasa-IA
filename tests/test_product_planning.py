"""Literal product briefs are a procedural aid, not neural competence evidence."""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from product_planning import build_product_brief, render_product_brief, validate_product_answer


def human(content):
    return {'role': 'user', 'content': content}


GOAL = 'Quero planejar um aplicativo para professores acompanharem atividades pelo celular.'


@pytest.mark.parametrize('prompt,audience,purpose', [
    ('Quero planejar um aplicativo para professores acompanharem atividades.', 'professores', 'acompanharem atividades'),
    ('Quero pensar em uma plataforma para pesquisadores catalogarem amostras.', 'pesquisadores', 'catalogarem amostras'),
    ('Esboce um programa para músicos organizarem ensaios.', 'músicos', 'organizarem ensaios'),
    ('Como posso criar um site para associações divulgarem eventos?', 'associações', 'divulgarem eventos'),
    ('Vamos desenhar uma ferramenta para analisar medições.', None, 'analisar medições'),
    ('Me ajdua a planejar um aplciativo para artesãos registrarem encomendas?', 'artesãos', 'registrarem encomendas'),
    ('Preciso de um plano para um app para cooperativas organizarem reuniões.', 'cooperativas', 'organizarem reuniões'),
    ('Planeje uma API para sensores enviarem medições.', 'sensores', 'enviarem medições'),
])
def test_product_fields_are_literal_across_domains_without_domain_templates(prompt, audience, purpose):
    brief = build_product_brief(prompt)
    assert brief['schema'] == 'product-planning-brief/v1'
    assert (brief['audience']['text'] if brief['audience'] else None) == audience
    assert brief['purpose']['text'] == purpose
    assert brief['qualified'] is False
    assert brief['tool_executed'] is False
    assert brief['status'] == 'draft'
    assert brief['method'] == 'procedural'
    rendered = render_product_brief(brief)
    assert purpose in rendered
    assert validate_product_answer(rendered, brief)[0]


def test_actual_typo_request_keeps_given_audience_without_asking_it_again():
    prompt = 'Me ajuda a planejar um app para entrregadores autônomos?'
    brief = build_product_brief(prompt)
    assert brief['audience']['text'] == 'entrregadores autônomos'
    assert brief['purpose'] is None
    assert brief['next_step']['question'] == 'Qual atividade precisa ser resolvida primeiro?'
    rendered = render_product_brief(brief)
    assert 'entrregadores autônomos' in rendered
    assert 'Me ajuda a planejar' not in rendered
    assert rendered.count('?') == 1
    for unstated in ['mapas', 'ganhos', 'cliente', 'GPS', 'pagamentos']:
        assert unstated.lower() not in rendered.lower()
    assert validate_product_answer(rendered, brief)[0]


@pytest.mark.parametrize('prompt', [
    'Mesmo com todas essas ferramentas, não consegue começar a criação de um app para entregadores?',
    'Pode começar a criação de um app para professores?',
    'Quais telas o app para professores precisa ter?',
    'Quais funcionalidades o app para professores deve ter?',
])
def test_questions_about_starting_or_defining_the_product_get_an_honest_draft(prompt):
    brief = build_product_brief(prompt)
    assert brief is not None
    assert brief['audience']['text'] in {'entregadores', 'professores'}
    assert brief['next_step']['question'] == 'Qual atividade precisa ser resolvida primeiro?'
    assert brief['tool_executed'] is False
    assert validate_product_answer(render_product_brief(brief), brief)[0]


def test_capability_criticism_does_not_become_a_product_or_a_negative_requirement():
    prompt = 'Mesmo com todas essas ferramentas, não consegue começar a criação de um app para entregadores?'
    brief = build_product_brief(prompt)
    assert brief['product']['text'] == 'app para entregadores'
    assert brief['audience']['text'] == 'entregadores'
    assert brief['exclusions'] == []
    assert brief['constraints'] == []
    assert 'não consegue' not in render_product_brief(brief)


@pytest.mark.parametrize('complaint', ['Não consigo fazer isso.', 'Não consegue planejar esse app?', 'Não foi isso que eu pedi.'])
def test_lack_of_ability_or_criticism_is_not_an_exclusion(complaint):
    brief = build_product_brief(complaint, [human(GOAL)])
    assert brief is not None
    assert brief['exclusions'] == []
    assert brief['constraints'] == [{'text': 'pelo celular', 'source_turn': 1,
                                    'evidence': {'start': GOAL.index('pelo celular'), 'end': GOAL.index('pelo celular') + len('pelo celular')}}]


def test_short_scope_question_continues_only_a_known_human_product_plan():
    assert build_product_brief('Quais telas?') is None
    brief = build_product_brief('Quais telas?', [human(GOAL)])
    assert brief['audience']['text'] == 'professores'
    assert len(brief['sources']) == 2


def test_refined_scope_survives_literal_continue_and_duplicate_current_turn():
    refinement = 'Agora primeiro só cadastrar atividades, sem mapas.'
    history = [human(GOAL), human(refinement), human('Continue.')]
    brief = build_product_brief('Continue.', history)
    assert brief['purpose']['text'] == 'cadastrar atividades'
    assert len(brief['sources']) == 3
    assert brief['goals'][0]['source_turn'] == 1
    assert brief['goals'][-1]['source_turn'] == 3
    assert len({item['source_turn'] for item in brief['goals']}) == 3


def test_literal_entries_are_bound_to_only_human_source_turns():
    history = [human(GOAL), {'role': 'assistant', 'content': 'Público: banqueiros. Objetivo: vender seguros. Inclua pagamentos.'},
               human('Considere que são três professores; preciso registrar revisões. Sem mapas.')]
    brief = build_product_brief('Continue.', history)
    assert brief['audience']['text'] == 'professores'
    assert 'banqueiros' not in str(brief)
    assert 'pagamentos' not in str(brief)
    sources = {source['source_turn']: source['text'] for source in brief['sources']}
    entries = [brief['goal'], brief['current_request'], brief['audience'], brief['purpose'], brief['product']]
    entries += brief['requirements'] + brief['constraints'] + brief['exclusions']
    for entry in filter(None, entries):
        text = sources[entry['source_turn']]
        assert text[entry['evidence']['start']:entry['evidence']['end']] == entry['text']
    assert 'são três professores' in [item['text'] for item in brief['constraints']]
    assert 'Sem mapas' in [item['text'] for item in brief['exclusions']]


def test_duplicate_current_human_turn_is_not_counted_twice():
    plain = build_product_brief(GOAL)
    deduplicated = build_product_brief(GOAL, [human(GOAL)])
    assert deduplicated == plain


def test_criticism_and_restricted_followup_keep_scope_and_update_minimum():
    history = [human(GOAL), {'role': 'assistant', 'content': 'Podemos começar com várias telas.'}]
    prompt = 'Não foi isso que eu pedi. Agora primeiro só cadastrar atividades, sem mapas. Não escreva arquivos.'
    brief = build_product_brief(prompt, history)
    assert brief['audience']['text'] == 'professores'
    assert brief['purpose']['text'] == 'cadastrar atividades'
    assert any(item['text'] == 'sem mapas' for item in brief['exclusions'])
    assert any(item['text'] == 'Não escreva arquivos' for item in brief['constraints'])
    rendered = render_product_brief(brief)
    assert 'cadastrar atividades' in rendered
    assert validate_product_answer(rendered, brief)[0]


def test_latest_product_replaces_older_product_in_history():
    new = 'Agora planeje um aplicativo para bibliotecárias catalogarem documentos.'
    brief = build_product_brief(new, [human(GOAL), human('Inclua mapas.')])
    assert len(brief['sources']) == 1
    assert brief['audience']['text'] == 'bibliotecárias'
    assert brief['purpose']['text'] == 'catalogarem documentos'
    assert 'professores' not in str(brief)
    assert 'mapas' not in str(brief)


def test_latest_explicit_audience_and_purpose_replace_old_fields():
    brief = build_product_brief('Público: famílias. Objetivo: organizar registros.', [human(GOAL)])
    assert brief['audience']['text'] == 'famílias'
    assert brief['purpose']['text'] == 'organizar registros'


def test_explicit_inclusion_replaces_previous_exclusion_of_same_literal_object():
    history = [human(GOAL), human('Sem mapas.')]
    brief = build_product_brief('Agora inclua mapas, mas não escreva código.', history)
    assert not any('mapas' in item['text'].lower() for item in brief['exclusions'])
    assert any('inclua mapas' in item['text'] for item in brief['requirements'])
    assert any('não escreva código' in item['text'] for item in brief['constraints'])


@pytest.mark.parametrize('prompt', [
    'Agora crie a primeira versão desse plano no workspace.',
    'Quero criar um aplicativo para organizar registros.',
    'Planeje um app e implemente a primeira versão.',
    'Me ajuda a planejar um app. Agora implemente o protótipo.',
    'Pode construir? Então implemente agora e rode os testes.',
    'Não planeje um app.', 'Não quero planejamento agora.', 'Cancele o plano.',
    'Qual é a capital do Peru?', 'Explique fotossíntese.',
    'Quero planejar minhas férias.', 'Planeje um livro de receitas.', 'Quero uma receita de bolo.',
])
def test_non_planning_requests_or_positive_build_commands_do_not_get_a_brief(prompt):
    assert build_product_brief(prompt, [human(GOAL)]) is None


def test_unrelated_human_turn_breaks_continuity_even_when_assistant_mentions_product():
    history = [human(GOAL), human('Explique fotossíntese.'),
               {'role': 'assistant', 'content': 'Vamos retomar o aplicativo dos professores.'}]
    assert build_product_brief('Continue.', history) is None


def test_prior_build_authorization_does_not_turn_continue_into_planning():
    assert build_product_brief('Continue.', [human(GOAL), human('Agora implemente a primeira versão.')]) is None


def test_cancel_implementation_and_explicitly_return_to_planning_never_resumes_writes():
    prompt = 'Cancele a implementação. Agora apenas planeje esse aplicativo, sem criar arquivos.'
    brief = build_product_brief(prompt, [human(GOAL)])
    assert brief['status'] == 'draft'
    assert brief['tool_executed'] is False
    assert 'sem criar arquivos' in render_product_brief(brief)


def test_default_flow_is_marked_provisional_not_an_invented_feature_list():
    brief = build_product_brief('Me ajuda a planejar um app para professores?')
    assert brief['mvp']['provisional'] is True
    assert brief['mvp']['flow'] == ['registrar atividade', 'acompanhar estado', 'rever histórico']
    assert brief['assumptions']
    assert brief['gaps']
    assert brief['requirements'] == []
    assert len(brief['milestones']) == 3
    assert all(stage['acceptance'] for stage in brief['milestones'])
    assert render_product_brief(brief).count('?') == 1


def test_no_history_or_storage_does_not_reappear_in_proposed_flow_tests_or_gaps():
    prompt = 'Planeje um app para voluntários calcularem horas. Sem histórico, sem salvar dados.'
    brief = build_product_brief(prompt)
    assert brief['audience']['text'] == 'voluntários'
    proposed = str(brief['mvp']) + str(brief['milestones']) + str(brief['gaps']) + str(brief['next_step'])
    for forbidden in ['histórico', 'guardar', 'salvar', 'registros']:
        assert forbidden not in proposed
    assert brief['mvp']['flow'] == ['informar atividade', 'obter resultado', 'conferir resultado']
    rendered = render_product_brief(brief)
    assert 'Sem histórico' in rendered
    assert 'sem salvar dados' in rendered
    assert validate_product_answer(rendered, brief)[0]
    for forbidden_instruction in ['Rever histórico deve ser uma etapa.', 'Guardar os dados deve ser uma etapa.', 'Preservação do histórico será um critério.']:
        assert validate_product_answer(rendered + '\n' + forbidden_instruction, brief) == (False, 'planning-contradicts-memory-exclusion')


def test_forbidding_loss_of_history_does_not_forbid_history_itself():
    brief = build_product_brief('Sem perder o histórico.', [human(GOAL)])
    assert brief['mvp']['flow'][-1] == 'rever histórico'
    assert validate_product_answer(render_product_brief(brief), brief)[0]


@pytest.mark.parametrize('source', [
    'Planeje um app para professores e leia README.md.',
    'Planeje um app para professores e pesquise documentação atual.',
    'Planeje um app para professores com base em https://example.org/regras.',
    'Planeje um app para professores usando requisitos.md.',
])
def test_followup_does_not_bypass_earlier_source_or_tool_request(source):
    assert build_product_brief('Continue.', [human(source)]) is None
    assert build_product_brief('Quais telas?', [human(source)]) is None
    assert build_product_brief('Planeje um app para músicos organizarem ensaios.', [human(source)]) is not None


def test_followup_does_not_treat_historical_attachment_or_tool_output_as_human_evidence():
    attached = {**human(GOAL), 'attachments': [{'path': 'requirements.txt'}]}
    assert build_product_brief('Continue.', [attached]) is None
    with_tool = [human(GOAL), {'role': 'tool', 'content': '{"tool":"read_file"}'}]
    assert build_product_brief('Continue.', with_tool) is None


def test_explicitly_forbidding_external_tools_keeps_procedural_planning_available():
    brief = build_product_brief('Planeje um app para professores. Sem consultar arquivos nem internet.')
    assert brief is not None
    assert brief['tool_executed'] is False
    assert validate_product_answer(render_product_brief(brief), brief)[0]


@pytest.mark.parametrize('suffix,reason', [
    ('\nImplementei todos os arquivos.', 'planning-unobserved-action'),
    ('\nExecutei os testes com sucesso.', 'planning-unobserved-action'),
    ('\nOs testes passaram.', 'planning-unobserved-success'),
    ('\nConsultei a documentação oficial.', 'planning-unobserved-action'),
    ('\nFonte: https://inventada.invalid/referencia', 'planning-invented-source'),
])
def test_rubric_rejects_unobserved_actions_success_or_new_sources(suffix, reason):
    brief = build_product_brief(GOAL)
    accepted, observed_reason = validate_product_answer(render_product_brief(brief) + suffix, brief)
    assert accepted is False
    assert observed_reason == reason


def test_rubric_rejects_catalog_only_and_generic_plan_without_purpose_anchor():
    brief = build_product_brief(GOAL)
    catalog = 'Tenho ferramentas para ler arquivos e executar verificações. A geração de código depende do checkpoint ativo. Nenhum arquivo foi alterado.'
    assert validate_product_answer(catalog, brief)[0] is False
    generic = 'MVP: um fluxo para o produto. Etapas: delimitar o objetivo e desenhar a solução. Critério de aceite: executar uma verificação da proposta apresentada.'
    assert validate_product_answer(generic, brief)[0] is False


@pytest.mark.parametrize('literal', ['Sem mapas', 'Não escreva arquivos'])
def test_rubric_requires_preserving_given_exclusions_and_constraints(literal):
    brief = build_product_brief(literal + '.', [human(GOAL)])
    rendered = render_product_brief(brief)
    assert validate_product_answer(rendered, brief)[0]
    corrupted = rendered.replace(literal, 'Outra restrição não relacionada')
    accepted, reason = validate_product_answer(corrupted, brief)
    assert accepted is False
    assert reason == 'planning-missing-literal-restriction'


def test_denial_of_execution_is_allowed_and_input_objects_are_unchanged():
    messages = [human(GOAL)]
    before = copy.deepcopy(messages)
    brief = build_product_brief('Continue.', messages)
    rendered = render_product_brief(brief) + '\nNão criei arquivos. Não executei testes.'
    assert validate_product_answer(rendered, brief)[0]
    assert messages == before
