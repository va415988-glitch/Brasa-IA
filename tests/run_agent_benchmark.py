#!/usr/bin/env python3
"""Matriz local de seleção de ferramentas e continuação do agente."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from model_server import ModelService


CASES = [
    ("Cria uma tela de login com animações", "create_web_page"),
    ("Cria uma tela de login e rode os testes", "create_web_page"),
    ("Pesquise na internet as novidades do Rust", "research_web"),
    ("Abra https://www.rust-lang.org/", "open_page"),
    ("Liste os arquivos do workspace", "list_files"),
    ("Leia o arquivo README.md", "read_file"),
    ("Busque no código por create_web_page", "search_files"),
    ("Rode os testes do projeto", "project_checks"),
    ("Analise este projeto", "inspect_project"),
    ("Crie o arquivo notas/ideias.md:\nconteúdo", "create_file"),
    ("Crie a pasta notas/rascunhos", "create_directory"),
    ("O que é ownership em Rust?", None),
]


def main():
    service = ModelService("benchmark-only")
    rows = []
    durations = []
    for question, expected in CASES:
        started = time.perf_counter()
        result = service.reply([{"role": "user", "content": question}])
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        durations.append(elapsed)
        actual = (result.get("tool_call") or {}).get("tool")
        rows.append({"question": question, "expected": expected, "actual": actual, "ok": actual == expected, "elapsed_ms": elapsed})

    first = service.reply([{"role": "user", "content": CASES[1][0]}])
    trace_id = first.get("trace_id")
    messages = [
        {"role": "user", "content": CASES[1][0]},
        {"role": "tool", "content": json.dumps({"tool": "create_web_page", "ok": True, "data": {"path": "preview/login.html"}, "trace_id": trace_id})},
    ]
    second = service.reply(messages)
    messages.append({"role": "tool", "content": json.dumps({"tool": "project_checks", "ok": True, "data": {"passed": True, "executed": True}, "trace_id": trace_id})})
    final = service.reply(messages)

    failed_check = service.reply([
        {"role": "user", "content": "Rode os testes e explique a falha"},
        {"role": "tool", "content": json.dumps({"tool": "project_checks", "ok": True, "data": {
            "check": "pytest", "passed": False, "executed": True,
            "stderr": "SyntaxError: invalid syntax at app.py:4"
        }})},
    ])
    fallback = service.reply([
        {"role": "user", "content": "Pesquise e leia sobre Rust"},
        {"role": "tool", "content": json.dumps({"tool": "search_web", "ok": True, "data": {"results": [
            {"url": "https://one.invalid", "source_id": "web-1"},
            {"url": "https://two.invalid", "source_id": "web-2"}
        ]}})},
        {"role": "tool", "content": json.dumps({"tool": "open_page", "ok": False, "error": "falha ao abrir https://one.invalid"})},
    ])
    correct = sum(row["ok"] for row in rows)
    report = {
        "cases": len(rows),
        "selection_accuracy": round(correct / len(rows), 3),
        "planning_avg_ms": round(sum(durations) / len(durations), 2),
        "multi_step": {
            "first": (first.get("tool_call") or {}).get("tool"),
            "second": (second.get("tool_call") or {}).get("tool"),
            "final_status": (final.get("agent") or {}).get("status"),
            "trace_continuity": first.get("trace_id") == second.get("trace_id") == final.get("trace_id"),
        },
        "recovery": {
            "failed_check_next": (failed_check.get("tool_call") or {}).get("tool"),
            "failed_check_phase": (failed_check.get("agent") or {}).get("phase"),
            "web_fallback": (fallback.get("tool_call") or {}).get("arguments", {}).get("url"),
            "web_fallback_phase": (fallback.get("agent") or {}).get("phase"),
        },
        "rows": rows,
        "reference": {
            "gpt_6_astra": "Responses tool loop, strict schemas, tool search, async calls, steering and parallel calls",
            "claude_fable_5": "tool_use/tool_result loop, explicit action guidance, adaptive reasoning and parallel calls",
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    recovery_ok = report["recovery"]["failed_check_next"] == "diagnose_project" and report["recovery"]["web_fallback"] == "https://two.invalid"
    raise SystemExit(0 if correct == len(rows) and report["multi_step"]["second"] == "project_checks" and report["multi_step"]["final_status"] == "completed" and report["multi_step"]["trace_continuity"] and recovery_ok else 1)


if __name__ == "__main__":
    main()
