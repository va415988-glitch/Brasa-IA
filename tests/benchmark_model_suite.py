#!/usr/bin/env python3
"""Bateria de avaliação local para medir inteligência e robustez do agente."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (str(ROOT), str(ROOT / "python")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)
from model_server import ModelService
from tests.model_assessment import category_summary


TASKS = [
    {"id": "tool-select-01", "category": "tool-routing", "question": "Liste os arquivos do workspace", "expected_tool": "list_files", "required_terms": ["workspace", "arquivo"]},
    {"id": "tool-select-02", "category": "tool-routing", "question": "Leia o arquivo README.md", "expected_tool": "read_file", "required_terms": ["readme", "arquivo"]},
    {"id": "tool-select-03", "category": "tool-routing", "question": "Pesquise no código por create_web_page", "expected_tool": "search_files", "required_terms": ["código", "create_web_page"]},
    {"id": "tool-select-04", "category": "tool-routing", "question": "Cria uma tela de login e rode os testes", "expected_tool": "create_web_page", "required_terms": ["login", "teste"]},
    {"id": "tool-select-05", "category": "tool-routing", "question": "O que é ownership em Rust?", "expected_tool": None, "required_terms": ["ownership", "rust"]},
    {"id": "tool-select-06", "category": "qa", "question": "Explique variáveis em Python.", "expected_tool": None, "required_terms": ["variável", "python"]},
    {"id": "tool-select-07", "category": "qa", "question": "Qual é a diferença entre GET e POST?", "expected_tool": None, "required_terms": ["get", "post", "http"]},
    {"id": "tool-select-08", "category": "programming", "question": "Como projetar uma função Python fácil de testar?", "expected_tool": None, "required_terms": ["responsabilidade", "test"]},
    {"id": "tool-select-09", "category": "programming", "question": "Como tratar erros em uma API?", "expected_tool": None, "required_terms": ["autenticação", "erros"]},
    {"id": "tool-select-10", "category": "creativity", "question": "Como sair de um bloqueio criativo?", "expected_tool": None, "required_terms": ["restrições", "versões"]},
    {"id": "tool-select-11", "category": "creativity", "question": "Crie três conceitos de jogo com uma mecânica incomum.", "expected_tool": None, "required_terms": ["jogo", "puzzle"]},
    {"id": "tool-select-12", "category": "general-knowledge", "question": "Como avaliar se uma afirmação científica é confiável?", "expected_tool": None, "required_terms": ["método", "evidência"]},
    {"id": "tool-select-13", "category": "general-knowledge", "question": "Explique correlação e causalidade.", "expected_tool": None, "required_terms": ["correlação", "causalidade"]},
    {"id": "tool-select-14", "category": "tool-routing", "question": "Inspecione a imagem logo.png", "expected_tool": "inspect_media", "required_terms": ["imagem", "logo"]},
    {"id": "tool-select-15", "category": "tool-routing", "question": "Extraia o texto do documento manual.pdf", "expected_tool": "extract_document_text", "required_terms": ["texto", "manual"]},
]


def score_answer(answer: str, required: list[str]) -> float:
    text = (answer or "").lower()
    if not required:
        return 1.0
    hits = sum(1 for term in required if term.lower() in text)
    return hits / len(required)


def assess(service: ModelService):
    rows = []
    for task in TASKS:
        started = time.perf_counter()
        result = service.reply([{"role": "user", "content": task["question"]}])
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        actual_tool = (result.get("tool_call") or {}).get("tool")
        answer_text = result.get("text") or ""
        tool_ok = task["expected_tool"] in (None, actual_tool) if task["expected_tool"] is not None else actual_tool is None or actual_tool in {None, "None"}
        answer_score = score_answer(answer_text, task.get("required_terms", []))
        rows.append({
            "id": task["id"],
            "category": task["category"],
            "question": task["question"],
            "expected_tool": task["expected_tool"],
            "actual_tool": actual_tool,
            "tool_ok": bool(tool_ok),
            "answer_score": answer_score,
            "elapsed_ms": elapsed_ms,
        })
    return rows


def summarize(rows):
    tool_ok = sum(1 for row in rows if row["tool_ok"])
    answer_scores = [row["answer_score"] for row in rows]
    return {
        "case_count": len(rows),
        "tool_accuracy": round(tool_ok / len(rows), 3) if rows else 0.0,
        "answer_quality_avg": round(sum(answer_scores) / len(answer_scores), 3) if answer_scores else 0.0,
        "latency_ms": {
            "p50": round(sorted(row["elapsed_ms"] for row in rows)[len(rows) // 2], 2),
            "p95": round(sorted(row["elapsed_ms"] for row in rows)[min(len(rows) - 1, max(0, int(len(rows) * 0.95)))], 2),
            "max": round(max(row["elapsed_ms"] for row in rows), 2),
        },
        "categories": category_summary(rows),
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description="Bateria de benchmark para assistente local")
    parser.add_argument("--report", type=Path, default=ROOT / "model" / "eval_suite_report.json")
    args = parser.parse_args()

    service = ModelService("benchmark-suite")
    rows = assess(service)
    report = summarize(rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
