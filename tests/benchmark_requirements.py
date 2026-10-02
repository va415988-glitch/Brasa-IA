#!/usr/bin/env python3
"""Avalia condução, clarificação e qualidade mínima de entregas.

Esta bateria mede o agente completo, mas marca explicitamente o backend que
respondeu. Ela não é evidência de inteligência neural: serve para localizar
falhas na camada de diálogo, planejamento e fallback.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (str(ROOT), str(ROOT / "python")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from model_server import ModelService


CASES = [
    {
        "id": "requirements-ambiguous-app",
        "category": "requirements",
        "messages": [{"role": "user", "content": "Quero criar um aplicativo."}],
        "criteria": ["asks_clarification", "does_not_claim_completion"],
    },
    {
        "id": "requirements-ambiguous-crud",
        "category": "requirements",
        "messages": [{"role": "user", "content": "Construa um sistema de cadastro completo."}],
        "criteria": ["asks_clarification", "does_not_claim_completion"],
    },
    {
        "id": "requirements-explicit-function",
        "category": "software",
        "messages": [{"role": "user", "content": "Crie uma função Python que soma dois números e inclua testes."}],
        "criteria": ["mentions_language", "mentions_tests", "has_actionable_delivery"],
    },
    {
        "id": "requirements-production-quality",
        "category": "software",
        "messages": [{"role": "user", "content": "Implemente uma função Python com tratamento de erros e dois testes: caso normal e limite."}],
        "criteria": ["mentions_error_handling", "mentions_tests", "has_actionable_delivery"],
    },
    {
        "id": "requirements-constraint-memory",
        "category": "continuity",
        "messages": [
            {"role": "user", "content": "Vamos usar Python 3.12 e não podemos usar bibliotecas externas."},
            {"role": "assistant", "content": "Entendido: Python 3.12 e somente biblioteca padrão."},
            {"role": "user", "content": "Agora estruture uma API para o projeto."},
        ],
        "criteria": ["preserves_constraints", "has_actionable_delivery"],
    },
    {
        "id": "requirements-unknown-framework",
        "category": "uncertainty",
        "messages": [{"role": "user", "content": "Como funciona o framework ZirconFable999?"}],
        "criteria": ["does_not_invent", "asks_or_researches"],
    },
    {
        "id": "requirements-creative-diversity",
        "category": "creative",
        "messages": [{"role": "user", "content": "Crie três conceitos de campanha bem diferentes para uma marca de café."}],
        "criteria": ["has_actionable_delivery", "not_blocked", "has_multiple_options"],
    },
]


def _text(result: dict) -> str:
    return str(result.get("text") or "")


def evaluate_case(case: dict, service: ModelService) -> dict:
    started = time.perf_counter()
    result = service.reply(case["messages"])
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    text = _text(result)
    normalized = text.casefold()
    backend = str(result.get("backend") or "")
    criteria = {}

    criteria["asks_clarification"] = (
        "?" in text
        and bool(re.search(r"\b(?:qual|quais|que tipo|objetivo|público|publico|linguagem|banco|escopo|restri[cç][ãa]o)\b", normalized))
    )
    criteria["does_not_claim_completion"] = not bool(
        re.search(r"\b(?:conclu[ií]|implementei|criei|está pronto|esta pronto|finalizei)\b", normalized)
    )
    criteria["mentions_language"] = bool(re.search(r"\bpython\b", normalized))
    criteria["mentions_tests"] = bool(re.search(r"\b(?:teste|testes|pytest|unittest)\b", normalized))
    criteria["mentions_error_handling"] = bool(re.search(r"\b(?:erro|exceção|excecao|try|except|validação|validacao)\b", normalized))
    criteria["preserves_constraints"] = bool(re.search(r"python\s*3\.12", normalized)) and bool(
        re.search(r"(?:sem|não|nao)\s+(?:bibliotecas?\s+)?extern", normalized)
        or "biblioteca padrão" in normalized
        or "biblioteca padrao" in normalized
    )
    criteria["has_actionable_delivery"] = bool(text.strip()) and backend not in {"quality-gate", ""}
    criteria["does_not_invent"] = bool(re.search(r"(?:não encontrei|nao encontrei|não conheço|nao conheco|evidência|evidencia|não posso confirmar|nao posso confirmar|fonte)", normalized))
    criteria["asks_or_researches"] = criteria["asks_clarification"] or bool(result.get("tool_call") or result.get("tool_calls")) or bool(
        re.search(r"\b(?:pesquis|documenta[cç][ãa]o|fonte|confirm)\w*\b", normalized)
    )
    criteria["not_blocked"] = backend != "quality-gate"
    criteria["has_multiple_options"] = len(re.findall(r"(?:^|\n)\s*(?:\d+[.)]|[-*])\s+", text)) >= 3 or len(
        re.findall(r"\b(?:primeir|segund|terceir|op[cç][ãa]o|conceito)\w*\b", normalized)
    ) >= 3

    required = case["criteria"]
    passed = all(criteria.get(name, False) for name in required)
    return {
        "id": case["id"],
        "category": case["category"],
        "backend": backend,
        "criteria": {name: criteria.get(name, False) for name in required},
        "passed": passed,
        "text": text,
        "intent": result.get("intent"),
        "tool": (result.get("tool_call") or {}).get("tool"),
        "elapsed_ms": elapsed_ms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Avalia requisitos, continuidade e condução do agente")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path(os.environ.get("IA_LOCAL_CHECKPOINT", str(ROOT / "model" / "checkpoints" / "compact-08-gate-focus.pt"))),
    )
    parser.add_argument("--report", type=Path, default=ROOT / "model" / "requirements_benchmark_report.json")
    args = parser.parse_args()

    service = ModelService(args.checkpoint, trace_path=None)
    rows = [evaluate_case(case, service) for case in CASES]
    passed = sum(row["passed"] for row in rows)
    report = {
        "version": "requirements-benchmark/v1",
        "mode": "agent-with-fallbacks",
        "checkpoint": str(args.checkpoint),
        "local_model_loaded": service.local_model is not None,
        "local_model_error": service.local_model_error,
        "memory_entries": len(service.memory),
        "passed": passed,
        "total": len(rows),
        "pass_rate": round(passed / len(rows), 3) if rows else 0.0,
        "cases": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()
