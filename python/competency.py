"""Política de aprendizagem orientada por competência, independente de área.

Fontes da web são evidência de estudo, não prova de que o agente consegue
executar uma tarefa. Este módulo mantém a parte comum do ciclo: identificar a
área, escolher uma política de pesquisa e produzir uma trilha de prática que
possa ser executada por um laboratório ou revisada por uma pessoa.
"""

from __future__ import annotations

import re
import shutil
from typing import Any


DOMAIN_CONCEPTS: dict[str, tuple[str, ...]] = {
    "programming": (
        "fundamentos", "dados-e-controle", "erros-e-testes", "composicao",
        "problema-novo", "integracao",
    ),
    "writing": (
        "objetivo-e-publico", "estrutura-e-coesao", "clareza-e-tom",
        "revisao", "adaptacao", "integracao",
    ),
    "research": (
        "pergunta-e-escopo", "qualidade-da-fonte", "extracao-de-evidencia",
        "sintese", "incerteza", "integracao",
    ),
    "planning": (
        "objetivo-e-escopo", "restricoes-e-recursos", "etapas-e-marcos",
        "riscos-e-alternativas", "verificacao", "integracao",
    ),
    "mathematics": (
        "definicoes", "hipoteses", "procedimento", "verificacao",
        "casos-limite", "integracao",
    ),
    "communication": (
        "intencao", "contexto", "clareza", "escuta-e-perguntas",
        "adaptacao", "integracao",
    ),
    "design": (
        "necessidade-do-usuario", "hierarquia", "acessibilidade",
        "iteracao", "validacao", "integracao",
    ),
    "general": (
        "definicao-do-problema", "decomposicao", "opcoes-e-tradeoffs",
        "verificacao", "transferencia", "integracao",
    ),
}


_PROGRAMMING_TOPIC_PATTERNS = (
    r"\btypescript\b", r"\bjavascript\b", r"\bpython(?:3)?\b",
    r"\brust\b", r"\bjava\b", r"\bc\+\+\b", r"(?<!\w)c#(?!\w)",
    r"\bgo(?:lang)?\b", r"\bphp\b", r"\bkotlin\b", r"\bswift\b",
    r"\bdart\b", r"\bruby\b", r"\b(?:bash|shell)\b", r"\blua\b",
    r"\belixir\b", r"\b(?:matlab|julia)\b", r"(?<!\w)c(?!\w)",
    r"(?<!\w)r(?!\w)", r"\bsql\b", r"\bnode(?:\.js)?\b",
)


_DOMAIN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("programming", (
        "typescript", "javascript", "python", "rust", "java", "c++", "c#",
        "go", "golang", "php", "kotlin", "swift", "dart", "ruby", "lua",
        "elixir", "matlab", "julia", "react", "node", "api", "framework",
        "biblioteca", "codigo", "programa", "algoritmo", "sql", "backend",
        "frontend", "software", "bash", "shell script", "linguagem c",
        "c programming", "r programming",
    )),
    ("writing", (
        "redacao", "redação", "escrever", "escrita", "texto", "copywriting",
        "romance", "poesia", "roteiro", "artigo", "gramatica", "gramática",
    )),
    ("research", (
        "pesquisa", "pesquisar", "fontes", "evidencia", "evidência", "literatura",
        "referencias", "referências", "revisao sistematica", "revisão sistemática",
    )),
    ("planning", (
        "planejamento", "planejar", "projeto", "roadmap", "cronograma", "estrategia",
        "estratégia", "metas", "processo", "produto", "priorizar", "prioridade",
    )),
    ("mathematics", (
        "matematica", "matemática", "calculo", "cálculo", "algebra", "álgebra",
        "probabilidade", "estatistica", "estatística", "teorema", "equacao", "equação",
    )),
    ("communication", (
        "comunicacao", "comunicação", "conversa", "negociacao", "negociação",
        "apresentacao", "apresentação", "feedback", "persuasao", "persuasão",
    )),
    ("design", (
        "design", "ux", "ui", "interface", "usabilidade", "acessibilidade",
        "prototipo", "protótipo", "wireframe", "layout",
    )),
)


def _text(*parts: Any) -> str:
    return re.sub(r"\s+", " ", " ".join(str(part or "") for part in parts)).strip().casefold()


def infer_domain(topic: str = "", question: str = "", documents: list[dict] | None = None) -> str:
    """Classifica uma competência sem manter uma lista fechada de tecnologias."""
    topic_material = _text(topic, question)
    # Nomes curtos como C e R só são reconhecidos como programação no
    # assunto explícito; procurá-los em qualquer documento geraria falsos
    # positivos em praticamente toda frase.
    if any(re.search(pattern, topic_material) for pattern in _PROGRAMMING_TOPIC_PATTERNS):
        return "programming"
    material = _text(topic, question, " ".join(str(item.get("text") or "") for item in (documents or []))[:12000])
    for domain, markers in _DOMAIN_RULES:
        if any(re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", material) for marker in markers):
            return domain
    return "general"


def domain_concepts(domain: str, topic: str = "", question: str = "") -> list[str]:
    selected = domain if domain in DOMAIN_CONCEPTS else infer_domain(topic, question)
    return list(DOMAIN_CONCEPTS.get(selected, DOMAIN_CONCEPTS["general"]))


def learning_topic_from_question(question: str) -> str | None:
    """Extrai o assunto de pedidos gerais que não são necessariamente técnicos."""
    text = re.sub(r"\s+", " ", str(question or "")).strip(" \t\r\n\"“”")
    patterns = (
        r"^(?:aprenda|estude|domine|aprimore|pesquise sobre|me ensine sobre)\s+(.+)$",
        r"^(?:como|qual a melhor forma de)\s+(?:planejar|escrever|pesquisar|estudar|organizar|aprender)\s+(.+)$",
        r"^(?:me ajude a|quero aprender a|preciso aprender a)\s+(.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.I)
        if not match:
            continue
        candidate = re.split(
            r"\s+(?:e depois|e então|e entao|para que eu|para criar|para construir|para implementar)\b",
            match.group(1), maxsplit=1, flags=re.I,
        )[0].strip(" .?!:;\"'“”")
        if 2 <= len(candidate) <= 100:
            return candidate
    return None


def research_query(topic: str, question: str = "") -> str:
    """Produz uma consulta inicial adequada ao domínio identificado."""
    domain = infer_domain(topic, question)
    suffix = {
        "programming": "official documentation standards and tested examples",
        "research": "methodology authoritative sources and primary studies",
        "writing": "style guides authoritative examples and revision principles",
        "planning": "reliable frameworks case studies and practical guidance",
        "mathematics": "textbook explanations formal definitions and worked examples",
        "communication": "evidence-based communication frameworks and practical examples",
        "design": "usability accessibility standards and validated patterns",
        "general": "authoritative sources practical examples and common pitfalls",
    }[domain]
    return f"{topic.strip()} {suffix}".strip()


def _executor_topic(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(topic).casefold())


LOCAL_EXECUTOR_TOPICS = {
    "python", "python3", "javascript", "typescript", "nodejs", "node", "rust",
    "bash", "shell", "shellbash",
}
if shutil.which("go"):
    LOCAL_EXECUTOR_TOPICS.update({"go", "golang"})


def learning_contract(topic: str, question: str = "", documents: list[dict] | None = None) -> dict[str, Any]:
    """Descreve o ciclo pesquisar -> praticar -> transferir -> integrar."""
    domain = infer_domain(topic, question, documents)
    concepts = domain_concepts(domain, topic, question)
    has_executor = _executor_topic(topic) in LOCAL_EXECUTOR_TOPICS
    if domain == "programming" and has_executor:
        practice_mode = "local-executor"
        practice_verification = "isolated-test"
    elif domain == "programming":
        practice_mode = "bounded-rubric"
        practice_verification = "isolated-test-or-rubric"
    else:
        practice_mode = "model-review"
        practice_verification = "rubric-and-evidence"
    tasks = []
    for index, concept in enumerate(concepts):
        level = "foundation" if index < 2 else "practice" if index < 4 else "transfer"
        tasks.append({
            "id": f"{level}-{concept}",
            "level": level,
            "objective": concept,
            "mode": practice_mode,
            "verification": practice_verification,
        })
    tasks.extend([
        {"id": "unseen-transfer", "level": "transfer", "objective": "resolver uma variação inédita", "mode": "model-review", "verification": "unseen-case"},
        {"id": "integration-deliverable", "level": "integration", "objective": "entregar um resultado completo e verificável", "mode": "model-review", "verification": "acceptance-criteria"},
    ])
    return {
        "schema": "learning-contract/v1",
        "topic": topic,
        "domain": domain,
        "research": {"query": research_query(topic, question), "requires_primary_sources": True, "minimum_independent_hosts": 2},
        "practice": {"tasks": tasks, "requires_unseen_task": True, "requires_integration": True},
        "policy": "fontes orientam a prática; somente resultados verificados atualizam a competência",
    }
