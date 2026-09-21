"""Currículo adaptativo mínimo para o professor local."""

from __future__ import annotations

import re


BASE = [
    ("fundamentos", "identificar conceitos fundamentais e a sintaxe básica"),
    ("dados-e-controle", "usar dados, funções e controle de fluxo"),
    ("erros-e-testes", "tratar erros e escrever testes"),
    ("composicao", "compor módulos ou componentes"),
    ("problema-novo", "resolver uma variação inédita sem copiar o exemplo"),
]


# Estes limites definem o que significa "dominado". A porcentagem exibida
# pode subir durante a pesquisa, mas nenhuma fonte ou exercício isolado deve
# promover uma competência para mastered.
COMPLETION = {
    "required_levels": 3,
    "required_pass_rate": 0.9,
    "required_independent_hosts": 2,
    "minimum_document_count": 8,
    "minimum_practice_tasks": 12,
    "minimum_transfer_tasks": 2,
    # Domínio só pode ser marcado como completo quando todos os objetivos
    # da trilha foram cobertos; a margem de 90% deixava lacunas visíveis
    # convivendo com o rótulo MASTERED.
    "required_covered_ratio": 1.0,
    "requires_unseen_task": True,
    "requires_integration_task": True,
}


def build(topic: str, documents: list[dict] | None = None) -> dict:
    text = " ".join(str(item.get("text") or "") for item in (documents or []))[:30000].lower()
    concepts = []
    for key, label in BASE:
        concepts.append({"id": key, "objective": label, "status": "pending"})
    optional = {
        "rust": ["ownership", "borrowing", "result-option", "cargo", "iterators", "traits", "pattern-matching", "modules"],
        "zig": ["comptime", "allocators", "error-unions", "build-system"],
        "javascript": ["closures", "promises", "modules", "event-loop"],
        "python": ["iterators", "exceptions", "modules", "testing"],
        "react": ["components", "state", "effects", "testing"],
        "nodejs": ["http-server", "streams", "modules", "observability"],
        "sql": ["relational-model", "joins", "indexes", "transactions"],
        "c#": ["types", "classes", "linq", "async-await"],
        "html/css": ["semantic-html", "layout", "accessibility", "responsive-design"],
        "machinelearning": ["datasets", "features", "training", "evaluation", "overfitting"],
        "computerscience": ["algorithms", "data-structures", "complexity", "systems"],
        "axum": ["routing", "extractors", "shared-state", "middleware", "error-handling", "testing", "async"],
    }
    key = topic.lower().replace(".js", "").replace(" ", "")
    for concept in optional.get(key, []):
        concepts.append({"id": concept, "objective": f"demonstrar {concept}",
                         # A menção em uma fonte é evidência de que o assunto
                         # merece estudo, nunca prova de domínio prático.
                         "status": "pending"})
    # O professor também extrai conceitos citados nas fontes. Eles não viram
    # automaticamente conhecimento dominado: entram como objetivos a provar.
    discovered = re.findall(r"\b(?:ownership|borrowing|allocator|allocators|promise|promises|"
                            r"closure|closures|module|modules|component|components|state|"
                            r"error handling|testing|async|comptime|build system|memory)\b", text)
    known = {item["id"] for item in concepts}
    for item in dict.fromkeys(discovered):
        normalized = item.replace(" ", "-")
        if normalized not in known:
            concepts.append({"id": normalized, "objective": f"demonstrar {item}", "status": "pending"})
            known.add(normalized)
    completion = dict(COMPLETION)
    # Repositórios de produto podem ensinar muita prática, mas não substituem
    # uma referência independente. Linguagens e frameworks seguem o mesmo
    # piso; a trilha específica é que determina quais conceitos faltam.
    return {"schema": "curriculum/v2", "topic": topic, "levels": [
        {"id": "foundation", "title": "Fundamentos", "concepts": concepts[:2]},
        {"id": "practice", "title": "Prática guiada", "concepts": concepts[2:5]},
        {"id": "transfer", "title": "Transferência", "concepts": concepts[5:]},
    ], "completion": completion}


def gaps(curriculum: dict) -> list[str]:
    return [item["id"] for level in curriculum.get("levels", [])
            for item in level.get("concepts", []) if item.get("status") != "covered"]
