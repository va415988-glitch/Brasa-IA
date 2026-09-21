"""Separa instrução, histórico e material citado antes do roteamento."""
import re
import unicodedata


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if unicodedata.category(c) != 'Mn')


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


def route_intent(question, has_attachments=False):
    text = normalize(question).strip()
    if has_attachments:
        return 'attachment-review'
    if re.fullmatch(r'(oi|ola|bom dia|boa tarde|boa noite|e ai)[!.?, ]*(tudo bem[?.! ]*)?', text) or re.fullmatch(r'(tudo bem|como voce esta|como vai)[?.! ]*', text):
        return 'conversation'
    if re.search(r'\b(quem e voce|sobre voce|voce consegue|voce faz)\b', text):
        return 'conversation'
    if re.fullmatch(r'(obrigad[oa]|valeu|perfeito|beleza|legal|otimo|otima|entendi|certo|show|massa)[!.?, ]*', text):
        return 'conversation'
    if re.match(r'^(pesquise|busque na internet|procure na internet)\b', text) or re.search(r'https?://\S+', text):
        return 'web-research'
    if re.search(r'\b(noticias?|cotacao|clima|versao atual|preco atual)\b', text):
        return 'current-research'
    if re.match(r'^(liste|leia|abra|crie|edite|busque)\b', text) and re.search(r'\b(arquivo|pasta|workspace|projeto)\b', text):
        return 'workspace'
    # Termos de planejamento dentro de uma solicitação técnica (por exemplo,
    # "organize a resposta sobre Rust") não devem mascarar a intenção de
    # programação e impedir a pesquisa/documentação da tecnologia citada.
    technical_request = re.search(
        r'\b(api|biblioteca|framework|sdk|rust|python|java|c\+\+|javascript|typescript|react|django|tokio|cargo|npm|sql|sqlx|jwt|postgresql|websocket|codigo|programacao)\b',
        text,
    )
    if re.search(r'\b(planeje|planejar|organize|organizar|checklist|cronograma|rotina|prioridade|priorizar|passos)\b', text) and not technical_request:
        return 'planning'
    if technical_request:
        return 'programming'
    if re.search(r'\b(ideia|ideias|criativo|criativa|roteiro|historia|poema|conto|romance|personagem|letra de musica|slogan|campanha|branding|identidade visual|conceito visual|nomes? para|brainstorm|storyboard)\b', text):
        return 'creative'
    if re.search(r'\b(resuma|resumir|reescreva|redija|escreva|revise|revisar|documento|relatorio|relatório|mensagem|email|e-mail)\b', text):
        return 'work-writing'
    if re.search(r'\b(python|rust|codigo|programacao|funcao|algoritmo|bug|tupla)\b', text):
        return 'programming'
    if re.search(r'\b(o que e|quem e|quem foi|explique|diferenca|como funciona|quando|capital)\b', text):
        return 'knowledge'
    return 'unknown'
