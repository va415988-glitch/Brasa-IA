"""Separa instrução, histórico e material citado antes do roteamento."""
import re
import unicodedata


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if unicodedata.category(c) != 'Mn')


def is_workspace_identity_question(question):
    """Detecta perguntas sobre a pasta/projeto selecionado nesta sessão."""
    text = normalize(str(question or '')).strip()
    if re.search(
        r'\b(?:analise|analisar|analisa|examine|examinar|inspecione|inspecionar|'
        r'revise|revisar|investigue|investigar|crie|criar|edite|editar|corrija|'
        r'corrigir|implemente|implementar|altere|alterar|modifique|modificar)\b', text,
    ):
        return False
    asks_identity = re.search(
        r'\b(?:qual\s+(?:(?:e\s+)?(?:o\s+)?)?(?:nome\s+do\s+)?'
        r'(?:workspace|projeto|pasta|diretorio)|onde\s+(?:esta|fica)|'
        r'em\s+qual\s+(?:workspace|projeto|pasta|diretorio)|'
        r'em\s+que\s+(?:workspace|projeto|pasta|diretorio)|'
        r'(?:nome|caminho)\s+do\s+(?:workspace|projeto|pasta|diretorio))\b', text,
    )
    names_workspace = re.search(r'\b(?:workspace|projeto|pasta|diretorio)\b', text)
    describes_selection = re.search(
        r'\b(?:aberto|aberta|atual|selecionado|selecionada|ativo|ativa|esta|sessao|usa|usando|fica)\b', text,
    )
    explicit_name_or_path = re.search(
        r'\b(?:nome|caminho)\s+do\s+(?:workspace|projeto|pasta|diretorio)\b', text,
    )
    return bool(asks_identity and names_workspace and (describes_selection or explicit_name_or_path))


def is_workspace_inventory_question(question):
    """Reconhece pedidos de inventário da pasta selecionada, sem pesquisa web."""
    text = normalize(str(question or '')).strip()
    target = r'(?:workspace|projeto|pasta|diretorio|repositorio|repo)'
    if re.search(r'\b(?:crie|criar|edite|editar|corrija|corrigir|implemente|implementar|remova|remover)\b', text):
        return False
    if re.search(r'\b(?:o que (?:ha|tem|existe)|que (?:arquivos|pastas|itens) (?:ha|tem|existem))\s+(?:no|na|neste|nesta|nesse|nessa)\s+' + target + r'\b', text):
        return True
    if re.search(r'\bquais?\s+(?:arquivos|pastas|diretorios|itens)\b.{0,50}\b(?:no|na|do|da|neste|nesta)\s+' + target + r'\b', text):
        return True
    return bool(re.search(
        r'\b(?:liste|listar|mostre|mostrar|veja|ver)\s+(?:(?:os|as|a|o)\s+)?'
        r'(?:arquivos|pastas|diretorios|itens|conteudo|estrutura)\b.{0,50}'
        r'\b(?:no|na|do|da|neste|nesta)\s+' + target + r'\b', text,
    ))


def explicit_workspace_change_request(question):
    """Recognize an affirmative file-change instruction, not a negated mention."""
    text = normalize(str(question or '')).strip()
    action = re.compile(
        r'\b(?:crie|criar|construa|construir|desenvolva|desenvolver|'
        r'implemente|implementar|edite|editar|altere|alterar|modifique|modificar|'
        r'corrija|corrigir|refatore|refatorar|apague|apagar|remova|remover|'
        r'monte|montar|integre|integrar)\b'
    )
    for match in action.finditer(text):
        prefix = text[max(0, match.start() - 48):match.start()]
        if re.search(
            r'\b(?:nao|sem)\s+(?:(?:quero|preciso|precisa|deve|devemos|'
            r'vamos|pode|posso|deveria)\s+)?(?:me\s+)?$', prefix,
        ):
            continue
        return True
    return False


def is_proposal_only_request(question):
    """Recognize an explicit request to plan an action without authorizing it."""
    text = normalize(str(question or '')).strip()
    return bool(re.search(
        r'\b(?:so|apenas|somente)\s+(?:(?:me|a)\s+)?'
        r'(?:proponha|propor|sugira|sugerir|planeje|planejar|descreva|descrever)\b',
        text,
    ))


def is_project_understanding_request(question):
    """Detecta quando a pessoa quer uma conclusão sobre o projeto após ler evidências."""
    text = normalize(str(question or '')).strip()
    # Pedidos que nomeiam arquivos e pedem sua leitura devem seguir o fluxo
    # explícito de leitura, mesmo quando também pedem uma explicação do projeto.
    # A inspeção ampla do workspace não substitui essas leituras direcionadas.
    explicit_file_read = (
        re.search(r'\b(?:leia|ler|abra|abrir)\b', text)
        and re.search(r'(?<![\w./-])[\w.-]+(?:/[\w.-]+)*\.(?:py|rs|ts|tsx|js|jsx|json|toml|md|txt|html|css|yml|yaml)\b', text)
    )
    if explicit_file_read:
        return False
    explicit_workspace_target = re.search(r'\b(?:workspace|projeto|repositorio|repo|arquivos?|codigo|aplicativo|aplicacao|pasta|diretorio)\b', text)
    political_domain = re.search(
        r'\b(?:sistema politico|estado brasileiro|estado da bahia|governo|instituicoes|politica|politico)\b', text,
    )
    if political_domain and not explicit_workspace_target:
        return False
    # "código" sozinho também aparece em perguntas técnicas e pedidos de
    # explicação/revisão; não significa que a pessoa pediu uma inspeção do
    # workspace. Exija um alvo de projeto ou arquivos explicitamente indicado.
    target = re.search(r'\b(?:projeto|workspace|repositorio|repo|sistema|aplicativo|arquivos?|diretorio|pasta)\b', text)
    asks_understanding = re.search(
        r'\b(?:o que (?:e|faz|(?:voce )?me diz)|sobre o que|conclusao|resumo|resuma|'
        r'explique|explica|descreva|descreve|como funciona|para que serve|finalidade|objetivo|'
        r'estado(?: atual)?|situacao(?: atual)?|panorama|como esta)\b', text,
    )
    asks_analysis = re.search(
        r'\b(?:analise|analisar|analisa|examine|examinar|examina|inspecione|inspecionar|'
        r'inspeciona|revise|revisar|revisa|investigue|investigar|investiga)\b', text,
    )
    asks_mutation = explicit_workspace_change_request(text)
    return bool(target and (asks_understanding or asks_analysis) and not asks_mutation)


def is_opinion_request(question):
    """Recognize an explicit request for judgment before classifying its topic."""
    text = normalize(str(question or '')).strip()
    if not text:
        return False

    asks_for_judgment = re.search(
        r'\b(?:concorda(?:s)?(?:\s+comigo)?(?:\s+que)?|'
        r'voce\s+concorda(?:\s+comigo)?(?:\s+que)?|'
        r'voce\s+acha(?:\s+que)?|vc\s+acha(?:\s+que)?|'
        r'o\s+que\s+(?:voce\s+)?acha|'
        r'(?:na|em)\s+(?:sua|tua)\s+opiniao|'
        r'qual\s+(?:a\s+)?(?:sua\s+)?opiniao|'
        r'o\s+que\s+(?:voce\s+)?pensa\s+(?:de|sobre)|'
        r'qual\s+(?:a\s+)?sua\s+leitura\s+(?:de|sobre))\b',
        text,
    )
    if not asks_for_judgment:
        return False

    # A request for judgment may be followed by a separate implementation ask.
    # Keep that operational goal in the workspace/planning routes.
    action_command = re.search(
        r'\b(?:crie|cria|edite|corrija|implemente|altere|modifique|aplique|'
        r'execute|rode|liste|pesquise|investigue|analise|construa|desenvolva|'
        r'integre|configure)\b', text,
    )
    return not action_command


def is_advice_request(question):
    """Recognize a request to choose or recommend a next step, not an opinion poll."""
    text = normalize(str(question or '')).strip()
    return bool(re.search(
        r'\bqual\s+(?:(?:caminho|opcao|del[ae]s?)\s+)?(?:voce\s+)?'
        r'(?:tentaria|escolheria|recomendaria|recomenda|indicaria|indica)\b',
        text,
    ))


def is_diagnostic_advice_request(question):
    """Keep hypothesis and safe-check questions conversational unless inspection is requested."""
    text = normalize(str(question or '')).strip()
    asks_for_diagnosis = re.search(
        r'\b(?:qual hipotese|que hipotese|o que posso concluir|o que seria apenas hipotese|'
        r'qual experimento|que experimento|qual checagem|que checagem|'
        r'como investigar|como eu investigaria)\b', text,
    )
    explicitly_targets_workspace = re.search(
        r'\b(?:inspecione|inspecionar|analise|analisar|examine|examinar|leia|ler|abra|abrir|'
        r'revise|revisar|depure|depurar|debugue|debugar)\b.{0,120}\b'
        r'(?:projeto|workspace|repositorio|arquivos?|codigo|src/|[\w.-]+\.(?:py|ts|tsx|js|jsx|rs|cpp|h))\b',
        text,
    )
    return bool(asks_for_diagnosis and not explicitly_targets_workspace)


def is_idea_discussion_request(question):
    """Recognize a tentative proposal that invites discussion, not execution."""
    text = normalize(str(question or '')).strip()
    proposal = re.search(
        r'\b(?:tenho\s+(?:uma\s+)?(?:ideia|solucao)|pensei\s+(?:numa|em\s+uma)\s+(?:ideia|solucao)|'
        r'minha\s+(?:ideia|proposta|solucao)|(?:eu\s+)?(?:acho|acredito|considero|prefiro|sugiro)\s+que|'
        r'(?:me\s+)?ajud[ae]\s+a\s+explor\w*\s+(?:a\s+)?(?:ideia|proposta|solucao)|'
        r'me\s+parece\s+que|na\s+minha\s+(?:visao|opiniao)|para\s+mim|minha\s+leitura\s+e|'
        r'que\s+tal|podemos|poderiamos|e\s+se|'
        r'ao\s+inv[eé]s\s+de|em\s+vez\s+de)\b', text,
    )
    if not proposal:
        return False
    # An imperative request remains an action even if it also contains a proposal marker.
    return not re.match(
        r'^(?:por\s+favor\s+)?(?:crie|criar|implemente|implementar|desenvolva|desenvolver|'
        r'construa|construir|integre|integrar|configure|configurar|corrija|corrigir|aplique|aplicar)\b',
        text,
    )


def classify_speech_act(question, messages=None):
    """Label the conversational move even when generation falls back locally."""
    if is_opinion_request(question):
        return 'opinion_request'
    if is_idea_discussion_request(question):
        return 'proposal_discussion'
    history = [item for item in (messages or [])
               if isinstance(item, dict) and item.get('role') in {'user', 'assistant'}]
    if len(str(question or '').split()) <= 6 and len(history) > 1:
        return 'short_follow_up'
    return 'conversation'


def is_project_feedback_request(question, messages=None):
    """Recognize a request for project advice, including a contextual pronoun."""
    text = normalize(str(question or '')).strip()
    if not re.search(r'\b(?:melhoraria|melhorarias|melhorar|melhoras?|melhorias|aperfeicoaria|priorizaria|pontos (?:fracos|de melhora|de melhoria)|problemas|riscos)\b', text):
        return False
    if re.search(r'\b(?:melhore|implemente|corrija|edite|altere|aplique|faca)\b', text):
        return False
    if re.search(r'\b(?:projeto|workspace|repositorio|repo|sistema|aplicativo|codigo|diretorio|pasta)\b', text):
        return True
    if not re.search(r'\b(?:nele|nela|dele|dela|nisso|neste|nesse)\b', text):
        return False
    for item in reversed((messages or [])[:-1]):
        content = normalize(str(item.get('content') or ''))
        if item.get('role') == 'assistant' and re.search(r'\b(?:projeto|workspace|repositorio|stack|arquivos encontrados|estrutura)\b', content):
            return True
    return False




def is_project_continuation(question):
    """Recognize an instruction to resume work in the selected project."""
    text = normalize(question).strip()
    resume = re.search(r'\b(?:continue|continuar|prossiga|prosseguir|retome|retomar|avance|avancar|siga|seguir|volte|voltar|retorne|retornar|dar continuidade|de continuidade)\b', text)
    project = re.search(r'\b(?:workspace|projeto|pasta|diretorio|codigo|implementacao|interface|aplicativo|sistema|jogo|game)\b', text)
    return bool(resume and project)


def restore_followup_goal(messages):
    """Keep the original user task when a later turn explicitly resumes it."""
    users = [str(item.get('content') or '').strip() for item in messages
             if item.get('role') == 'user' and str(item.get('content') or '').strip()]
    if len(users) < 2:
        return None
    current = users[-1].split('\n\nWorkspace local:', 1)[0].strip()
    if not re.search(r'\b(?:retome|retomar|continue|continuar|prossiga|prosseguir|'
                     r'tente novamente|tenta de novo|de novo)\b', normalize(current)):
        return None
    action = re.compile(r'\b(?:crie|criar|edite|editar|corrija|corrigir|'
                        r'implemente|implementar|altere|alterar|desenvolva|desenvolver|'
                        r'construa|construir|analise|analisar|inspecione|inspecionar)\b')
    for previous in reversed(users[:-1]):
        original = previous.split('\n\nWorkspace local:', 1)[0].strip()
        if len(original.split()) >= 4 and action.search(normalize(original)):
            return original[:4000]
    return None


def is_conversational_text(question):
    """Classifica conversa social, emocional e follow-ups sem capturar tarefas técnicas."""
    text = normalize(question).strip()
    if not text:
        return False

    technical = re.search(
        r'\b(?:api|python|rust|javascript|typescript|sql|codigo|c[oó]digo|programacao|'
        r'programação|framework|biblioteca|endpoint|bug|algoritmo|arquivo|workspace|'
        r'projeto|aplicativo|aplicacao|sistema|site|pasta|terminal|teste|testes)\b',
        text,
    )
    operational = (
        re.search(r'\b(?:criar|crie|construir|construa|desenvolver|desenvolva|'
                  r'implementar|implemente|integrar|integre|configurar|configure|'
                  r'corrigir|corrija|editar|edite|ler|leia|listar|liste|'
                  r'pesquisar|pesquise|executar|execute)\b', text)
        and re.search(r'\b(?:workspace|projeto|aplicativo|aplicacao|sistema|site|'
                      r'arquivo|pasta|codigo|código|api|endpoint|funcionalidade|bot|assistente)\b', text)
    )
    if operational:
        return False

    conversational_critique = re.search(
        r'\b(?:falta alma|respostas prontas|nao entende o que (?:eu )?pergunto|falta discernimento)\b',
        text,
    )
    if conversational_critique:
        return True

    markers = (
        r'^(?:oi|ola|bom dia|boa tarde|boa noite|e ai)\b',
        r'\b(?:tudo bem|como voce esta|como vai voce|como voce esta hoje|esta tudo bem)\b',
        r'\b(?:obrigad[oa]|valeu)\b',
        r'\b(?:desculpa|foi mal|perdao)\b',
        r'\b(?:perfeito|beleza|legal|otimo|otima|entendi|certo|show|massa|concordo|discordo|faz sentido)\b',
        r'\b(?:nao entendi|não entendi|estou confus[oa]|to confus[oa]|estou perdid[oa]|'
        r'frustrad[oa]|cansad[oa]|ansios[oa]|preocupad[oa]|animad[oa])\b',
        r'\b(?:como assim|pode explicar|explique melhor|o que quis dizer|qual o primeiro passo|qual e o primeiro passo|e agora)\b',
        r'\b(?:qual deles primeiro|qual seria o primeiro|e por que|e porque|e isso resolve|'
        r'faz sentido|entao fechamos assim|entao seguimos assim|consegue explicar melhor|'
        r'e se der errado|qual opcao escolheria|o que ficou pendente)\b',
        r'\b(?:voce lembra|você lembra|lembra do que|o que decidimos|qual meu objetivo|'
        r'quais minhas preferencias|o que ficou pendente)\b',
        r'\b(?:se voce nao souber|se nao souber|reconhece que|voce confundiu|'
        r'qual foi a causa exata|meu teste falhou|estava correta|'
        r'voce leu todos os arquivos|resposta que voce me deu ontem|'
        r'melhor opcao para mim|sem voce saber|quanto vai custar|'
        r'garantir.{0,40}threads|threads.{0,40}garantir|'
        r'causa exata.{0,80}(?:rust|aplicativo|fechar)|'
        r'me ajuda com isso|sem lista e em uma frase|'
        r'(?:qual funcao.{0,100}framework|framework.{0,100}qual funcao).{0,100}(?:compilador|quantico)|'
        r'(?:quem ganhou|quem recebeu).{0,140}\b20\d{2}\b)\b',
        r'\b(?:quero conversar|vamos conversar|podemos conversar|me ajuda a pensar|'
        r'consegue me ajudar a pensar|me ajude a decidir|decisao dificil|decisão difícil|'
        r'desabafar|s[oó] conversar)\b',
        r'^(?:o que acha|o que voce acha|qual sua opiniao|qual a sua opiniao)\b',
        r'\b(?:pode me ajudar|consegue me ajudar|queria ajuda)\b',
    )
    if not any(re.search(pattern, text) for pattern in markers):
        return False
    explicit_conversation = bool(re.search(r'\b(?:quero|vamos|podemos)\s+conversar\b', text))
    # Uma pergunta técnica que começa com uma saudação continua técnica.
    if technical and not explicit_conversation and not re.search(
        r'\b(?:nao entendi|não entendi|confus[oa]|como assim|pode explicar|'
        r'o que quis dizer|qual o primeiro passo|qual deles primeiro|qual seria o primeiro|'
        r'e por que|e isso resolve|entao fechamos assim|consegue explicar melhor|'
        r'meu teste falhou|qual foi a causa exata|voce leu todos os arquivos|'
        r'voce confundiu|reconhece que|se voce nao souber|garantir.{0,40}threads|'
        r'causa exata.{0,80}(?:rust|aplicativo|fechar)|me ajuda com isso|sem lista e em uma frase|'
        r'(?:qual funcao.{0,100}framework|framework.{0,100}qual funcao)|'
        r'(?:quem ganhou|quem recebeu).{0,140}\b20\d{2}\b)\b',
        text,
    ):
        return False
    return True


def legacy_messages(prompt):
    # Compatibilidade com clientes antigos; novos clientes enviam JSON estruturado.
    return [{'role': role, 'content': content.strip()} for role, content in
            re.findall(r'<\|(user|assistant)\|>\n(.*?)(?=<\|(?:user|assistant)\|>|$)', prompt, re.S)
            if content.strip()]


def turn_context(messages):
    users = [m for m in messages if m.get('role') == 'user']
    if not users:
        return '', [], ''
    current = users[-1]
    question = current.get('content', '')
    # Editor location is context, not a second instruction to route.
    question = question.split('\n\nWorkspace local:', 1)[0].strip()
    # The IDE appends its active editor even when the user asks about the
    # entire project. It must not narrow that request to the incidental file.
    # Only remove the generated location format; explicit user paths remain.
    project_question = re.sub(
        r'(?m)^Arquivo ativo: [^\n]+ · [^\n]+ · \d+ linhas · cursor na linha \d+\.\s*$',
        '', question,
    ).strip()
    if is_project_understanding_request(project_question) or is_project_feedback_request(project_question, messages):
        question = project_question
    previous_goal = restore_followup_goal(messages)
    if previous_goal:
        question = previous_goal + '\nContinuação solicitada: ' + question
    attachments = current.get('attachments', [])
    # Migração de anexos antigos concatenados ao prompt. Os marcadores não
    # determinam ações: são só dados, e não são usados na API estruturada.
    if not attachments and '[Anexo' in question:
        question, material = question.split('[Anexo', 1)
        files = [{'path': path.strip(), 'content': text.strip()} for path, text in
                 re.findall(r'\[Arquivo:\s*([^\]]+)\]\n(.*?)(?=\[Arquivo:|$)', material, re.S)]
        attachments = [{'name': 'anexo da conversa anterior', 'kind': 'directory', 'files': files}]
    text = normalize(question).strip()
    reference = bool(re.search(r'\b(nesse projeto|neste projeto|desse projeto|deste projeto|nessa ideia|nesta ideia|nisso|dele|dela|nele|nela)\b', text) or
                     re.match(r'^(e (em|os|as|o|a|sobre)\b|isso\b|e isso\??$|explique melhor|continue\b|pode continuar\b|como assim\??$)', text))
    if reference and not attachments:
        attachments = next((m['attachments'] for m in reversed(users[:-1]) if m.get('attachments')), [])
    contextual = question
    if reference and len(users) > 1 and not attachments:
        contextual = users[-2].get('content', '') + '\nContinuação: ' + question
    return question.strip(), attachments, contextual.strip()

def is_game_development_request(question):
    """Distingue produção jogável de brainstorming sobre jogos."""
    text = normalize(question).strip()
    game = re.search(
        r'\b(?:jogo|gameplay|game design|level design|phaser|godot|unity|unreal|'
        r'aventura interativa|visual novel|mecanica de jogo|mecânica de jogo)\b',
        text,
    )
    action = re.search(
        r'\b(?:crie|criar|construa|construir|desenvolva|desenvolver|implemente|'
        r'implementar|prototipe|prototipar|monte|montar|transforme|transformar|'
        r'planeje|planejar|faca|fazer)\b',
        text,
    )
    ideation = re.search(r'\b(?:ideias?|conceitos?|brainstorm|sugira|proponha)\b', text)
    deliverable = re.search(
        r'\b(?:jogavel|jogável|prototipo|protótipo|codigo|código|workspace|projeto|'
        r'typescript|javascript|html|canvas|fase|demo)\b',
        text,
    )
    return bool(game and action and (not ideation or deliverable))


def is_workspace_status_request(question):
    text = normalize(str(question or '')).strip()
    workspace_status = bool(re.search(
        r'\b(?:estado atual do sistema|estado atual desse sistema|estado atual do projeto|'
        r'estado do projeto|status do projeto|status do workspace|como esta o sistema|'
        r'como esta o projeto|resumo do projeto ativo)\b', text,
    ))
    political_or_personal_state = bool(re.search(
        r'\b(?:sistema politico|estado brasileiro|estado da bahia|estado de saude|'
        r'governo|instituicoes|politica|politico|saude|emocional)\b', text,
    ))
    return workspace_status and not political_or_personal_state


def route_intent(question, has_attachments=False):
    text = normalize(question).strip()
    if has_attachments:
        return 'attachment-review'
    if is_workspace_status_request(text):
        return 'workspace'
    local_review_request = bool(re.search(
        r'\b(?:revise minhas alteracoes|o que mudou no projeto|o que mudou no codigo|'
        r'resuma as alteracoes locais|revise o diff|analise o diff|mudancas pendentes no git)\b', text,
    ))
    local_environment_issue = bool(re.search(
        r'\b(?:dependencia ausente|dependencias ausentes|dependencia faltando|'
        r'dependencias faltando|problema de configuracao local|diagnostique o ambiente|'
        r'diagnosticar o ambiente|aplicacao nao inicia|servidor nao sobe|porta ja esta ocupada)\b', text,
    ))
    if local_review_request or local_environment_issue:
        return 'workspace'
    if re.fullmatch(r'(oi|ola|bom dia|boa tarde|boa noite|e ai)[!.?, ]*(tudo bem[?.! ]*)?', text) or re.fullmatch(r'(tudo bem|como voce esta|como vai)[?.! ]*', text):
        return 'conversation'
    if re.fullmatch(r'(sim|e|isso|exatamente|pois e|justamente|claro|entendo|ah|ta)[!.?, ]*', text):
        return 'conversation'
    if re.search(r'\b(quem e voce|sobre voce|voce consegue|voce faz)\b', text):
        return 'conversation'
    if re.fullmatch(r'(obrigad[oa]|valeu|perfeito|beleza|legal|otimo|otima|entendi|certo|show|massa)[!.?, ]*', text):
        return 'conversation'
    if re.search(r'\b(?:quais|liste|mostre)\b.{0,35}\b(?:ferramentas|tools)\b', text):
        return 'workspace'
    if is_workspace_identity_question(text) or is_workspace_inventory_question(text):
        return 'workspace'
    if is_project_understanding_request(text):
        return 'workspace'
    if is_project_continuation(text):
        return 'workspace'
    if is_diagnostic_advice_request(text):
        return 'conversation'
    if is_idea_discussion_request(text):
        return 'conversation'
    if is_opinion_request(text):
        return 'conversation'
    if is_advice_request(text):
        return 'conversation'
    if (re.search(r'\bframework\s+[a-z][a-z0-9_.-]*(?:\s+\d+(?:\.\d+)*)?\b.{0,100}\bqual funcao\b', text)
            and re.search(r'\b(?:compilador|modo quantico|quantico)\b', text)):
        return 'conversation'
    if re.search(r'\bcausa exata\b', text) and re.search(r'\b(?:rust|aplicativo|fechar sozinho)\b', text):
        return 'conversation'
    if is_conversational_text(text):
        return 'conversation'
    internal_search = bool(re.search(
        r'\b(?:busque|buscar|pesquise|pesquisar|procure|procurar|localize|localizar|'
        r'encontre|encontrar|ache|achar)\b', text,
    )) and bool(re.search(r'\b(?:no codigo|nos arquivos|no repositorio|no repo|no workspace|'
                          r'em arquivos|no projeto|na pasta)\b', text))
    if internal_search:
        return 'workspace'
    if re.match(r'^(pesquise|busque na internet|procure na internet)\b', text) or re.search(r'https?://\S+', text):
        return 'web-research'
    if re.search(r'\b(noticias?|cotacao|clima|versao atual|preco atual)\b', text):
        return 'current-research'
    workspace_goal = bool(re.search(r'\b(?:workspace|projeto|pasta|diretorio|repositorio|repo)\b', text)) and bool(
        re.search(r'\b(?:quero|preciso|vamos|crie|criar|construa|construir|desenvolva|desenvolver|'
                  r'implemente|implementar|integre|integrar|configure|configurar|corrija|corrigir|'
                  r'analise|analisa|analisar|revise|revisa|revisar|inspecione|inspeciona|inspecionar|'
                  r'checa|cheque|checar|veja|ver|olhe|olhar|examine|examinar|avalie|avaliar|'
                  r'investigue|investigar|mapeie|mapear|diga|verifique|verificar|'
                  r'validar|valide|rode|rodar|execute|executar|compile|compilar)\b', text)
    )
    if workspace_goal:
        return 'workspace'
    # Pedidos de construção em linguagem natural não precisam mencionar
    # ``workspace`` ou ``código`` para serem tarefas de engenharia. Esse
    # sinal vem antes da pesquisa proativa para que frases como "faça um app"
    # entrem no fluxo de requisitos/planejamento.
    natural_build_request = bool(re.search(
        r'\b(?:quero|preciso|vamos|faça|faca|crie|criar|construa|construir|'
        r'desenvolva|desenvolver|implemente|implementar|monte|montar|'
        r'transforme|transformar|converta|converter)\b', text,
    )) and bool(re.search(
        r'\b(?:sistema|app|aplicativo|produto|site|interface|tela|dashboard|'
        r'portal|api|serviço|servico|servidor|ferramenta|programa|plataforma|'
        r'cadastro|pedidos?|clientes?|pagamentos?|entregas?)\b', text,
    ))
    if natural_build_request:
        return 'workspace'
    if re.match(r'^(liste|leia|abra|crie|edite|busque|pesquise|procure|localize|encontre|ache|analisa|analise|checa|cheque|veja|olhe|examine|avalie|inspecione|verifique|execute|rode)\b', text) and re.search(r'\b(arquivo|pasta|workspace|projeto|codigo|repositorio|repo)\b', text):
        return 'workspace'
    if is_game_development_request(text):
        return 'game-development'
    # Termos de planejamento dentro de uma solicitação técnica (por exemplo,
    # "organize a resposta sobre Rust") não devem mascarar a intenção de
    # programação e impedir a pesquisa/documentação da tecnologia citada.
    technical_request = re.search(
        r'\b(api|biblioteca|framework|sdk|rust|python|java|c\+\+|javascript|typescript|react|django|tokio|cargo|npm|sql|sqlx|jwt|postgresql|websocket|codigo|programacao)\b',
        text,
    )
    study_plan_request = (
        re.search(r'\b(?:estud\w*|aprend\w*|pratic\w*)\b', text)
        and re.search(r'\b(?:rotina|cronograma|agenda|plano|planej\w*|organiz\w*|passos)\b', text)
    )
    if study_plan_request:
        return 'planning'
    if re.search(r'\b(planeje|planejar|organize|organizar|checklist|cronograma|rotina|prioridade|priorizar|passos)\b', text) and not technical_request:
        return 'planning'
    if technical_request:
        return 'programming'
    if re.search(r'\b(ideia|ideias|criativo|criativa|roteiro|historia|poema|conto|romance|personagem|jogo|m[eé]canica|conceitos?|letra de musica|slogan|campanha|branding|identidade visual|conceito visual|nomes? para|brainstorm|storyboard)\b', text):
        return 'creative'
    if re.search(r'\b(resuma|resumir|reescreva|redija|escreva|revise|revisar|documento|relatorio|relatório|mensagem|email|e-mail)\b', text):
        return 'work-writing'
    if re.search(r'\b(python|rust|codigo|programacao|funcao|algoritmo|bug|tupla)\b', text):
        return 'programming'
    if re.search(r'\b(o que e|quem e|quem foi|explique|explicar|explica|analogia|diferenca|como funciona|quando|capital)\b', text):
        return 'knowledge'
    return 'unknown'
