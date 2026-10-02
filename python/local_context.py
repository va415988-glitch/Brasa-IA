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
        r"conhece|sabe|aprendeu|aprendizado|capacidade|consegue\s+(?:fazer|criar|construir|desenvolver)|"
        r"pode\s+(?:fazer|criar|construir|desenvolver))\b", text
    )
    return bool(refers_to_agent and asks_about_ability)


def capability_snapshot(skills: dict) -> dict:
    """Projeta o registro de competências num contrato estável para APIs e chat."""
    rows = []
    for topic, skill in skills.items():
        evidence = skill.get("evidence") or {}
        practice = skill.get("practice") or {}
        laboratory = skill.get("laboratory_checks") or {}
        evaluation = skill.get("evaluation") or {}
        contract = skill.get("learning_contract") or {}
        practice_plan = ((contract.get("practice") or {}).get("tasks") or [])
        rows.append({
            "topic": topic,
            "status": skill.get("status") or "unknown",
            "domain": contract.get("domain") or "general",
            "lab_progress": round(float(evaluation.get("progress") or 0), 4),
            "approved_practices": int(practice.get("passed") or 0),
            "unclassified_practice_records": len(practice.get("unclassified_legacy") or [])
            + int((practice.get("legacy_unclassified_counts") or {}).get("passed") or 0),
            "reference_lab_checks": int(laboratory.get("passed") or 0),
            "independent_sources": int(evidence.get("independent_hosts") or 0),
            "evidence_documents": int(evidence.get("documents") or 0),
            "pending_practices": max(0, len(practice_plan) - int(practice.get("passed") or 0)),
            "next_action": (
                "executar laboratório isolado" if (practice_plan and any(item.get("mode") == "local-executor" for item in practice_plan))
                else "resolver uma prática revisável e uma tarefa inédita" if practice_plan
                else "pesquisar fontes primárias e definir uma prática"
            ),
            "source_repository": skill.get("source_repository"),
        })
    rows.sort(key=lambda row: (-row["approved_practices"], -row["lab_progress"], row["topic"].casefold()))
    return {
        "schema": "agent-capabilities/v1",
        "source": "local-competency-ledger",
        "summary": {
            "registered": len(rows),
            "with_approved_practice": sum(row["approved_practices"] > 0 for row in rows),
            "with_reference_lab_checks": sum(row["reference_lab_checks"] > 0 for row in rows),
            "mastered": sum(row["status"] == "mastered" for row in rows),
        },
        "skills": rows,
        "interpretation": "O progresso mede critérios da trilha; exercícios fixos do laboratório verificam o ambiente, não soluções escritas pelo agente.",
    }


def capability_reply(snapshot: dict, question: str) -> str:
    rows = snapshot["skills"]
    if not rows:
        return ("Ainda não tenho competências registradas no laboratório local. Posso pesquisar fontes, "
                "praticar e validar uma tarefa em qualquer área, mas não seria honesto afirmar domínio sem evidências.")

    text = normalize(question)
    named = [row for row in rows if re.search(rf"(?<!\w){re.escape(normalize(row['topic']))}(?!\w)", text)]
    if named:
        row = named[0]
        status = "critérios do laboratório concluídos" if row["status"] == "mastered" else "conhecimento parcial"
        practices = row["approved_practices"]
        reference_checks = row["reference_lab_checks"]
        if row["status"] == "mastered":
            next_step = "Posso aplicar essas práticas e verificar a tarefa concreta no projeto."
        elif practices or row.get("pending_practices"):
            next_step = f"Próxima ação: {row['next_action']}; depois mostro o resultado e as lacunas restantes."
        else:
            next_step = "Próxima ação: pesquisar fontes primárias, praticar e validar uma tarefa inédita."
        return (
            f"Sobre {row['topic']}, o registro indica {status}: {practices} tarefa(s) resolvida(s) pelo agente e aprovada(s), "
            f"{reference_checks} exercício(s) fixo(s) do laboratório aprovado(s) e {row['independent_sources']} fonte(s) independente(s). "
            "Os exercícios fixos verificam o laboratório e o ambiente; não contam como soluções escritas pela IA. "
            f"O progresso de {round(row['lab_progress'] * 100)}% mede apenas os critérios do laboratório; "
            f"não equivale a proficiência geral. {next_step}"
        )

    practiced = [row for row in rows if row["approved_practices"] > 0]
    lab_checked = [row for row in rows if row["reference_lab_checks"] > 0]
    if practiced:
        shown = practiced[:8]
        examples = ", ".join(f"{row['topic']} ({row['approved_practices']} tarefas aprovadas)" for row in shown)
        additional = len(practiced) - len(shown)
        suffix = f"; também há desempenho aprovado em outras {additional} competências" if additional else ""
        evidence = f"Tenho tarefas resolvidas pelo agente e aprovadas em {examples}{suffix}. "
    else:
        evidence = "Ainda não há tarefas práticas resolvidas pelo agente e aprovadas registradas. "
    if lab_checked:
        shown_checks = lab_checked[:8]
        examples = ", ".join(f"{row['topic']} ({row['reference_lab_checks']} exercícios)" for row in shown_checks)
        additional_checks = len(lab_checked) - len(shown_checks)
        suffix = f" e em mais {additional_checks} competências" if additional_checks else ""
        evidence += (f"O laboratório executou exercícios fixos de referência em {examples}{suffix}; "
                    "isso valida a suíte e o ambiente, não a capacidade do agente de produzir as soluções. ")
    unclassified_count = sum(row["unclassified_practice_records"] for row in rows)
    if unclassified_count:
        evidence += (f"Há {unclassified_count} registro(s) antigo(s) sem origem comprovada; "
                    "não entram como desempenho do agente até revisão. ")
    remaining = len(rows) - len(practiced)
    extra = f"{remaining} trilhas ainda não têm desempenho independente do agente aprovado. " if remaining else ""
    mastered_count = snapshot["summary"]["mastered"]
    if mastered_count == 0:
        mastery = "Nenhuma trilha concluiu todos os critérios do laboratório. "
    elif mastered_count == 1:
        mastery = "1 trilha concluiu todos os critérios do laboratório. "
    else:
        mastery = f"{mastered_count} trilhas concluíram todos os critérios do laboratório. "
    return (
        evidence + extra + mastery
        + "Esses registros mostram evidência local, não garantem domínio de uma linguagem ou framework inteiro. "
        "Os percentuais da aba Treinamento medem critérios do laboratório, não proficiência geral. "
        "Para uma tarefa de programação, posso consultar fontes pertinentes e testar o resultado no projeto."
    )
