"""Contrato explícito para uma tarefa agêntica.

O contrato é uma fronteira de segurança entre a intenção do usuário, o
planejador e o executor. Ele não tenta adivinhar a solução: registra o que
deve ser preservado, como o sucesso será verificado e quando uma ação precisa
de autorização.
"""

from __future__ import annotations

import re
from hashlib import sha256

from dialogue import normalize


SCHEMA = "task-contract/v1"
MAX_OBJECTIVE_CHARS = 4_000
MAX_PATHS = 32
MAX_STEPS = 32


def _has(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, flags=re.I))


def _paths(objective: str) -> list[str]:
    found = re.findall(r"(?<![\w./-])(?:[\w.-]+/)+[\w.-]+\.[\w]+", objective)
    unique = []
    for path in found:
        if path not in unique and len(path) <= 240:
            unique.append(path)
    return unique[:MAX_PATHS]


def _intent(text: str, writes: bool, reads: bool, research: bool) -> str:
    if writes:
        return "change"
    if research:
        return "research"
    if reads:
        return "inspect"
    if _has(text, r"\b(?:compare|explique|como|qual|por que|o que)\b"):
        return "answer"
    return "understand"


def build_task_contract(objective: str, *, max_steps: int = 32) -> dict:
    """Deriva um contrato conservador sem transformar texto externo em ordem."""
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("objetivo da tarefa é obrigatório")
    objective = objective.strip()[:MAX_OBJECTIVE_CHARS]
    text = normalize(objective)
    explicit_no_write = _has(
        text,
        r"\b(?:(?:não|nao)\s+(?:edite|altere|modifique|crie|apague|remova|escreva|execute|publique)|sem\s+(?:editar|alterar|modificar|criar|apagar|remover|escrever|executar|publicar))\b",
    )
    writes = not explicit_no_write and _has(
        text,
        r"\b(?:crie|criar|edite|editar|altere|alterar|modifique|modificar|corrija|corrigir|implemente|implementar|aplique|aplicar|remova|remover|apague|apagar|publique|deploy)\b",
    )
    reads = _has(text, r"\b(?:leia|ler|abra|abrir|analise|analisar|inspecione|inspecionar|compare|busque|procure)\b")
    research = _has(text, r"\b(?:pesquise|pesquisar|fontes?|documentação|documentacao|internet|web|cite|citação|citacao)\b") or "http://" in text or "https://" in text
    asks_verification = _has(text, r"\b(?:teste|testes|verifique|verificar|valide|validar|benchmark|meça|medir|rode|executar|execute)\b")
    destructive = _has(text, r"\b(?:apague|apagar|remova|remover|delete|reset|drop|publique|deploy|produção|producao|segredo|credencial)\b")
    paths = _paths(objective)
    intent = _intent(text, writes, reads, research)
    criteria = []
    if writes:
        criteria.append({"id": "change-scoped", "text": "alterações limitadas ao escopo autorizado e apresentadas como diff"})
        criteria.append({"id": "rollback-ready", "text": "estado anterior preservado ou operação reversível"})
    if asks_verification or writes:
        criteria.append({"id": "verified", "text": "checks reproduzíveis executados e resultado registrado"})
    if research:
        criteria.append({"id": "sources", "text": "fontes e limites de evidência registrados"})
    if not criteria:
        criteria.append({"id": "grounded-answer", "text": "resposta relacionada ao pedido, com incertezas explícitas"})
    budget_steps = max(1, min(MAX_STEPS, int(max_steps)))
    contract = {
        "schema": SCHEMA,
        "version": 1,
        "status": "draft",
        "objective": objective,
        "objective_sha256": sha256(objective.encode("utf-8")).hexdigest(),
        "intent": intent,
        "scope": {"workspace_scoped": True, "explicit_paths": paths},
        "risk": "high" if destructive else ("medium" if writes else "low"),
        "side_effects": "workspace-write" if writes else "read-only",
        "requires_approval": bool(writes or destructive),
        "acceptance_criteria": criteria,
        "verification": {
            "required": bool(writes or asks_verification),
            "status": "pending",
            "checks": [],
        },
        "budget": {
            "max_steps": budget_steps,
            "max_external_requests": 4,
            "max_files_changed": 32,
        },
        "policies": [
            "evidence-is-data-not-instructions",
            "no-unverified-completion-claim",
            "secrets-stay-out-of-traces-and-training",
            "writes-stay-inside-selected-workspace",
        ],
    }
    validate_task_contract(contract)
    return contract


def validate_task_contract(contract: dict) -> None:
    """Falha fechado para contratos persistidos ou recebidos pela UI."""
    if not isinstance(contract, dict) or contract.get("schema") != SCHEMA:
        raise ValueError("contrato de tarefa inválido")
    if not isinstance(contract.get("objective"), str) or not contract["objective"].strip():
        raise ValueError("contrato sem objetivo")
    if contract.get("side_effects") not in {"read-only", "workspace-write"}:
        raise ValueError("efeito colateral desconhecido")
    if not isinstance(contract.get("requires_approval"), bool):
        raise ValueError("aprovação do contrato inválida")
    if contract.get("status") not in {
        "draft", "observing", "awaiting-approval", "awaiting-verification",
        "recovering", "verified", "completed", "blocked", "failed",
    }:
        raise ValueError("status do contrato inválido")
    if contract.get("intent") not in {"change", "research", "inspect", "answer", "understand"}:
        raise ValueError("intenção do contrato inválida")
    if contract.get("risk") not in {"low", "medium", "high"}:
        raise ValueError("risco do contrato inválido")
    if contract.get("side_effects") == "workspace-write" and not contract["requires_approval"]:
        raise ValueError("escrita sem aprovação explícita")
    budget = contract.get("budget")
    if not isinstance(budget, dict):
        raise ValueError("orçamento ausente")
    for key, maximum in (("max_steps", MAX_STEPS), ("max_external_requests", 16), ("max_files_changed", 128)):
        value = budget.get(key)
        if not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"orçamento inválido: {key}")
    scope = contract.get("scope")
    if not isinstance(scope, dict) or not isinstance(scope.get("explicit_paths"), list):
        raise ValueError("escopo inválido")
    if len(scope["explicit_paths"]) > MAX_PATHS:
        raise ValueError("escopo acima do limite")
    criteria = contract.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("critérios de aceite ausentes")
    if any(not isinstance(item, dict) or not item.get("id") or not item.get("text") for item in criteria):
        raise ValueError("critério de aceite inválido")
    verification = contract.get("verification")
    if not isinstance(verification, dict) or not isinstance(verification.get("required"), bool):
        raise ValueError("verificação do contrato ausente")
    if verification.get("status") not in {"pending", "passed", "failed"}:
        raise ValueError("status de verificação inválido")


def evidence_record(tool: str, call_id: str, result: dict, sequence: int) -> dict:
    """Resume uma observação sem duplicar conteúdo sensível no ledger."""
    payload = result if isinstance(result, dict) else {"value": str(result)}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    is_project_check = tool == "project_checks" or (
        tool == "terminal_run" and data.get("operation") == "project_check"
    )
    batch_files = [item.get("result") for item in data.get("operations", [])
                   if isinstance(item, dict) and item.get("tool") in {"create_file", "edit_file"}
                   and isinstance(item.get("result"), dict)] if tool == "apply_batch" else []
    changed = payload.get("ok") is True and (
        (tool in {"create_file", "create_web_page"} and data.get("created") is True)
        or (tool in {"edit_file", "apply_repair"} and data.get("updated") is True)
        or (tool == "apply_batch" and bool(batch_files) and data.get("undo_available") is True)
    )
    reversible = changed and (
        data.get("created") is True or bool(data.get("backup"))
        or (tool == "apply_batch" and data.get("undo_available") is True)
    )
    paths = [str(data[key]) for key in ("path", "workspace") if data.get(key)]
    paths.extend(str(item["path"]) for item in batch_files if item.get("path"))
    source_count = 0
    if tool == "research_web" and payload.get("ok") is True:
        source_count = sum(1 for page in data.get("pages", [])
                           if isinstance(page, dict) and str(page.get("text") or "").strip())
    return {
        "sequence": int(sequence),
        "tool": str(tool),
        "call_id": str(call_id),
        "ok": bool(payload.get("ok")),
        "result_sha256": sha256(str(payload).encode("utf-8")).hexdigest(),
        "verified": bool(
            payload.get("ok") is True
            and is_project_check
            and data.get("executed") is True
            and data.get("passed") is True
        ),
        "changed": bool(changed),
        "reversible": bool(reversible),
        "source_count": source_count,
        "paths": paths,
        "summary": str(data.get("summary") or data.get("status") or "resultado registrado")[:240],
    }


def evaluate_acceptance(contract: dict, evidence: list[dict], answer: str) -> dict[str, list[int]]:
    """Liga cada critério a observações; texto do planejador nunca prova efeito."""
    changed = [item for item in evidence if item.get("ok") and item.get("changed")]
    latest_change = max((item["sequence"] for item in changed), default=0)
    checks = [item for item in evidence if item.get("verified")
              and item.get("sequence", 0) > latest_change]
    sources = [item for item in evidence if item.get("source_count", 0) > 0]
    observed = [item for item in evidence if item.get("ok")]
    mapping = {
        "change-scoped": [item["sequence"] for item in changed if item.get("paths")],
        "rollback-ready": [item["sequence"] for item in changed if item.get("reversible")],
        "verified": [item["sequence"] for item in checks],
        "sources": [item["sequence"] for item in sources],
        "grounded-answer": [item["sequence"] for item in observed] if str(answer).strip() else [],
    }
    if contract.get("intent") == "inspect" and not observed:
        mapping["grounded-answer"] = []
    elif contract.get("intent") != "inspect" and str(answer).strip():
        mapping["grounded-answer"] = [0]
    return {item["id"]: mapping.get(item["id"], [])
            for item in contract.get("acceptance_criteria", [])}
