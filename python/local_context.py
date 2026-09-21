"""Contrato local de capacidades: dados verificáveis, não texto recuperado da web."""

import re

from dialogue import normalize


def is_capability_question(question: str) -> bool:
    """Distingue perguntas sobre o agente de pedidos sobre uma tecnologia."""
    text = normalize(question)
    refers_to_agent = re.search(r"\b(voce|seu|sua|seus|suas|teu|tua|agente|assistente)\b", text)
    if re.search(r"\b(?:voce|agente|assistente)\s+(?:sabe|conhece)\s+(?:como|sobre|por que|quando|onde|se)\b", text):
        return False
    asks_about_ability = re.search(
        r"\b(dominio|domina|dominar|proficiencia|habilidades?|competencias?|conhecimento|"
        r"conhece|sabe|aprendeu|aprendizado|capacidade)\b", text
    )
    return bool(refers_to_agent and asks_about_ability)


def capability_snapshot(skills: dict) -> dict:
    """Projeta o registro de competências num contrato estável para APIs e chat."""
    rows = []
    for topic, skill in skills.items():
        evidence = skill.get("evidence") or {}
        practice = skill.get("practice") or {}
        evaluation = skill.get("evaluation") or {}
        rows.append({
            "topic": topic,
            "status": skill.get("status") or "unknown",
            "lab_progress": round(float(evaluation.get("progress") or 0), 4),
            "approved_practices": int(practice.get("passed") or 0),
            "independent_sources": int(evidence.get("independent_hosts") or 0),
            "evidence_documents": int(evidence.get("documents") or 0),
            "source_repository": skill.get("source_repository"),
        })
    rows.sort(key=lambda row: (-row["approved_practices"], -row["lab_progress"], row["topic"].casefold()))
    return {
        "schema": "agent-capabilities/v1",
        "source": "local-competency-ledger",
        "summary": {
            "registered": len(rows),
            "with_approved_practice": sum(row["approved_practices"] > 0 for row in rows),
            "mastered": sum(row["status"] == "mastered" for row in rows),
        },
        "skills": rows,
        "interpretation": "O progresso mede critérios do laboratório, não proficiência geral nem pesos do modelo.",
    }


def capability_reply(snapshot: dict, question: str) -> str:
    rows = snapshot["skills"]
    if not rows:
        return ("Ainda não tenho competências registradas no laboratório local. Posso consultar fontes, "
                "praticar e testar uma tecnologia, mas não seria honesto afirmar domínio sem evidências.")

    text = normalize(question)
    named = [row for row in rows if re.search(rf"(?<!\w){re.escape(normalize(row['topic']))}(?!\w)", text)]
    if named:
        row = named[0]
        status = "domínio validado" if row["status"] == "mastered" else "conhecimento parcial"
        practices = row["approved_practices"]
        return (
            f"Sobre {row['topic']}, meu registro indica {status}: {practices} prática(s) aprovada(s) "
            f"e {row['independent_sources']} fonte(s) independente(s). "
            f"O progresso de {round(row['lab_progress'] * 100)}% mede apenas os critérios do laboratório; "
            "não equivale a proficiência geral. Posso consultar as evidências e validar uma tarefa concreta "
            "antes de afirmar que sei executá-la bem."
        )

    practiced = [row for row in rows if row["approved_practices"] > 0]
    if practiced:
        examples = ", ".join(f"{row['topic']} ({row['approved_practices']} práticas)" for row in practiced[:5])
        evidence = f"Tenho práticas aprovadas em {examples}. "
    else:
        evidence = "Ainda não tenho práticas aprovadas em nenhuma competência registrada. "
    remaining = len(rows) - len(practiced)
    extra = f"Outras {remaining} competências têm registros, mas ainda carecem de prática aprovada. " if remaining else ""
    mastery = ("Nenhuma está classificada como dominada. " if snapshot["summary"]["mastered"] == 0
               else f"{snapshot['summary']['mastered']} estão classificadas como dominadas pelo laboratório. ")
    return (
        evidence + extra + mastery
        + "Esses registros mostram evidência local, não garantem domínio de uma linguagem ou framework inteiro. "
        "Os percentuais da aba Treinamento medem critérios do laboratório, não proficiência geral. "
        "Para uma tarefa de programação, posso consultar fontes pertinentes e testar o resultado no projeto."
    )
