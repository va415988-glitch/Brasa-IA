"""Translate an accepted learned route into a bounded, evidence-backed operation."""
from __future__ import annotations
import json
import re
import difflib
from cognitive_dialogue import build_frame, planner_response, validate_decision
from cognitive_router import predict_router
from dialogue import normalize

PATH_PATTERN = re.compile(r'(?<![\w.])(?:\.?\.?/)?[\w./-]+\.(?:txt|md|json|toml|ya?ml|csv|tsv|py|js|ts|rs|html|css|pdf|docx|xlsx|pptx|odt|ods|odp|epub|rtf|png|jpe?g|webp|gif|bmp|tiff?|wav|mp3|ogg|flac|m4a|mp4|mov|mkv|webm)\b', re.I)
MEDIA_SUFFIXES = frozenset({'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff', 'wav', 'mp3', 'ogg', 'flac', 'm4a', 'mp4', 'mov', 'mkv', 'webm'})
DOCUMENT_SUFFIXES = frozenset({'pdf', 'docx', 'xlsx', 'pptx', 'odt', 'ods', 'odp', 'epub', 'rtf'})


def positive_consultation_text(goal):
    """Remove negated consultation verbs without dropping a later positive request."""
    text = normalize(goal)
    verbs = re.compile(
        r'\b(?:pesquise|pesquisar|busque|buscar|procure|procurar|leia|ler|abra|abrir|'
        r'inspecione|inspecionar|examine|examinar|consulte|consultar|confira|conferir|'
        r'revise|revisar|analise|analisar|informe|diga)\b',
    )
    negative = re.compile(
        r'\b(?:nao|nunca|sem|nem)(?:\s+(?:quero|queremos|precisa|preciso|precisamos|'
        r'deve|devo|pode|posso|que|voce|usar|use|acessar|fazer)){0,8}\s*$',
    )
    parts, end = [], 0
    for match in verbs.finditer(text):
        prefix = re.split(r'[;,!?]|\b(?:mas|porem|so|somente|apenas)\b', text[:match.start()])[-1]
        parts.append(text[end:match.start()])
        parts.append(' ' * len(match[0]) if negative.search(prefix) else match[0])
        end = match.end()
    parts.append(text[end:])
    return ''.join(parts)


def consultation_intent(frame, *, include_literals=True):
    """Identify a need for a source independently of the router's confidence.

    A proposal, planning request or critique does not need a file merely because
    a four-class selector is uncertain. Sources and observations retain their
    existing read-only path; uncertain dialogue retains the dialogue provider.
    """
    goal = frame['goal']
    text = positive_consultation_text(goal)
    urls = re.findall(r'https?://[^\s<>"`]+', goal) if include_literals else []
    paths = [path for path in PATH_PATTERN.findall(goal) if path.lower() != 'node.js'] if include_literals else []
    local = bool(re.search(
        r'\b(?:leia|ler|abra|abrir|inspecione|inspecionar|examine|examinar|'
        r'consulte|consultar|procure|procurar|busque|buscar|pesquise|pesquisar|'
        r'analise|analisar|confira|conferir|revise|revisar)\b.{0,120}\b'
        r'(?:arquivos?|documentos?|workspace|repositorio|repo|codigo|projeto|pasta)\b', text,
    ))
    # The object of a read must still be identified; no planner may guess a file.
    unnamed_file = bool(re.search(
        r'\b(?:leia|ler|abra|abrir|consulte|consultar)\b.{0,80}\b(?:arquivos?|documentos?)\b', text,
    )) and not paths and not urls
    web = bool(re.search(
        r'\b(?:pesquise|pesquisar|busque|buscar|procure|procurar)\b|'
        r'\b(?:consulte|consultar|confira|conferir|leia|ler|abra|abrir)\b.{0,100}\b'
        r'(?:web|internet|online|site|pagina|fontes? externas?|documentacao)\b|'
        r'\b(?:qual|quais|onde|encontre|encontrar|mostre|mostrar)\b.{0,80}\bdocumentacao\b|'
        r'\b(?:fontes? atuais|fontes? recentes)\b', text,
    ))
    fact = r'(?:versao|release|lancamento|preco|cotacao|cambio|juros|presidente|ceo|noticias?|clima)'
    current = r'(?:atual|atuais|hoje|agora|mais recente|ultima|ultimo)'
    volatile = bool(re.search(r'\b' + fact + r'\b.{0,80}\b' + current + r'\b', text)
                    or re.search(r'\b' + current + r'\b.{0,40}\b' + fact + r'\b', text))
    asks_fact = bool(
        re.search(r'\bqual(?:\s+e)?\s+(?:(?:a|o)\s+)?(?:' + current + r'\s+)?' + fact + r'\b', text)
        or re.match(r'^\s*(?:quem|quanto|como esta|informe|diga)\b', text)
        or '?' in goal and re.match(r'^\s*(?:(?:a|o)\s+)?(?:' + current + r'\s+)?' + fact + r'\b', text)
    )
    policy_text = [value if isinstance(value, str) else value.get('text', '')
                   for value in frame.get('constraints', []) if isinstance(value, (str, dict))]
    policy = normalize(goal) + '\n' + '\n'.join(normalize(value) for value in policy_text
                                                if isinstance(value, str))
    web_excluded = bool(re.search(
        r'\b(?:sem|nao)\s+(?:(?:usar|acessar|pesquisar|consultar|buscar)\s+)?'
        r'(?:(?:a|na|pela)\s+)?(?:web|internet|online)\b|'
        r'\bnao\s+(?:pesquise|busque|acesse|consulte)\b.{0,80}\b(?:web|internet|online|fontes externas)\b',
        policy,
    ))
    generic = bool(re.search(r'\b(?:consulte|consultar|confira|conferir)\b.{0,80}\bfonte\b', text))
    kind = ('web' if urls else 'local' if paths or local else 'web' if web or volatile and asks_fact
            else 'query' if generic else None)
    return {'kind': kind, 'urls': urls, 'paths': paths, 'web_excluded': web_excluded,
            'unnamed_file': unnamed_file}


def source_requested(goal):
    """Public guard for positive consultation/fresh facts, excluding literal sources.

    Callers handle files, URLs, attachments and observations separately. Negated
    reads/searches and plans merely mentioning prices do not request a source.
    """
    return consultation_intent({'goal': goal}, include_literals=False)['kind'] is not None


def unresolved_reference(frame):
    """A bare referent can ask for clarification; prior dialogue stays contextual."""
    text = normalize(frame['goal']).strip().rstrip('?.!').strip()
    referential = bool(re.fullmatch(
        r'(?:(?:e|mas|entao)\s+)?(?:qual deles|qual delas|qual desses|qual dessas|'
        r'(?:o que e|como funciona|pode explicar|pode me explicar) (?:isso|aquilo)|'
        r'(?:isso|aquilo)(?: funciona| atende| serve)?|'
        r'pode dizer se isso atende ao que preciso)', text,
    ))
    prior = list(frame.get('history', []))
    if prior and prior[-1] == {'role': 'user', 'content': frame['goal']}:
        prior.pop()
    return referential and not any(str(row.get('content') or '').strip() for row in prior)


def structured(kind, text, gap='', tool=None, refs=None):
    return {'decision': kind, 'text': text, 'gap': gap, 'tool_call': tool, 'evidence_ids': refs or []}


def excerpts(data):
    """Return observed text literally, never present an inferred answer as observed."""
    found = []
    if isinstance(data, dict):
        for key in ('content', 'text', 'excerpt', 'snippet'):
            if isinstance(data.get(key), str) and data[key].strip():
                found.append(data[key].strip()[:1200])
                break
        if not found:
            for key in ('pages', 'results', 'sources', 'search_results'):
                if isinstance(data.get(key), list):
                    for item in data[key][:3]:
                        found.extend(excerpts(item))
    return found[:3]


def candidates(data):
    if not isinstance(data, dict):
        return []
    items = data.get('search_results') or data.get('results') or data.get('sources') or []
    return [item['url'] for item in items if isinstance(item, dict)
            and isinstance(item.get('url'), str) and item['url'].startswith(('https://', 'http://'))]


def consultation(tool, arguments, gap):
    return structured('consult', gap, gap, {'tool': tool, 'arguments': arguments})


def file_consultation(path, tools):
    suffix = path.rsplit('.', 1)[-1].lower()
    tool = 'inspect_media' if suffix in MEDIA_SUFFIXES else 'extract_document_text' if suffix in DOCUMENT_SUFFIXES else 'read_file'
    if tool not in tools:
        return structured('blocked', 'Selecione um workspace com a ferramenta de leitura disponível para esse formato.',
                          'Leitor local indisponível: ' + tool + '.')
    args = {'path': path}
    if tool == 'read_file':
        args['max_bytes'] = 8192
    elif tool == 'extract_document_text':
        args['max_chars'] = 24000
    return consultation(tool, args, 'Vou conferir o conteúdo de ' + path + '.')


def media_decision(rows, goal):
    media = []
    for row in rows:
        data = row.get('data')
        if not row.get('ok') or not isinstance(data, dict) or row['tool'] not in {'attachment_evidence', 'inspect_media'}:
            continue
        observed = data.get('observation') or data
        kind = data.get('kind') or data.get('media_type')
        metadata = observed.get('metadata') or {}
        if kind in {'audio', 'video', 'image'}:
            media.append((row, kind, observed, metadata))
    for row, kind, observed, metadata in media:
        if kind == 'audio' and re.search(r'\b(?:transcreva|transcri[cç][aã]o|falas?|palavras?|ou[cç]a|escute|o que.*diz|resuma.*[aá]udio)\b', goal, re.I):
            return structured('blocked', 'O áudio foi recebido e seus metadados foram lidos. Não há um modelo próprio de transcrição conectado; não posso afirmar quais palavras foram faladas.',
                              'Falta o modelo próprio de reconhecimento de fala.', refs=[row['id']])
        if kind == 'video' and re.search(r'\b(?:cenas?|acontece|a[cç][oõ]es|falas?|transcreva|resuma|descreva)\b', goal, re.I):
            return structured('blocked', 'O vídeo foi recebido e o contêiner foi inspecionado. A interpretação de quadros e fala ainda precisa de modelos próprios conectados.',
                              'Faltam os modelos próprios de percepção de vídeo.', refs=[row['id']])
        if kind == 'image' and re.search(r'\b(?:cenas?|objetos?|pessoas?|animais?|mostra|apar[eê]ncia|descreva.*imagem)\b', goal, re.I):
            return structured('blocked', 'A imagem foi recebida; posso extrair metadados e tentar OCR de texto impresso. A descrição de objetos e relações ainda precisa de um modelo visual próprio.',
                              'Falta o modelo próprio de compreensão de cenas.', refs=[row['id']])
        if kind == 'image' and re.search(r'\b(?:leia|texto|ocr|escrito)\b', goal, re.I) and (
                observed.get('extracted_text') is False or metadata.get('ocr', {}).get('glyph_count') == 0):
            return structured('blocked', 'A imagem foi recebida, mas o OCR próprio experimental não extraiu texto utilizável.',
                              'Falta uma leitura de texto verificável dessa imagem.', refs=[row['id']])
    if re.search(r'\b(?:dura[cç][aã]o|quantos? segundos|quanto tempo)\b', goal, re.I):
        selected = [(row, metadata) for row, kind, _, metadata in media if kind in {'audio', 'video'}]
        if selected:
            for row, metadata in selected:
                seconds = metadata.get('duration_seconds')
                if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
                    return structured('blocked', 'O arquivo foi recebido, mas o leitor local não conseguiu determinar sua duração.',
                                      'Falta um decodificador ou metadado de duração compatível com esse arquivo.', refs=[row['id']])
            return structured('answer', '\n'.join(f"{row['data'].get('path', 'Arquivo')}: duração de {metadata['duration_seconds']} segundos."
                                                 for row, metadata in selected), refs=[row['id'] for row, _ in selected])
    if re.search(r'\b(?:dimens[oõ]es|resolu[cç][aã]o|largura|altura)\b', goal, re.I):
        selected = [(row, metadata) for row, kind, _, metadata in media if kind == 'image'
                    and isinstance(metadata.get('width'), int) and isinstance(metadata.get('height'), int)]
        if selected:
            return structured('answer', '\n'.join(f"{row['data'].get('path', 'Imagem')}: {metadata['width']} × {metadata['height']} pixels."
                                                 for row, metadata in selected), refs=[row['id'] for row, _ in selected])
    return None


def choose_action(frame, prediction):
    goal = frame['goal']
    tools = frame['tools']
    explicit_urls = re.findall(r'https?://[^\s<>"`]+', goal)
    if explicit_urls and frame['observations'] and all(row['tool'] == 'attachment_evidence' for row in frame['observations']) and 'open_page' in tools:
        return consultation('open_page', {'url': explicit_urls[0].rstrip('.,;')}, 'Vou consultar a URL indicada no pedido; o anexo é contexto adicional.')
    if frame['observations'] and all(row['tool'] == 'attachment_evidence' for row in frame['observations']) \
            and re.search(r'\b(?:pesquise|pesquisar|busque|documenta[cç][aã]o)\b', goal, re.I) \
            and not re.search(r'\b(?:sem|n[aã]o).*\b(?:web|internet)\b', goal, re.I):
        if 'research_web' in tools:
            return consultation('research_web', {'query': goal, 'save_to_corpus': False, 'max_results': 3},
                                'Vou pesquisar o pedido; os anexos permanecem como contexto adicional.')
    if frame['observations']:
        rows = frame['observations']
        observed_media = media_decision(rows, goal)
        if observed_media:
            return observed_media
        last = rows[-1]
        data = last['data'] if isinstance(last['data'], dict) else {}
        attempted_urls = {row['data'].get('url') for row in rows
                          if row['tool'] == 'open_page' and isinstance(row['data'], dict)}
        # Search snippets are discovery evidence. Open an actual source before
        # declaring an answer, and try a different result after a failed fetch.
        if last['tool'] in {'search_web', 'research_web', 'open_page'}:
            urls = list(dict.fromkeys(url for row in rows for url in candidates(row['data'])))
            pending = [url for url in urls if url not in attempted_urls]
            pages = data.get('pages') or []
            if pending and 'open_page' in tools and (last['tool'] != 'research_web' or not pages):
                if last['tool'] != 'open_page' or not last['ok'] or not excerpts(data):
                    return consultation('open_page', {'url': pending[0]}, 'Vou abrir uma fonte encontrada para conferir seu conteúdo.')
            if (not last['ok'] or not excerpts(data)) and 'search_web' in tools \
                    and not any(row['tool'] == 'search_web' for row in rows):
                return consultation('search_web', {'query': goal}, 'A primeira consulta falhou; vou buscar outra fonte acessível.')
        if last['tool'] in {'read_file', 'extract_document_text', 'inspect_media'} and not last['ok'] and 'find_paths' in tools \
                and not any(row['tool'] == 'find_paths' for row in rows):
            paths = PATH_PATTERN.findall(goal)
            if paths:
                return consultation('find_paths', {'pattern': paths[0].split('/')[-1], 'max_results': 20},
                                    'Vou localizar o arquivo solicitado no workspace antes de tentar outra leitura.')
        if last['tool'] == 'find_paths' and last['ok'] and not data.get('truncated'):
            matches = [item for item in data.get('matches', [])
                       if isinstance(item, dict) and item.get('kind') == 'file' and item.get('path')]
            if len(matches) == 1:
                return file_consultation(matches[0]['path'], tools)
        useful = [(row, excerpts(row['data'])) for row in frame['observations'] if row['ok']]
        useful = [(row, texts) for row, texts in useful
                  if row['tool'] in {'read_file', 'extract_document_text', 'open_page', 'research_web', 'attachment_evidence', 'inspect_media'}]
        useful = [(row, texts) for row, texts in useful if texts]
        if useful:
            attached = [(row, texts) for row, texts in useful if row['tool'] == 'attachment_evidence']
            if attached:
                if re.search(r'\b(?:compare|diferen[cç]as?|difere|mudou)\b', goal, re.I) and len(attached) == 2:
                    left, right = attached[0][0], attached[1][0]
                    a, b = left['data'].get('content', ''), right['data'].get('content', '')
                    delta = '\n'.join(list(difflib.unified_diff(a.splitlines(), b.splitlines(),
                        fromfile=left['data'].get('path', 'anexo-1'), tofile=right['data'].get('path', 'anexo-2'), lineterm=''))[:60])
                    return structured('answer', 'Comparação literal dos trechos observados (sem inferir o restante do arquivo):\n\n'
                                      + ('```diff\n'+delta+'\n```' if delta else 'Os trechos disponíveis coincidem.'), refs=[left['id'], right['id']])
                if re.search(r'\b(?:total|soma|m[eé]dia|estat[ií]sticas?|linhas|colunas)\b', goal, re.I):
                    tables = []
                    for row, _ in attached:
                        data = row['data']
                        table = (data.get('observation') or {}).get('metadata', {}).get('table')
                        if isinstance(table, dict):
                            details = [str(table.get('rows_observed', 0)) + ' linhas observadas.']
                            for column in table.get('numeric_columns', []):
                                details.append(f"{column['column']}: soma {column['sum']}; média {column['mean']}; {column['numeric_values']} valores numéricos.")
                            if table.get('truncated') or table.get('source_truncated'):
                                details.append('A leitura está truncada; os totais cobrem somente as linhas observadas.')
                            tables.append((row, data.get('path', 'tabela') + ':\n'+'\n'.join(details)))
                    if tables:
                        return structured('answer', 'Estatísticas calculadas localmente:\n\n'+'\n\n'.join(text for _, text in tables), refs=[row['id'] for row, _ in tables])
                paragraphs = []
                selected = useful[:8]
                for row, texts in selected:
                    data = row['data'] if isinstance(row['data'], dict) else {}
                    paragraphs.append(str(data.get('path') or 'Anexo') + ':\n\n'
                                      + '\n\n'.join('> ' + text.replace('\n', '\n> ') for text in texts)
                                      + ('\n\nLimites da leitura: '+'; '.join(data.get('warnings', [])) if data.get('warnings') else ''))
                return structured('answer', 'Evidências recebidas dos anexos:\n\n' + '\n\n'.join(paragraphs),
                                  refs=[row['id'] for row, _ in selected])
            row, texts = useful[-1]
            quote = '\n\n'.join('> ' + text.replace('\n', '\n> ') for text in texts)
            return structured('answer', 'Trechos obtidos da fonte consultada:\n\n' + quote, refs=[row['id']])
        last = frame['observations'][-1]
        return structured('blocked', 'A consulta não retornou conteúdo suficiente para responder. '
                          + (last.get('error') or 'Não encontrei um trecho de fonte utilizável.'),
                          'Falta uma fonte acessível com conteúdo pertinente ao pedido.')
    intent = consultation_intent(frame)
    urls, paths = intent['urls'], intent['paths']
    label = prediction['label']
    # Explicit sources outweigh confidence in a four-class routing model.
    if intent['kind'] in {'web', 'local'}:
        label = intent['kind']
    elif intent['kind'] is None:
        if unresolved_reference(frame):
            return structured('blocked', 'A qual item, biblioteca ou opção você está se referindo?',
                              'O referente do pedido não foi identificado.')
        return None
    elif not prediction['accepted'] or label not in {'web', 'local'}:
        return structured('blocked', 'Não consegui identificar com segurança a consulta necessária. Indique a fonte ou o arquivo.',
                          'A escolha da ação está abaixo do limiar de confiança validado.')
    if label == 'local':
        if not paths:
            return structured('blocked', 'Qual arquivo do projeto devo consultar?', 'Arquivo local não identificado no pedido.')
        return file_consultation(paths[0], tools)
    if label == 'web':
        if paths and not urls:
            return structured('blocked', 'O pedido menciona um arquivo; preciso confirmar a fonte antes de pesquisar fora do projeto.',
                              'Origem local ou externa ainda ambígua.')
        if intent['web_excluded']:
            return structured('blocked', 'A consulta web está excluída pelo pedido. Indique uma fonte local.', 'Falta fonte permitida.')
        if urls and 'open_page' in tools:
            call = {'tool': 'open_page', 'arguments': {'url': urls[0].rstrip('.,;')}}
        elif 'research_web' in tools:
            call = {'tool': 'research_web', 'arguments': {'query': goal, 'save_to_corpus': False, 'max_results': 3}}
        elif 'search_web' in tools:
            call = {'tool': 'search_web', 'arguments': {'query': goal}}
        else:
            return structured('blocked', 'Não há ferramenta de pesquisa disponível nesta sessão.', 'Consulta externa indisponível.')
        return structured('consult', 'Vou consultar uma fonte para responder.', 'Obter informação atual ou documentação externa.', call)
    if label == 'clarify':
        return structured('blocked', 'A qual item, biblioteca ou opção você está se referindo?', 'O referente do pedido não foi identificado.')
    return None  # Direct answers retain the existing dialogue provider and its quality gates.


def router_response(service, messages, cognition, bundle, on_delta=None):
    frame = build_frame(messages, cognition, service.tools)
    prediction = predict_router(bundle, frame['goal']) if not frame['observations'] else {
        'label': 'evidence', 'score': 1.0, 'accepted': True}
    value = choose_action(frame, prediction)
    skill_routing = None
    intent = consultation_intent(frame)
    if (not frame['observations'] and len(frame['goal']) <= 8000
            and intent['kind'] == 'local' and not intent['unnamed_file']
            and not intent['paths'] and not intent['urls']
            and not (intent['kind'] == 'web' and intent['web_excluded'])
            and value is not None and value['decision'] == 'blocked'):
        call = service.plan_tool(frame['goal'], messages=messages)
        routing = (call or {}).get('skill_routing') or {}
        implementation = any(skill.get('id') == 'implementation' for skill in routing.get('selected_skills', []))
        if call and routing and not implementation and call.get('tool') in frame['tools']:
            skill_routing = call['skill_routing']
            value = consultation(call['tool'], call['arguments'],
                                 'Vou consultar a capacidade de leitura selecionada pela habilidade registrada.')
    if value is None:
        response = service._reply(messages, objective='conversation', cognition=None, on_delta=on_delta)
        if response.get('backend') == 'quality-gate':
            response['agent'] = service._agent('blocked', 'plan', 0,
                stop_reason='cognitive_direct_answer_unavailable', retryable=False, verified=False)
    else:
        value = validate_decision(json.dumps(value, ensure_ascii=False), frame, service.tools)
        response = planner_response(value, frame)
        response['backend'] = 'cognitive-router-evidence' if frame['observations'] else 'cognitive-router'
        if on_delta and value['decision'] == 'answer':
            for offset in range(0, len(response['text']), 120):
                on_delta(response['text'][offset:offset + 120])
    response['action_selection'] = {**prediction, 'weights_sha256': bundle[2]['sha256'],
                                    'answer_method': 'quoted-observation' if frame['observations'] else 'existing-dialogue-or-tool'}
    if skill_routing:
        response['skill_routing'] = skill_routing
    return response
