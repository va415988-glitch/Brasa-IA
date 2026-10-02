"""Portfólio inicial de competências técnicas do agente.

São 20 trilhas de aprendizagem, não 20 prompts soltos. Cada trilha declara
família, usos e relações para que o orquestrador reutilize fundamentos e não
recomece do zero em cada linguagem.
"""

from __future__ import annotations

import copy
import re
from typing import Any


PORTFOLIO_TRACKS: tuple[dict[str, Any], ...] = (
    {"id": "python", "label": "Python", "topics": ["Python"], "family": "general-purpose", "uses": ["IA", "dados", "automação", "backend"], "priority": 1.30, "related": ["R", "Julia", "JavaScript"]},
    {"id": "javascript", "label": "JavaScript", "topics": ["JavaScript"], "family": "web", "uses": ["web", "frontend", "backend"], "priority": 1.25, "related": ["TypeScript", "Node.js"]},
    {"id": "typescript", "label": "TypeScript", "topics": ["TypeScript"], "family": "web", "uses": ["sistemas grandes", "web", "backend"], "priority": 1.35, "related": ["JavaScript", "Node.js"]},
    {"id": "java", "label": "Java", "topics": ["Java"], "family": "enterprise", "uses": ["backend", "sistemas corporativos", "Android legado"], "priority": 1.10, "related": ["Kotlin", "C#"]},
    {"id": "csharp", "label": "C#", "topics": ["C#"], "family": "enterprise", "uses": [".NET", "backend", "jogos"], "priority": 1.05, "related": ["Java", "C++"]},
    {"id": "sql", "label": "SQL", "topics": ["SQL"], "family": "data", "uses": ["bancos relacionais", "analítica", "backend"], "priority": 1.25, "related": ["Python", "R"]},
    {"id": "c", "label": "C", "topics": ["C"], "family": "systems", "uses": ["sistemas", "firmware", "embarcados"], "priority": 0.95, "related": ["C++", "Rust"]},
    {"id": "cpp", "label": "C++", "topics": ["C++"], "family": "systems", "uses": ["jogos", "motores", "alto desempenho"], "priority": 1.00, "related": ["C", "Rust"]},
    {"id": "rust", "label": "Rust", "topics": ["Rust"], "family": "systems", "uses": ["segurança de memória", "infraestrutura", "alto desempenho"], "priority": 1.20, "related": ["C", "C++", "Go"]},
    {"id": "go", "label": "Go", "topics": ["Go"], "family": "systems", "uses": ["cloud", "microsserviços", "infraestrutura"], "priority": 1.05, "related": ["Rust", "Python"]},
    {"id": "php", "label": "PHP", "topics": ["PHP"], "family": "web", "uses": ["web", "CMS", "backend"], "priority": 0.90, "related": ["JavaScript", "Ruby"]},
    {"id": "kotlin", "label": "Kotlin", "topics": ["Kotlin"], "family": "mobile", "uses": ["Android", "backend"], "priority": 0.95, "related": ["Java"]},
    {"id": "swift", "label": "Swift", "topics": ["Swift"], "family": "mobile", "uses": ["iOS", "macOS"], "priority": 0.85, "related": ["Kotlin"]},
    {"id": "dart", "label": "Dart", "topics": ["Dart"], "family": "mobile", "uses": ["Flutter", "mobile", "web"], "priority": 0.80, "related": ["JavaScript", "Kotlin"]},
    {"id": "ruby", "label": "Ruby", "topics": ["Ruby"], "family": "web", "uses": ["produtividade", "startups", "Rails"], "priority": 0.75, "related": ["Python", "PHP"]},
    {"id": "r", "label": "R", "topics": ["R"], "family": "data", "uses": ["estatística", "ciência", "visualização"], "priority": 0.85, "related": ["Python", "SQL"]},
    {"id": "bash", "label": "Shell / Bash", "topics": ["Bash"], "family": "automation", "uses": ["Linux", "servidores", "CI/CD"], "priority": 1.00, "related": ["Python", "Go"]},
    {"id": "lua", "label": "Lua", "topics": ["Lua"], "family": "embedded-scripting", "uses": ["jogos", "embarcados", "automação"], "priority": 0.65, "related": ["C", "JavaScript"]},
    {"id": "elixir", "label": "Elixir", "topics": ["Elixir"], "family": "distributed", "uses": ["concorrência", "tempo real", "alta disponibilidade"], "priority": 0.70, "related": ["Erlang", "Ruby"]},
    {"id": "scientific-computing", "label": "MATLAB / Julia", "topics": ["MATLAB", "Julia"], "family": "scientific", "uses": ["engenharia", "computação científica", "matemática financeira"], "priority": 0.75, "related": ["Python", "R"]},
)

SHARED_FOUNDATIONS: tuple[str, ...] = (
    "modelagem de problemas", "tipos e dados", "controle e composição", "erros e testes",
    "debugging", "algoritmos", "segurança", "desempenho", "concorrência", "documentação",
)


def portfolio_manifest() -> dict[str, Any]:
    return {
        "schema": "learning-portfolio/v1",
        "tracks": copy.deepcopy(list(PORTFOLIO_TRACKS)),
        "shared_foundations": list(SHARED_FOUNDATIONS),
        "policy": "uma fonte orienta; prática isolada e transferência verificam; integração promove",
    }


def portfolio_topics() -> list[str]:
    return [topic for track in PORTFOLIO_TRACKS for topic in track["topics"]]


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def find_track(topic: str) -> dict[str, Any] | None:
    needle = _norm(topic)
    for track in PORTFOLIO_TRACKS:
        if needle in {_norm(track["id"]), _norm(track["label"]), *(_norm(item) for item in track["topics"])}:
            return copy.deepcopy(track)
    return None
