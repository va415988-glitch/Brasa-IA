"""Planejador local orientado a contratos.

O planejador não executa ferramentas. Ele ranqueia as ferramentas disponíveis,
extrai argumentos e devolve uma chamada tipada. A separação permite substituir
esta camada por um modelo neural treinado em traces sem alterar o runtime, a UI
ou o ACP.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from dialogue import is_project_continuation, is_workspace_identity_question, is_workspace_inventory_question, normalize
from document_reading import DOCUMENT_READER_EXTENSIONS, document_read_tool, mentioned_document_paths
from tool_registry import ToolRegistry, make_tool_call
from task_graph import TaskGraphPlanner


DOCUMENT_EXTENSIONS = "|".join(
    re.escape(extension.removeprefix("."))
    for extension in sorted(DOCUMENT_READER_EXTENSIONS, key=len, reverse=True)
)

PROJECT_INSPECTION_PHRASES = (
    "analise o projeto", "analisa o projeto", "analise este projeto", "analisa este projeto",
    "analise o workspace", "analisa o workspace", "inspecione o workspace", "inspeciona o workspace",
    "revise o workspace", "revisa o workspace", "inspecione a estrutura", "inspeciona a estrutura",
    "analise o repositorio", "analisa o repositorio", "checa o repositorio", "cheque o repositorio",
    "checa o repo", "cheque o repo", "veja o repositorio", "veja o repo", "olhe o repositorio",
    "olhe o repo", "examine o repositorio", "examine o repo", "avalie o repositorio", "avalie o repo",
    "identifique os pontos de entrada",
)


def _features(text: str) -> list[str]:
    normalized = normalize(text)
    words = re.findall(r"[\wÀ-ÿ]{2,}", normalized)
    compact = re.sub(r"\s+", " ", normalized)
    grams = [compact[index:index + 4] for index in range(max(0, len(compact) - 3))]
    return words + [f"#4:{gram}" for gram in grams if " " not in gram]


def learned_feature_score(text: str, tool: str, artifact: dict) -> float:
    """Score learned evidence with the exact bounded rule used at runtime."""
    stats = (artifact.get("tools") or {}).get(tool, {})
    features = stats.get("features") or {}
    if not features:
        return 0.0
    matches = sum(1 for feature in set(_features(text)) if feature in features)
    return min(0.75, matches * 0.05)


class AgentPlanner:
    # Vocabulário de acionamento fica separado dos executores. Novas ferramentas
    # podem receber aliases aqui sem alterar o ciclo de execução.
    aliases = {
        "research_web": ("pesquise", "pesquisar", "busque na internet", "procure na internet", "fontes", "web"),
        "search_web": ("busque links na web", "busque resultados na web", "encontre páginas na internet", "encontre links na internet"),
        "list_sources": ("liste as fontes", "mostre as fontes", "quais fontes foram consultadas", "fontes desta sessão"),
        "open_page": ("abra a pagina", "leia a pagina", "analise a pagina", "url"),
        "list_files": ("liste os arquivos", "listar arquivos", "mostre os arquivos", "pastas"),
        "path_info": ("metadados do arquivo", "tamanho do arquivo", "tipo do arquivo", "esse arquivo existe"),
        "find_paths": ("encontre arquivos", "localize arquivos", "procure arquivos por nome", "busque arquivos por nome"),
        "list_tree": ("arvore de arquivos", "arvore do projeto", "estrutura de diretorios"),
        "compare_files": ("compare os arquivos", "comparar arquivos", "compare dois arquivos"),
        "git_diff": ("git diff", "diff do git", "diff das alteracoes"),
        "inspect_code": ("inspecione o codigo", "inspecione os simbolos", "liste as funcoes", "mostre as classes"),
        "code_references": ("onde e usada", "onde e usado", "quem chama", "quem usa", "referencias de", "usos de"),
        "change_impact": ("impacto da mudanca", "impacto da alteracao", "o que pode quebrar", "o que quebra se"),
        "discover_tests": ("quais testes existem", "descubra os testes", "liste os testes", "arquivos sem teste", "o que nao tem teste"),
        "security_scan": ("revisao de seguranca", "audite a seguranca", "vulnerabilidades no codigo", "falhas de seguranca no codigo", "segredos no codigo"),
        "dependency_audit": ("audite as dependencias", "auditoria de dependencias", "dependencias sem versao", "revise as dependencias"),
        "list_tools": ("quais ferramentas", "liste as ferramentas", "ferramentas disponiveis"),
        "create_workspace": ("crie um workspace", "crie novo workspace", "crie um projeto em"),
        "read_file": ("leia o arquivo", "ler arquivo", "abra o arquivo", "mostre o arquivo"),
        "search_files": ("busque no codigo", "buscar no codigo", "procure nos arquivos", "pesquise nos arquivos", "localize no codigo", "encontre no codigo", "ache no codigo"),
        "project_checks": ("rode os testes", "execute os testes", "verifique o projeto", "validar o projeto", "verifique o workspace", "rode a verificacao", "execute a verificacao", "verificacao automatizada", "checks do projeto"),
        "terminal_run": ("execute no terminal", "executar no terminal", "rode no terminal", "rode o comando", "execute o comando", "rode o script", "execute o script", "compile no terminal"),
        "process_start": ("inicie o servidor", "iniciar o servidor", "inicie servidor", "iniciar servidor", "suba o servidor", "subir o servidor", "rode o servidor", "rodar o servidor", "inicie a aplicação", "inicie a aplicacao", "rode a aplicação", "rode a aplicacao", "execute a aplicação local", "execute a aplicacao local", "inicie o projeto"),
        "process_status": ("status do servidor", "estado do servidor", "como está o servidor", "como esta o servidor", "servidor está ativo", "servidor esta ativo", "servidor está rodando", "servidor esta rodando", "mostre os logs do servidor", "logs do processo", "verifique se a aplicação está pronta", "verifique se a aplicacao esta pronta"),
        "process_stop": ("pare o servidor", "parar o servidor", "pare servidor", "parar servidor", "encerre o servidor", "desligue o servidor", "pare a aplicação", "pare a aplicacao", "encerre a aplicação", "encerre a aplicacao"),
        "apply_batch": ("aplique o lote", "aplique o lote de alterações", "aplique o lote de alteracoes", "aplique estas mudanças em conjunto", "aplique estas mudancas em conjunto", "aplique as alterações em vários arquivos", "aplique as alteracoes em varios arquivos"),
        "undo_batch": ("desfaça o lote", "desfaca o lote", "desfazer o lote", "desfaça a última alteração em lote", "desfaca a ultima alteracao em lote", "desfaça alterações em lote", "desfaca alteracoes em lote"),
        "inspect_project": PROJECT_INSPECTION_PHRASES + ("inspecione o projeto", "inspeciona o projeto", "revise o projeto", "revisa o projeto", "continue o projeto", "continue projeto", "continuar o projeto", "retome o projeto", "prossiga com o projeto"),
        "set_workspace": ("selecione o projeto", "selecione a pasta", "abra o workspace", "defina o workspace", "workspace local"),
        "inspect_media": ("inspecione a imagem", "inspecionar mídia", "inspecione a mídia", "metadados da mídia"),
        "extract_document_text": ("extraia o pdf", "extraia o texto", "leia o pdf", "leia o documento", "leia a planilha", "leia a apresentação", "texto do documento", "resuma o documento", "analise o documento"),
        "create_web_page": ("pagina", "site", "landing page", "tela", "interface", "dashboard", "formulario"),
        "create_file": ("crie o arquivo", "criar arquivo", "gere o arquivo"),
        "create_directory": ("crie a pasta", "criar pasta", "monte a pasta"),
    }
    action_words = ("continue", "continuar", "prossiga", "prosseguir", "retome", "retomar", "avance", "avancar", "cria", "crie", "monta", "monte", "construa", "desenvolva", "gere", "gera", "faca", "transforme", "transformar", "altere", "alterar", "melhore", "melhorar", "modifique", "modificar")

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self.graph = TaskGraphPlanner()
        self.learned = {}
        artifact = Path(__file__).resolve().parent.parent / "model" / "planner" / "planner_index.json"
        try:
            self.learned = json.loads(artifact.read_text(encoding="utf-8")) if artifact.exists() else {}
        except (OSError, json.JSONDecodeError):
            self.learned = {}

    @staticmethod
    def _phrase_score(text: str, phrase: str) -> float:
        if " " in phrase:
            return 3.0 if phrase in text else 0.0
        return 1.0 if re.search(rf"\b{re.escape(phrase)}\b", text) else 0.0

    def _learned_score(self, text: str, tool: str) -> float:
        """Pontuação pequena aprendida; contratos continuam sendo a autoridade."""
        # Mantém o reranker abaixo da força de evidência explícita do contrato
        # e evita que um corpus pequeno substitua a validação.
        return learned_feature_score(text, tool, self.learned)

    def scores(self, question: str) -> list[tuple[float, str]]:
        text = normalize(question)
        scored = []
        for tool in self.registry.tools:
            score = sum(self._phrase_score(text, phrase) for phrase in self.aliases.get(tool, ()))
            score += self._learned_score(text, tool)
            description = normalize(str(self.registry.tools[tool].get("description", "")))
            score += sum(0.25 for word in set(re.findall(r"[\wÀ-ÿ]{4,}", text)) if word in description)
            has_action = any(word in text for word in self.action_words)
            has_visual = any(phrase in text for phrase in self.aliases["create_web_page"])
            has_project_analysis = any(phrase in text for phrase in PROJECT_INSPECTION_PHRASES)
            asks_tests = bool(
                re.search(r"\b(?:test(?:s|ing)?|teste(?:s)?|pytest|unittest|cargo\s+test|npm\s+test|check(?:s)?|verificacao\s+automatizada)\b", text)
                or re.search(r"\b(?:verifique|verificar|valide|validar)\s+(?:o\s+|a\s+)?(?:projeto|workspace)\b", text)
            )
            internal_search = bool(re.search(r"\b(?:c[oó]digo|arquivos?|workspace|projeto|repositorio)\b", text)) and bool(re.search(r"\b(?:busque|buscar|pesquise|pesquisar|procure|procurar|localize|localizar|encontre|encontrar|ache|achar)\b", text))
            creative_request = bool(re.search(
                r"\b(?:conceitos?|jogo|m[eé]canica|hist[oó]ria|poema|roteiro|slogan|campanha|branding|brainstorm|personagem)\b",
                text,
            )) and not has_visual
            if tool == "research_web" and internal_search:
                score = 0.0
            workspace_path = self._workspace_path(question)
            if workspace_path and tool == "set_workspace":
                score += 8.0
            elif workspace_path and tool in {"list_files", "inspect_project"}:
                score -= 2.0
            if tool == "search_files" and internal_search:
                score += 3.0
            # Um caminho de arquivo explícito é evidência mais forte que a
            # palavra genérica "projeto". Sem este reforço, pedidos como
            # "Leia src/app.py" podiam cair em inspect_project.
            explicit_file_mentions = re.findall(r"(?<![\w./-])[\w.-]+(?:/[\w.-]+)*\.[\w]+", question)
            document_mention = re.search(rf"(?<![\w./-])[\w.-]+(?:/[\w.-]+)*\.(?:{DOCUMENT_EXTENSIONS})(?![\w])", question, flags=re.I)
            asks_to_read = bool(re.search(r"\b(?:leia|ler|abra|abrir|mostre|mostrar|resuma|resumir|analise|analisar|extraia|extrair|interprete|interpretar|compare|comparar)\b", text))
            if tool == "path_info" and explicit_file_mentions and asks_to_read:
                # A palavra "tamanho" dentro de uma pergunta sobre o conteúdo
                # de um arquivo não deve trocar a leitura pelo stat do caminho.
                score = 0.0
            if tool == "read_file" and self._file(question) and len(explicit_file_mentions) == 1:
                score += 4.0
            if document_mention and asks_to_read and len(explicit_file_mentions) == 1:
                preferred_reader = document_read_tool(document_mention.group(0))
                if tool == preferred_reader:
                    score += 9.0
                elif tool in {"read_file", "extract_document_text"}:
                    score = 0.0
            if len(explicit_file_mentions) > 1:
                if tool == "list_files":
                    score += 3.0
                elif tool == "read_file":
                    score -= 1.0
            if tool == "create_web_page" and has_action and has_visual:
                score += 4.0
            if tool == "project_checks" and has_visual and has_action:
                score -= 2.0
            # Verificação só pode ser escolhida quando foi pedida de forma
            # explícita. Palavras como "projeto" aparecem em quase toda tarefa
            # de edição e não devem iniciar um ciclo de testes/diagnóstico.
            if tool == "project_checks" and not asks_tests:
                score = 0.0
            if tool == "terminal_run":
                explicit_run = bool(re.search(r"\b(?:execute|executar|rode|rodar|inicie|iniciar|compile|compilar)\b", text))
                has_command = bool(re.search(r"`[^`\n]+`", question) or re.search(r"```[^\n]*\n", question)
                                   or re.search(r"\b(?:comando|terminal)\s*:\s*\S", question, re.I))
                if not explicit_run or not has_command:
                    score = 0.0
                else:
                    score += 5.0
            if tool == "inspect_project" and is_project_continuation(question):
                score += 8.0
            if is_workspace_identity_question(question):
                if tool == "inspect_project":
                    score += 12.0
                elif tool in {"search_web", "research_web", "open_page"}:
                    score = 0.0
            if is_workspace_inventory_question(question):
                if tool == "list_files":
                    score += 12.0
                elif tool in {"search_web", "research_web", "open_page"}:
                    score = 0.0
            if tool == "inspect_project" and has_project_analysis and asks_tests:
                score += 2.0
            if tool == "project_checks" and has_project_analysis:
                score -= 1.0
            if tool == "inspect_project" and has_project_analysis:
                score += 3.0
            if creative_request and tool in {"create_web_page", "create_file", "create_directory", "project_checks", "diagnose_project"}:
                score = 0.0
            if tool == "create_web_page" and not any(word in text for word in self.action_words):
                score = 0.0
            if tool in {"create_file", "create_directory"} and not any(word in text for word in self.action_words):
                score = 0.0
            if tool == "open_page" and not re.search(r"https?://\S+", question) and "url" not in text:
                score -= 1.0
            if tool == "open_page" and re.search(r"https?://\S+", question):
                score += 5.0
            # Pedidos de leitura de mídia/documento são específicos e não
            # devem competir com busca de código ou conversa genérica.
            if tool == "inspect_media" and re.search(r"\b(?:imagem|audio|áudio|video|vídeo|m[ií]dia)\b", text):
                score += 3.0
            if tool == "extract_document_text" and re.search(r"\b(?:pdf|documento|extrair texto)\b", text):
                score += 3.0
            if score > 0:
                scored.append((score, tool))
        return sorted(scored, reverse=True)

    @staticmethod
    def _file(question: str, verbs: str = r"leia|ler|abra|abrir|mostre|mostrar"):
        return re.search(rf"\b(?:{verbs})\s+(?:o\s+)?(?:arquivo\s+)?`?([\w./-]+\.[\w]+)`?", question, flags=re.I)

    @staticmethod
    def _workspace_path(question: str) -> str | None:
        quoted = re.search(r'["“](/[^"”\n]+)["”]', question)
        if quoted:
            return quoted.group(1).strip().rstrip('.,;')
        match = re.search(
            r'\b(?:workspace(?:\s+local)?|projeto|pasta|diret[oó]rio)\s*'
            r'(?:em|no caminho|:)?\s*(/[^\n]+?)(?=\s+(?:e|para|com|onde)\b|[.!?]?\s*$)',
            question,
            flags=re.I,
        )
        return match.group(1).strip().rstrip('.,;') if match else None

    @staticmethod
    def relative_folder(question: str) -> str | None:
        match = re.search(r"\b(?:na|no|da|do)\s+(?:pasta|diret[oó]rio)\s+`?([\w./-]+)`?", question, re.I)
        if not match:
            return None
        folder = match.group(1).rstrip('.,;:!?')
        if folder.lower() in {'atual', 'ativo', 'selecionado', 'selecionada'} or folder.startswith('/') or '..' in Path(folder).parts:
            return None
        return folder

    @staticmethod
    def workspace_read_request(question: str) -> tuple[str, dict] | None:
        """Escolhe consultas locais explícitas antes de qualquer pesquisa externa."""
        text = normalize(question)
        if re.search(r"\b(?:quais|liste|mostre)\b.{0,35}\b(?:ferramentas|tools)\b", text):
            return 'list_tools', {}
        if re.search(r"\b(?:crie|criar|edite|editar|apague|apagar|remova|remover|implemente|implementar)\b", text):
            return None
        if re.search(r"\b(?:inspecione|inspecionar|liste|listar|mostre)\b.{0,35}\b(?:simbolos|imports|funcoes|classes)\b", text):
            quoted = re.findall(r"`([^`\n]+)`", question)
            paths = quoted or mentioned_document_paths(question)
            return 'inspect_code', {'path': paths[0] if paths else ''}
        if re.search(r"\b(?:revisao\s+de\s+seguranca|audite\s+a\s+seguranca|"
                     r"(?:vulnerabilidades|falhas\s+de\s+seguranca)\s+(?:no|do|neste|nesse|deste|desse)\s+(?:codigo|projeto|repositorio|workspace)|"
                     r"segredos\s+(?:no|nos|expostos)|chaves\s+expostas|senhas\s+no\s+codigo)\b", text):
            return 'security_scan', {'path': AgentPlanner.relative_folder(question) or '', 'min_severity': 'low'}
        if re.search(r"\b(?:audite|auditoria|revise|verifique)\b.{0,30}\b(?:dependencias|lockfiles?)\b", text):
            return 'dependency_audit', {}
        if re.search(r"\b(?:quais|liste|mostre|descubra|encontre)\b.{0,30}\btestes\b|\b(?:sem|nao\s+tem)\s+testes?\b", text) \
                and not re.search(r"\b(?:rode|rodar|execute|executar|passe|passar|falh\w*|quebr\w*|erros?)\b", text):
            return 'discover_tests', {}
        symbol = re.search(r"`([A-Za-z_$][\w$]*(?:(?:\.|::)[A-Za-z_$][\w$]*)*)`", question) or re.search(
            r"\b(?:fun[cç][aã]o|m[eé]todo|classe|s[ií]mbolo|vari[aá]vel)\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)", question)
        if re.search(r"\b(?:impacto|o\s+que\s+(?:pode\s+)?quebra[r]?)\b", text):
            quoted = re.findall(r"`([^`\n]+\.[A-Za-z0-9]+)`", question) or re.findall(
                r"(?<![\w./-])[\w.-]+(?:/[\w.-]+)*\.(?:py|[cm]?[jt]sx?|rs|go|java|kt|rb|php|c|cc|cpp|h|hpp|cs|swift)\b", question)
            if quoted or symbol:
                return 'change_impact', {**({'path': quoted[0]} if quoted else {}),
                                         **({'symbol': symbol.group(1)} if symbol and not quoted else {})}
        if symbol and re.search(r"\b(?:onde|quem)\b.{0,30}\b(?:usa|usad[oa]|chama|chamad[oa]|referencia)\b|\b(?:referencias|usos)\s+(?:de|da|do)\b", text):
            return 'code_references', {'symbol': symbol.group(1)}
        if re.search(r"\b(?:git\s+diff|diff\s+do\s+git|diff\s+das\s+alteracoes)\b", text):
            return 'git_diff', {}
        if re.search(r"\bcompar(?:e|ar)\b", text) and re.search(r"\barquivos?\b", text):
            paths = re.findall(r"`([^`\n]+)`", question)
            if len(paths) < 2:
                paths = re.findall(r"(?<![\w./-])[\w.-]+(?:/[\w.-]+)*\.[\w]+", question)
            if len(paths) >= 2:
                return 'compare_files', {'left': paths[0], 'right': paths[1]}
        if re.search(r"\b(?:metadados|tamanho|tipo|existe|informacoes)\b", text) and re.search(r"\b(?:arquivo|pasta|caminho|diretorio)\b", text):
            quoted = re.findall(r"`([^`\n]+)`", question)
            named = re.search(r"\b(?:arquivo|pasta|caminho|diret[oó]rio)\s+([\w./-]+)", question, re.I)
            path = quoted[0] if quoted else named.group(1) if named else None
            if path and path not in {'atual', 'existe'}:
                return 'path_info', {'path': path}
        if re.search(r"\b(?:encontre|encontrar|localize|localizar|procure|procurar|busque|buscar)\b", text) and re.search(r"\b(?:arquivos?|pastas?|caminhos?|nomes?)\b", text):
            quoted = re.findall(r"`([^`\n]+)`", question)
            glob = re.search(r"(?<!\w)([\w.\-/]*[?*][\w.\-/?*]*)", question)
            named = re.search(r"(?<![\w./-])[\w.-]+(?:/[\w.-]+)*\.[\w]+", question)
            pattern = glob.group(1) if glob else quoted[0] if quoted else named.group(0) if named else None
            if pattern and len(pattern) <= 120:
                return 'find_paths', {'pattern': pattern, 'path': AgentPlanner.relative_folder(question) or '', 'max_results': 100}
        if re.search(r"\b(?:arvore|estrutura\s+de\s+diretorios)\b", text) and re.search(r"\b(?:arquivos?|pastas?|workspace|projeto|repositorio|repo|diretorios?)\b", text):
            return 'list_tree', {'path': AgentPlanner.relative_folder(question) or '', 'max_depth': 3, 'max_entries': 200}
        return None

    def arguments(self, tool: str, question: str) -> dict | None:
        original = question.strip()
        text = normalize(original)
        if tool == "open_page":
            match = re.search(r"https?://\S+", original)
            return {"url": match.group(0).rstrip(".,)")} if match else None
        if tool == "search_web":
            query = re.sub(
                r"^\s*(?:busque|buscar|procure|procurar|encontre|encontrar|pesquise|pesquisar)\s+",
                "", original, flags=re.I,
            )
            query = re.sub(r"\b(?:links?|resultados?|p[aá]ginas?|sites?)\b", " ", query, flags=re.I)
            query = re.sub(r"\b(?:na|no|da|do|pela|pelo)\s+(?:web|internet)\b", " ", query, flags=re.I)
            query = re.sub(r"\bpor favor\b", " ", query, flags=re.I)
            query = re.sub(r"^\s*(?:sobre|de|para)\s+", "", query, flags=re.I)
            query = re.sub(r"\s+", " ", query).strip(" \t:;,.!?")
            return {"query": query} if query else None
        if tool == "list_sources":
            return {}
        if tool == "cite_sources":
            source_ids = list(dict.fromkeys(re.findall(r"\bweb-\d+\b", original, flags=re.I)))
            return {"source_ids": source_ids} if source_ids else None
        if tool == "research_web":
            query = re.sub(r"^(?:pesquise|pesquisar|busque|procure)(?:\s+na internet|\s+na web|\s+fontes?)?\s*", "", original, flags=re.I).strip(" :")
            return {"query": query, "max_results": 2, "save_to_corpus": bool(re.search(r"\b(?:salve|salvar|acervo|corpus|aprenda)\b", text))} if query else None
        if tool in {"path_info", "find_paths", "list_tree", "compare_files", "git_diff", "inspect_code", "list_tools",
                    "code_references", "change_impact", "discover_tests", "security_scan", "dependency_audit"}:
            request = self.workspace_read_request(question)
            return request[1] if request and request[0] == tool else None
        if tool == "list_files":
            if is_workspace_inventory_question(question):
                return {"path": self.relative_folder(question) or "", "include_hidden": True, "max_entries": 200}
            return {"path": self.relative_folder(question) or ""}
        if tool == "read_file":
            match = self._file(original)
            return {"path": match.group(1)} if match else None
        if tool == "search_files":
            quoted_term = re.search(r"`([^`\n]+)`", original)
            query_match = re.search(r"\b(?:por|pelo|pela|sobre)\s+(.+)$", original, flags=re.I)
            query = quoted_term.group(1).strip() if quoted_term else query_match.group(1).strip() if query_match else re.sub(r".*?\b(?:c[oó]digo|arquivos?|workspace|projeto)\b\s*(?:em)?\s*[:\-]?\s*", "", original, flags=re.I).strip() or original
            folder = self.relative_folder(question)
            if folder:
                query = re.sub(r"\s+\b(?:na|no|da|do)\s+(?:pasta|diret[oó]rio)\s+`?[\w./-]+`?.*$", "", query, flags=re.I).strip()
            return {"query": query or original, **({"path": folder} if folder else {})}
        if tool == "project_checks":
            return {"check": "auto"}
        if tool == "terminal_run":
            fenced = re.search(r"```[^\n]*\n([\s\S]*?)```", original)
            inline = re.search(r"`([^`\n]+)`", original)
            labeled = re.search(r"\b(?:comando|terminal)\s*:\s*([^\n]+)", original, re.I)
            raw = (fenced.group(1).strip() if fenced else inline.group(1).strip() if inline
                   else labeled.group(1).strip() if labeled else "")
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
            if len(lines) != 1:
                return None
            try:
                parts = tuple(shlex.split(lines[0], posix=True))
            except ValueError:
                return None
            # Comandos aceitos formam uma lista fechada. Não encaminhamos
            # executáveis, flags, caminhos ou expressões do usuário ao shell.
            profiles = {
                ("git", "status"): {"operation": "git_status"},
                ("git", "status", "--short"): {"operation": "git_status"},
                ("git", "diff", "--stat"): {"operation": "git_diff_stat"},
                ("cargo", "test"): {"operation": "project_check", "check": "cargo-test"},
                ("cargo", "test", "--all-targets"): {"operation": "project_check", "check": "cargo-test"},
                ("npm", "test"): {"operation": "project_check", "check": "npm-test"},
                ("npm", "run", "check"): {"operation": "project_check", "check": "npm-check"},
                ("pytest",): {"operation": "project_check", "check": "pytest"},
                ("pytest", "-q"): {"operation": "project_check", "check": "pytest"},
                ("python", "-m", "pytest"): {"operation": "project_check", "check": "pytest"},
                ("python3", "-m", "pytest"): {"operation": "project_check", "check": "pytest"},
                ("python", "-m", "unittest"): {"operation": "project_check", "check": "unittest"},
                ("python3", "-m", "unittest"): {"operation": "project_check", "check": "unittest"},
            }
            return profiles.get(parts)
        if tool == "process_start":
            return {"profile": "auto-dev"}
        if tool in {"process_status", "process_stop"}:
            return {}
        if tool == "apply_batch":
            candidates = re.findall(r"```(?:json)?\s*([\s\S]*?)```", original, flags=re.I)
            decoder = json.JSONDecoder()
            for candidate in candidates or [original]:
                for index, character in enumerate(candidate):
                    if character != "{":
                        continue
                    try:
                        value, _ = decoder.raw_decode(candidate[index:])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict) and isinstance(value.get("operations"), list):
                        return {"operations": value["operations"]}
            return None
        if tool == "undo_batch":
            match = re.search(r"\bbatch-[a-f0-9]{24}\b", text, flags=re.I)
            return {"transaction_id": match.group(0)} if match else {}
        if tool == "inspect_project":
            return {"max_depth": 4}
        if tool == "set_workspace":
            path = self._workspace_path(original)
            return {"path": path} if path else None
        if tool == "extract_document_text":
            quoted = re.search(r'["“]([^"”\n]+?\.(?:' + DOCUMENT_EXTENSIONS + r'))["”]', original, flags=re.I)
            match = quoted or re.search(r"(?<![\w./-])([\w.-]+(?:/[\w.-]+)*\.(?:" + DOCUMENT_EXTENSIONS + r")(?![\w]))", original, flags=re.I)
            return {"path": match.group(1)} if match else None
        if tool == "inspect_media":
            match = re.search(r"(?:m[ií]dia|imagem|[áa]udio|v[ií]deo|pdf|documento|arquivo)\s+([\w./-]+)", original, flags=re.I)
            return {"path": match.group(1)} if match else None
        if tool == "create_web_page":
            login = bool(re.search(r"\b(?:login|entrar|acesso)\b", text))
            # O runtime resolve colisões de nome de forma segura. O caminho
            # continua determinístico para projetos novos, mas não força o
            # agente a falhar quando a prévia padrão já existe.
            return {"prompt": original, "path": "preview/login.html" if login else "preview/index.html", "title": "Tela de login" if login else "Protótipo local"}
        if tool == "create_file":
            match = re.search(r"\b(?:crie|criar|gere|gerar)\s+(?:o\s+)?arquivo\s+([\w./-]+)\s*(?::|\n)\s*(?:conte[uú]do\s*:\s*)?([\s\S]+)", original, flags=re.I)
            return {"path": match.group(1), "content": match.group(2).strip()} if match else None
        if tool == "create_directory":
            match = re.search(r"\b(?:crie|criar|monte|montar)\s+(?:(?:uma|a)\s+)?pasta\s+([\w./-]+)", original, flags=re.I)
            return {"path": match.group(1)} if match else None
        return None

    def file_plan(self, question: str) -> list[dict]:
        """Exact user-supplied files; never invent content for a vague app request."""
        if not re.match(r'^\s*(?:crie|cria|gere)\b', normalize(question)):
            return []
        blocks = re.findall(r'^#{1,3}\s+([^\n]+)\n```[^\n]*\n([\s\S]*?)\n```\s*$', question, re.M)
        if not blocks:
            direct = self.arguments('create_file', question)
            if not direct:
                return []
            path = direct['path']
            if path.startswith('/') or '..' in Path(path).parts or '\\' in path:
                return []
            return [make_tool_call(
                self.registry, 'create_file', direct,
                'Criar o arquivo com o caminho e o conteúdo explicitamente fornecidos no pedido.',
            )]
        if not 2 <= len(blocks) <= 10:
            return []
        calls = []
        paths = set()
        for raw_path, content in blocks:
            path = raw_path.strip().strip('`')
            if path in paths or path.startswith('/') or '..' in Path(path).parts or '\\' in path:
                return []
            paths.add(path)
            calls.append(make_tool_call(self.registry, 'create_file', {'path': path, 'content': content}, 'Criar o arquivo com o conteúdo explicitamente fornecido no pedido.'))
        return calls

    def plan(self, question: str, allowed_tools: set[str] | None = None) -> dict | None:
        normalized = normalize(question)
        has_command_literal = bool(
            re.search(r"`[^`\n]+`", question)
            or re.search(r"```[^\n]*\n", question)
            or re.search(r"\b(?:comando|terminal)\s*:\s*\S", question, re.I)
        )
        explicitly_runs_command = bool(re.search(r"\b(?:execute|executar|rode|rodar|run)\b", normalized))
        mentions_shell = bool(re.search(r"\b(?:terminal|bash|shell|comando)\b", normalized))
        if explicitly_runs_command and mentions_shell and has_command_literal:
            # Uma ferramenta externa de shell genérico não deve virar busca,
            # verificação de projeto ou outra ação quando o perfil local não
            # reconhece o comando exato.
            if self.arguments("terminal_run", question) is None:
                return None

        ranked = self.scores(question)
        if allowed_tools is not None:
            ranked = [(score, tool) for score, tool in ranked if tool in allowed_tools]
        for index, (score, tool) in enumerate(ranked):
            # Sinais aprendidos e sobreposição de descrições são apoio, não
            # evidência suficiente para disparar uma ferramenta por conta própria.
            if score < 1.0:
                continue
            arguments = self.arguments(tool, question)
            if arguments is not None and self.registry.has(tool):
                next_score = ranked[index + 1][0] if index + 1 < len(ranked) else 0.0
                margin = max(0.0, score - next_score)
                confidence = min(0.99, max(0.35, 0.5 + margin / max(score, 1.0) * 0.5))
                call = make_tool_call(self.registry, tool, arguments, f"Ferramenta selecionada por evidência: {tool} ({score:.2f}).")
                call["planner"] = {
                    "strategy": "contract-ranking",
                    "score": round(score, 2),
                    "margin": round(margin, 2),
                    "confidence": round(confidence, 2),
                    "candidates": [name for _, name in ranked[:4]],
                    "candidate_scores": [{"tool": name, "score": round(value, 2)} for value, name in ranked[:4]],
                    "task_graph": self.graph.build(question, tool),
                }
                return call
        return None
