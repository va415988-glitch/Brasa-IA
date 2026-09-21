"""Planejador local orientado a contratos.

O planejador não executa ferramentas. Ele ranqueia as ferramentas disponíveis,
extrai argumentos e devolve uma chamada tipada. A separação permite substituir
esta camada por um modelo neural treinado em traces sem alterar o runtime, a UI
ou o ACP.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from dialogue import normalize
from tool_registry import ToolRegistry, make_tool_call
from task_graph import TaskGraphPlanner


def _features(text: str) -> list[str]:
    normalized = normalize(text)
    words = re.findall(r"[\wÀ-ÿ]{2,}", normalized)
    compact = re.sub(r"\s+", " ", normalized)
    grams = [compact[index:index + 4] for index in range(max(0, len(compact) - 3))]
    return words + [f"#4:{gram}" for gram in grams if " " not in gram]


class AgentPlanner:
    # Vocabulário de acionamento fica separado dos executores. Novas ferramentas
    # podem receber aliases aqui sem alterar o ciclo de execução.
    aliases = {
        "research_web": ("pesquise", "pesquisar", "busque na internet", "procure na internet", "fontes", "web"),
        "open_page": ("abra a pagina", "leia a pagina", "analise a pagina", "url"),
        "list_files": ("liste os arquivos", "listar arquivos", "mostre os arquivos", "pastas", "workspace"),
        "read_file": ("leia o arquivo", "ler arquivo", "abra o arquivo", "mostre o arquivo"),
        "search_files": ("busque no codigo", "buscar no codigo", "procure nos arquivos", "pesquise nos arquivos"),
        "project_checks": ("rode os testes", "execute os testes", "verifique o projeto", "validar o projeto"),
        "inspect_project": ("analise o projeto", "inspecione o projeto", "revise o workspace"),
        "set_workspace": ("selecione o projeto", "selecione a pasta", "abra o workspace", "defina o workspace"),
        "inspect_media": ("inspecione a imagem", "inspecionar mídia", "inspecione a mídia", "metadados da mídia"),
        "extract_document_text": ("extraia o pdf", "extraia o texto", "leia o pdf", "texto do documento"),
        "create_web_page": ("pagina", "site", "landing page", "tela", "interface", "dashboard", "formulario"),
        "create_file": ("crie o arquivo", "criar arquivo", "gere o arquivo"),
        "create_directory": ("crie a pasta", "criar pasta", "monte a pasta"),
    }
    action_words = ("cria", "crie", "monta", "monte", "construa", "desenvolva", "gere", "gera", "faca", "transforme", "transformar", "altere", "alterar", "melhore", "melhorar", "modifique", "modificar")

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
        stats = self.learned.get("tools", {}).get(tool, {})
        features = stats.get("features", {})
        if not features:
            return 0.0
        matches = sum(1 for feature in set(_features(text)) if feature in features)
        # Mantém o reranker abaixo da força de uma evidência explícita do
        # contrato e evita que um corpus pequeno substitua a validação.
        return min(0.75, matches * 0.05)

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
            has_project_analysis = any(phrase in text for phrase in ("analise o projeto", "inspecione o projeto", "revise o workspace"))
            asks_tests = bool(re.search(r"\b(?:teste|testes|verifique|valid[ae]|rode|execute)\b", text))
            internal_search = bool(re.search(r"\b(?:c[oó]digo|arquivos?|workspace|projeto)\b", text)) and bool(re.search(r"\b(?:busque|buscar|pesquise|pesquisar|procure)\b", text))
            if tool == "research_web" and internal_search:
                score = 0.0
            if tool == "search_files" and internal_search:
                score += 3.0
            if tool == "create_web_page" and has_action and has_visual:
                score += 4.0
            if tool == "project_checks" and has_visual and has_action:
                score -= 2.0
            # Verificação só pode ser escolhida quando foi pedida de forma
            # explícita. Palavras como "projeto" aparecem em quase toda tarefa
            # de edição e não devem iniciar um ciclo de testes/diagnóstico.
            if tool == "project_checks" and not asks_tests:
                score = 0.0
            if tool == "inspect_project" and has_project_analysis and asks_tests:
                score += 2.0
            if tool == "project_checks" and has_project_analysis:
                score -= 1.0
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
        return re.search(rf"\b(?:{verbs})\s+(?:o\s+)?(?:arquivo\s+)?([\w./-]+\.[\w]+)", question, flags=re.I)

    def arguments(self, tool: str, question: str) -> dict | None:
        original = question.strip()
        text = normalize(original)
        if tool == "open_page":
            match = re.search(r"https?://\S+", original)
            return {"url": match.group(0).rstrip(".,)")} if match else None
        if tool == "research_web":
            query = re.sub(r"^(?:pesquise|pesquisar|busque|procure)(?:\s+na internet|\s+na web|\s+fontes?)?\s*", "", original, flags=re.I).strip(" :")
            return {"query": query, "max_results": 2, "save_to_corpus": bool(re.search(r"\b(?:salve|salvar|acervo|corpus|aprenda)\b", text))} if query else None
        if tool == "list_files":
            return {"path": ""}
        if tool == "read_file":
            match = self._file(original)
            return {"path": match.group(1)} if match else None
        if tool == "search_files":
            query = re.search(r"\b(?:por|pelo|pela|sobre)\s+(.+)$", original, flags=re.I)
            query = query.group(1).strip() if query else re.sub(r".*?\b(?:c[oó]digo|arquivos?|workspace|projeto)\b\s*(?:em)?\s*[:\-]?\s*", "", original, flags=re.I).strip() or original
            return {"query": query}
        if tool == "project_checks":
            return {"check": "auto"}
        if tool == "inspect_project":
            return {"max_depth": 4}
        if tool == "set_workspace":
            match = re.search(r"(?:workspace|projeto|pasta)\s+(?:em|no caminho)?\s*(/[\w./-]+)", original, flags=re.I)
            return {"path": match.group(1)} if match else None
        if tool in {"inspect_media", "extract_document_text"}:
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

    def plan(self, question: str) -> dict | None:
        ranked = self.scores(question)
        for index, (score, tool) in enumerate(ranked):
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
