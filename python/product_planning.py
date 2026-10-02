"""Procedural software planning from literal human requirements.

This is a bounded conversational aid, not a learned specialist or a competence
certificate. It neither generates code nor reads files nor executes tools.
"""
from __future__ import annotations

import re
import unicodedata

SCHEMA = 'product-planning-brief/v1'
PRODUCT = r'(?:apps?|aplicativos?|aplciativos?|sistemas?|plataformas?|portais?|sites?|apis?|programas?|ferramentas?|software|produto(?:\s+(?:digital|web)))'
PLAN = r'(?:planej\w*|plano|esboc\w*|esbo[cç]\w*|pensar|desenhar|projet\w*|arquitetura|fluxo|discutir|ideia)'
BUILD = r'(?:crie|criar|implemente|implementar|construa|construir|desenvolva|desenvolver|edite|editar|escreva|escrever|execute|executar|rode|rodar|publique|publicar)'
STOP = {'a', 'o', 'as', 'os', 'de', 'do', 'da', 'dos', 'das', 'e', 'em', 'no', 'na', 'nos', 'nas',
        'um', 'uma', 'uns', 'umas', 'para', 'por', 'com', 'que', 'quem', 'se', 'eu', 'me', 'minha',
        'meu', 'isso', 'esse', 'essa', 'este', 'esta', 'voce', 'quero', 'preciso', 'planejar', 'planeje',
        'plano', 'planejamento', 'app', 'aplicativo', 'aplciativo', 'sistema', 'software', 'produto',
        'digital', 'web', 'apenas', 'somente', 'so', 'agora', 'antes', 'primeiro', 'inclua', 'incluir',
        'adicione', 'adicionar', 'nao', 'sem', 'nem', 'mude', 'mudar', 'deve', 'deveria'}


def _normal(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if not unicodedata.combining(c))


def _tokens(text):
    return {word for word in re.findall(r'\b\w+\b', _normal(text)) if word not in STOP and len(word) > 2}


def _entry(turn, start=0, end=None):
    text = turn['text']
    end = len(text) if end is None else end
    while start < end and text[start].isspace(): start += 1
    while end > start and text[end - 1].isspace(): end -= 1
    return {'text': text[start:end], 'source_turn': turn['source_turn'],
            'evidence': {'start': start, 'end': end}}


def _negated(text, start):
    prefix = _normal(text[max(0, start - 90):start])
    prefix = re.split(r'[.!?;\n]|\b(?:mas|porem|entao)\b', prefix)[-1]
    return bool(re.search(r'\b(?:nao|nunca|sem)\b(?:\s+\w+){0,6}\s*$', prefix))


def _build_requested(text):
    normal = _normal(text)
    start = re.search(r'\b(?:comece|inicie)\s+(?:a\s+)?(?:criacao|construcao|implementacao|desenvolvimento)\b', normal)
    if start and not _negated(text, start.start()): return True
    for found in re.finditer(r'\b' + BUILD + r'\b', normal):
        if _negated(text, found.start()): continue
        prefix = normal[max(0, found.start() - 100):found.start()]
        clause = re.split(r'[.!?;\n]', prefix)[-1]
        imperative = found.group() in {'crie', 'implemente', 'construa', 'desenvolva', 'edite', 'escreva', 'execute', 'rode', 'publique'}
        if imperative and not re.search(r'\b(?:como|poderia|consegue|capacidade|capaz|sabe)\b', clause):
            return True
        # Infinitives in a plan, a hypothetical or a how-to question are not
        # a new instruction to implement that plan.
        if re.search(r'\b' + PLAN + r'\b|\b(?:como|poderia|consegue|capacidade|capaz|sabe|pensando|antes de)\b', clause):
            continue
        if re.search(r'\b(?:quero|preciso|vamos|agora|comece|comecar)\b', clause) or not clause.strip():
            return True
    return bool(re.search(r'\b(?:faca|monte)\b.{0,80}\b(?:primeira versao|prototipo|arquivos?|codigo)\b', normal)
                and not re.search(r'\b' + PLAN + r'\b', normal)
                and not re.search(r'\b(?:nao|sem)\b.{0,40}\b(?:faca|monte)\b', normal))


def _planning(text):
    normal = _normal(text)
    return bool(re.search(r'\b' + PLAN + r'\b', normal)
                or re.search(r'\b(?:me (?:ajud\w*|ajdua)|ajude-me|ajuda|como (?:posso |poderia |eu )?(?:criar|comecar)|por onde comecar)\b', normal)
                or (re.search(r'\bcomecar\b.{0,40}\b(?:criacao|construcao|desenvolvimento|implementacao)\b', normal)
                    and re.search(r'\?|\b(?:consegue|pode|poderia|como)\b', normal))
                or re.match(r'^\s*(?:quais|qual|que)\s+(?:(?:a|as|o|os)\s+)?(?:telas?|fluxos?|funcionalidades?|requisitos?|arquitetura|etapas?|dados)\b', normal))


def _other_planning_topic(text):
    """A fresh non-software object must not inherit an older app objective."""
    normal = _normal(text)
    if re.search(r'\b' + PRODUCT + r'\b|\b(?:isso|esse|essa|este|esta)\b', normal): return False
    target = re.search(r'\b(?:planej\w*|esboc\w*|desenhar|pensar em|quero|preciso de)\s+(?:um|uma|meu|minha|meus|minhas|o|a|os|as)\s+(\w+)', normal)
    return bool(target and target.group(1) not in {'fluxo', 'fluxos', 'escopo', 'arquitetura', 'passos',
                'etapas', 'interface', 'telas', 'tela', 'versao', 'mvp', 'prototipo', 'dados', 'requisitos',
                'testes', 'criterios', 'estados', 'navegacao', 'funcionalidades', 'plano', 'planejamento'})


def _planning_refused(text):
    normal = _normal(text)
    return bool(re.search(r'\b(?:nao|nunca)\s+(?:(?:quero|queremos|preciso|precisamos|vamos|deve)\s+(?:de\s+)?)?(?:planej\w*|planos?|esboco)\b|^\s*sem\s+(?:planejamento|plano)\b', normal)
                or re.search(r'\b(?:cancele|esqueca)\s+(?:tudo|o plano|o planejamento|o projeto)\b', normal))


def _new_topic(text):
    normal = _normal(text)
    noun = re.search(r'\b' + PRODUCT + r'\b', normal)
    if not noun: return False
    before = normal[max(0, noun.start() - 35):noun.start()]
    return _planning(text) and not re.search(r'\b(?:esse|essa|este|esta|nesse|nessa|desse|dessa|mesmo|mesma)\s*$', before)


def _followup(text):
    normal = _normal(text).strip()
    if re.search(r'\b(?:outro assunto|mude de assunto|esqueca|cancele tudo)\b', normal): return False
    return bool(re.match(r'^(?:agora\b|primeiro\b|antes\b|continue\b|prossiga\b|nao foi isso\b|esta generico\b|faltou\b|considere\b|inclua\b|adicione\b|retire\b|remova\b|sem\b|nao\b|prefiro\b|(?:o )?publico\b|(?:a )?finalidade\b|(?:o )?objetivo\b|preciso\b|quero\b|so\b|apenas\b|somente\b|e se\b|esse\b|essa\b|este\b|esta\b|ele\b|ela\b|isso\b)', normal)
                or _planning(text))


def _human_turns(question, messages):
    turns = []
    for item in messages:
        if isinstance(item, dict) and item.get('role') == 'user' and isinstance(item.get('content'), str):
            text = item['content'].strip()
            if text: turns.append({'text': text, 'source_turn': len(turns) + 1,
                                   'external_context': bool(item.get('attachments'))})
        elif isinstance(item, dict) and item.get('role') == 'tool' and turns:
            turns[-1]['external_context'] = True
    if not turns or turns[-1]['text'] != question:
        turns.append({'text': question, 'source_turn': len(turns) + 1, 'external_context': False})
    return turns


def _external_requested(turn):
    if (turn['external_context'] or re.search(r'https?://', turn['text'], re.I)
            or re.search(r'\b(?:README(?:\.[\w]+)?|[\w./-]+\.(?:md|json|txt|csv|yaml|yml|toml|xml|pdf|docx|xlsx))\b', turn['text'], re.I)):
        return True
    for found in re.finditer(r'\b(?:leia|ler|abra|abrir|inspecione|inspecionar|consulte|consultar|pesquise|pesquisar|busque|buscar|procure|procurar|acesse|acessar)\b', _normal(turn['text'])):
        if not _negated(turn['text'], found.start()): return True
    return False


def _clauses(turn):
    for found in re.finditer(r'[^.!?;\n,]+', turn['text']):
        item = _entry(turn, found.start(), found.end())
        if item['text']: yield item


def _fields(turn):
    text = turn['text']
    audience = purpose = None
    for key, pattern in [('audience', r'\b(?:p[uú]blico|usu[aá]rios?|usu[aá]rias?|audi[eê]ncia)\s*(?::|ser[aá]|[ée])\s*([^.!?;\n]+)'),
                         ('purpose', r'\b(?:objetivo|finalidade)\s*(?::|ser[aá]|[ée])\s*([^.!?;\n]+)')]:
        matches = list(re.finditer(pattern, text, re.I))
        if matches:
            found = matches[-1]
            value = _entry(turn, found.start(1), found.end(1))
            if key == 'audience': audience = value
            else: purpose = value
    target = re.search(r'\b' + PRODUCT + r'\b\s+(?:para|que permite|que permita)\s+([^.!?;\n,]+)', text, re.I)
    if target:
        tail = target.group(1)
        end = target.end(1)
        question_tail = re.search(r'\s+(?:precisa|deve|deveria|pode|poderia|vai)\s+(?:ter|conter|oferecer|permitir|fazer|incluir)\b', tail, re.I)
        if question_tail:
            end = target.start(1) + question_tail.start()
            tail = tail[:question_tail.start()]
        words = list(re.finditer(r'\b\w+\b', tail))
        verb = next((word for word in words if _normal(word.group()).endswith(('ar', 'er', 'ir', 'arem', 'erem', 'irem'))
                     and _normal(word.group()) not in {'celular', 'mulher', 'lugar', 'software', 'poder'}), None)
        if verb:
            start = target.start(1)
            if verb.start() and audience is None: audience = _entry(turn, start, start + verb.start())
            if purpose is None:
                negative = re.search(r'\s+(?:sem|mas|n[aã]o)\b', tail, re.I)
                if negative: end = start + negative.start()
                purpose = _entry(turn, start + verb.start(), end)
        elif audience is None and re.search(r'\b' + PRODUCT + r'\b\s+para\s+', target.group(), re.I):
            audience = _entry(turn, target.start(1), end)
    reduced = re.search(r'\b(?:primeiro|inicialmente)\s+(?:s[oó]|apenas|somente)\s+([^.!?;\n,]+)', text, re.I)
    if reduced: purpose = _entry(turn, reduced.start(1), reduced.end(1))
    return audience, purpose


def _literal_requirements(turns):
    requirements, exclusions, constraints = [], [], []
    for turn in turns:
        for item in _clauses(turn):
            text, normal = item['text'], _normal(item['text'])
            negative = re.search(r'\b(?:sem|nao|nunca)\b', normal)
            if negative and re.match(r'nao\s+(?:consegu\w*|consigo|consigu\w*|posso|pode|podemos|sei|sabe\w*|foi|funciona\w*|tenho|tem|ha)\b', normal[negative.start():]):
                negative = None  # A capability complaint or failure is not a product exclusion.
            if negative:
                start = item['evidence']['start'] + negative.start()
                item = _entry(turn, start, item['evidence']['end'])
                target = constraints if re.search(r'\b(?:arquivos?|codigo|internet|ferramentas?|workspace|programar|escrever|criar|crie|edite|editar|implementar|implemente|executar|execute|rode|pesquis\w*|consult\w*)\b', _normal(item['text'])) else exclusions
                target.append(item)
            elif re.search(r'\b(?:preciso|deve|devem|inclua|adicione|considere|registr\w*|cadastr\w*|acompanhar|guardar|salvar|listar|exportar|controlar|calcular|gerenciar)\b', normal):
                # A later explicit inclusion replaces an earlier exclusion of
                # that same literal object. It does not infer new requirements.
                keys = _tokens(text)
                exclusions[:] = [old for old in exclusions if not (_tokens(old['text']) and _tokens(old['text']) <= keys)]
                aim = _fields(turn)[1] if _new_topic(turn['text']) else None
                requirements.append(aim or item)
            for pattern in [r'\b(?:pelo|no|em um)\s+(?:celular|navegador)\b', r'\b(?:offline|localmente)\b',
                            r'\b(?:em|ate)\s+(?:\d+|dez|vinte|trinta)\s+(?:minutos?|horas?|dias?)\b',
                            r'\b(?:sao|serao)\s+[^,.;!?]+']:
                found = re.search(pattern, normal)
                if found: constraints.append(_entry(turn, item['evidence']['start'] + found.start(), item['evidence']['start'] + found.end()))
    def unique(items):
        seen = set(); result = []
        for item in reversed(items):
            key = _normal(item['text'])
            if key not in seen: result.append(item); seen.add(key)
        return list(reversed(result))
    return unique(requirements), unique(exclusions), unique(constraints)


def _excluded_memory(exclusions, constraints):
    literals = ' '.join(_normal(item['text']) for item in exclusions + constraints)
    history = bool(re.search(r'\bsem\s+(?:o\s+)?historico\b|\bnao\s+(?:(?:quero|preciso|deve)\s+)?(?:(?:ter|incluir|inclua|manter|exibir|mostrar|guardar|salvar|registrar)\s+)?(?:o\s+)?historico\b', literals))
    persistence = bool(re.search(r'\b(?:guardar|salvar|armazenar|persistir|registrar)\b.{0,30}\b(?:dados|registros|historico)\b|\b(?:persistencia|armazenamento)\b', literals))
    return history, persistence


def build_product_brief(question, messages=()):
    """Return a draft only for a software-planning discussion or its refinements.

    Literal entries point to 1-based human turns and character offsets in
    ``sources``. Assistant suggestions never become user requirements.
    """
    if not isinstance(question, str) or not question.strip() or len(question) > 12000: return None
    question = question.strip()
    if _build_requested(question) or _planning_refused(question) or _other_planning_topic(question): return None
    turns = _human_turns(question, messages)
    current = turns[-1]
    if _new_topic(question):
        selected = [current]
    elif _followup(question):
        selected = [current]
        for previous in reversed(turns[:-1][-12:]):
            if _build_requested(previous['text']) or _planning_refused(previous['text']) or _other_planning_topic(previous['text']): return None
            if _new_topic(previous['text']): selected.insert(0, previous); break
            if not _followup(previous['text']): return None
            selected.insert(0, previous)
        else: return None
    else: return None
    if any(_external_requested(turn) for turn in selected): return None
    audience = purpose = None
    for turn in selected:
        public, aim = _fields(turn)
        audience, purpose = public or audience, aim or purpose
    requirements, exclusions, constraints = _literal_requirements(selected)
    no_history, no_persistence = _excluded_memory(exclusions, constraints)
    no_memory = no_history or no_persistence
    goal = _entry(selected[0])
    nouns = list(re.finditer(r'\b' + PRODUCT + r'\b', selected[0]['text'], re.I))
    noun = next((found for found in nouns if re.fullmatch(r'apps?|aplicativos?|aplciativos?|sistemas?|plataformas?|portais?|sites?|apis?', found.group(), re.I)), nouns[0] if nouns else None)
    if noun:
        ending = re.search(r'[.!?;\n]', selected[0]['text'][noun.start():])
        product_end = noun.start() + ending.start() if ending else len(selected[0]['text'])
    product = _entry(selected[0], noun.start(), product_end) if noun else None
    gaps = []
    if not audience: gaps.append('Quem usará a primeira versão ainda precisa ser definido.')
    if not purpose: gaps.append('A atividade principal e seu resultado esperado ainda precisam ser definidos.')
    gaps.append('A unidade de atividade e seus estados ainda precisam ser confirmados.')
    if not no_memory: gaps.append('A forma de guardar os registros ainda precisa ser escolhida.')
    question_next = ('Quem deve usar a primeira versão?' if not audience else
                     'Qual atividade precisa ser resolvida primeiro?' if not purpose else
                     'Qual resultado indica uma atividade concluída para esse público?' if no_memory else
                     'Qual registro representa uma atividade concluída para esse público?')
    return {'schema': SCHEMA, 'status': 'draft', 'method': 'procedural', 'qualified': False,
            'tool_executed': False, 'goal': goal, 'product': product, 'current_request': _entry(current),
            'goals': [_entry(turn) for turn in selected], 'audience': audience, 'purpose': purpose,
            'requirements': requirements, 'exclusions': exclusions, 'constraints': constraints,
            'sources': [{'text': turn['text'], 'source_turn': turn['source_turn']} for turn in selected], 'assumptions': [
                'Sugestão provisória: começar por um único fluxo e revisar o escopo antes de implementar.',
                'A unidade de atividade e seus estados são opções em aberto, não funcionalidades já definidas.'],
            'gaps': gaps,
            'mvp': {'provisional': True, 'flow': (['informar atividade', 'obter resultado', 'conferir resultado'] if no_memory else
                                                ['registrar atividade', 'acompanhar estado', 'rever histórico']),
                    'note': 'Modelo de fluxo sugerido; deve ser adaptado à finalidade informada.'},
            'milestones': [
                {'title': 'Delimitar o primeiro fluxo', 'action': 'Escolher a unidade de atividade, as entradas mínimas e os estados necessários.',
                 'acceptance': 'Um exemplo fictício deve mostrar o que entra, o que muda e quando a atividade termina.'},
                {'title': 'Revisar um esboço com o público', 'action': 'Representar o fluxo mínimo e conferir os requisitos e exclusões informados.',
                 'acceptance': 'Quem usaria o produto deve conseguir seguir o fluxo; itens fora do escopo ficam separados.'},
                {'title': 'Definir a verificação antes da implementação', 'action': ('Planejar testes para entrada válida, entrada inválida e resultado de uma única atividade.' if no_memory else
                                                                                 'Planejar testes para entrada válida, entrada inválida e preservação do histórico.'),
                 'acceptance': 'Cada teste deve declarar a entrada e o resultado esperado; implementação e execução ficam para uma etapa autorizada.'}],
            'next_step': {'action': 'Confirmar a unidade do primeiro fluxo antes de escolher a implementação.',
                          'question': question_next}}


def _display(entry):
    return entry['text'].strip().replace('?', '.').rstrip('.! ')


def render_product_brief(brief):
    """Render a useful draft while keeping proposals separate from given facts."""
    lines = ['Começaria por um esboço do menor fluxo útil, separando as decisões em aberto dos requisitos informados.', '']
    lines.append('Objetivo informado: ' + _display(brief['purpose']) + '.' if brief['purpose'] else
                 'Produto informado: ' + _display(brief['product'] or brief['goal']) + '.')
    lines.append('Público informado: ' + _display(brief['audience']) + '.' if brief['audience'] else 'Público: ainda não definido.')
    for key, label in [('requirements', 'Requisitos informados'), ('exclusions', 'Fora do escopo informado'), ('constraints', 'Restrições informadas')]:
        if brief[key]:
            lines.extend(['', label + ':'])
            lines.extend('- ' + _display(item) + '.' for item in brief[key])
    lines.extend(['', 'Premissas propostas:'])
    lines.extend('- ' + text for text in brief['assumptions'])
    lines.extend(['', 'MVP provisório: ' + ' → '.join(brief['mvp']['flow']) + '.', brief['mvp']['note'], '', 'Etapas e critérios de aceite:'])
    for index, stage in enumerate(brief['milestones'], 1):
        lines.append(f"{index}. {stage['title']}: {stage['action']} Critério: {stage['acceptance']}")
    lines.extend(['', 'Próxima decisão: ' + brief['next_step']['action'], brief['next_step']['question'],
                  '', 'Esboço provisório; a implementação e os testes ainda não foram realizados.'])
    return '\n'.join(lines)


def validate_product_answer(answer, brief):
    """A conservative draft rubric, not proof of general semantic competence."""
    if not isinstance(answer, str) or not 120 <= len(answer) <= 20000: return False, 'planning-answer-size'
    normal = _normal(answer)
    for found in re.finditer(r'\b(?:implementei|criei|desenvolvi|alterei|editei|executei|rodei|testei|verifiquei|publiquei|entreguei|conclui|finalizei|consultei|pesquisei|li)\b', normal):
        if not _negated(answer, found.start()): return False, 'planning-unobserved-action'
    if re.search(r'\b(?:testes|checks)\s+(?:passaram|foram executados|aprovados)|\b(?:foi implementado|esta implementado|arquivos foram (?:criados|alterados))\b', normal):
        return False, 'planning-unobserved-success'
    given = '\n'.join(turn['text'] for turn in brief['sources'])
    if set(re.findall(r'https?://[^\s)]+', answer)) - set(re.findall(r'https?://[^\s)]+', given)):
        return False, 'planning-invented-source'
    anchors = set()
    for item in [brief['audience'], brief['purpose']]:
        if item: anchors.update(_tokens(item['text']))
    if not anchors: anchors.update(_tokens(brief['goal']['text']))
    if anchors and not anchors.intersection(_tokens(answer)): return False, 'planning-missing-purpose-anchor'
    if not re.search(r'\bmvp\b|\b(?:primeira versao|versao minima|escopo minimo|menor versao|prototipo)\b', normal):
        return False, 'planning-missing-minimum-scope'
    if not re.search(r'\b(?:fluxo|etapas?|passos?)\b', normal): return False, 'planning-missing-flow'
    if not re.search(r'\b(?:criterio|aceite|verificacao|testes?|validacao)\b', normal): return False, 'planning-missing-verification'
    compact = re.sub(r'[^\w]+', ' ', normal).strip()
    no_history, no_persistence = _excluded_memory(brief['exclusions'], brief['constraints'])
    patterns = []
    if no_history or no_persistence:
        patterns.append(r'\b(?:rever|consultar|exibir|preservar|manter|mostrar|incluir)\b.{0,25}\bhistorico\b')
        patterns.append(r'\bpreservacao\s+(?:do\s+)?historico\b')
    if no_persistence:
        patterns.append(r'\b(?:salvar|guardar|armazenar|persistir|registrar)\b\s+(?:(?:o|os|a|as)\s+)?(?:dados|registros|historico)\b')
    for pattern in patterns:
        for found in re.finditer(pattern, normal):
            if not _negated(answer, found.start()): return False, 'planning-contradicts-memory-exclusion'
    for item in brief['constraints'] + brief['exclusions']:
        literal = re.sub(r'[^\w]+', ' ', _normal(item['text'])).strip()
        if literal not in compact: return False, 'planning-missing-literal-restriction'
    return True, 'accepted-procedural-planning-rubric'
