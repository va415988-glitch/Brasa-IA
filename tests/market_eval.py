"""Executa a matriz de comparação e calcula métricas reproduzíveis.

Uso:
  .venv/bin/python tests/market_eval.py
  .venv/bin/python tests/market_eval.py --transcripts corpus/eval/transcripts.jsonl

Cada transcrição externa deve conter: profile, task_id, text, elapsed_ms
e, opcionalmente, scores com notas de 0 a 4 para as dimensões da matriz.
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from model_server import ModelService

MATRIX = ROOT / "corpus/eval/market_reference_matrix.json"
TASKS = ROOT / "corpus/eval/reference_tasks.jsonl"
OUT = ROOT / "corpus/eval/market_report.json"
DIMENSIONS = [d["id"] for d in json.loads(MATRIX.read_text())["dimensions"]]


def evaluate_text(task, text, backend, elapsed_ms, scores=None):
    text = text or ""
    low = text.lower()
    missing = [check for check in task.get("checks", []) if check.lower() not in low]
    row = {
        "task_id": task["id"], "category": task["category"],
        "backend": backend, "elapsed_ms": round(elapsed_ms, 2),
        "missing": missing, "ok": bool(text.strip()) and not missing,
    }
    if scores:
        row["scores"] = {key: max(0, min(4, int(value))) for key, value in scores.items() if key in DIMENSIONS}
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcripts", type=Path, help="respostas externas em JSONL")
    args = parser.parse_args()
    tasks = [json.loads(line) for line in TASKS.read_text().splitlines() if line.strip()]
    by_id = {task["id"]: task for task in tasks}
    rows = []

    service = ModelService("market-evaluation")
    for task in tasks:
        started = time.perf_counter()
        result = service.reply([{"role": "user", "content": task["question"]}])
        elapsed = (time.perf_counter() - started) * 1000
        rows.append(evaluate_text(task, result.get("text", ""), "local", elapsed))

    if args.transcripts:
        for line in args.transcripts.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            task = by_id.get(item.get("task_id"))
            if not task:
                raise ValueError(f"task_id desconhecido: {item.get('task_id')}")
            rows.append(evaluate_text(task, item.get("text", ""), item["profile"], item.get("elapsed_ms", 0), item.get("scores")))

    profiles = defaultdict(list)
    for row in rows:
        profiles[row["backend"]].append(row)
    report = {"matrix_version": json.loads(MATRIX.read_text())["version"], "profiles": {}}
    for profile, profile_rows in profiles.items():
        categories = {}
        for category in sorted({r["category"] for r in profile_rows}):
            group = [r for r in profile_rows if r["category"] == category]
            categories[category] = {"passed": sum(r["ok"] for r in group), "total": len(group)}
        latencies = sorted(r["elapsed_ms"] for r in profile_rows)
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * .95))]
        scored = [r for r in profile_rows if r.get("scores")]
        dimension_scores = {}
        for dimension in DIMENSIONS:
            values = [r["scores"][dimension] for r in scored if dimension in r["scores"]]
            if values:
                dimension_scores[dimension] = round(sum(values) / len(values), 2)
        report["profiles"][profile] = {
            "passed": sum(r["ok"] for r in profile_rows), "total": len(profile_rows),
            "coverage_pct": round(sum(r["ok"] for r in profile_rows) / len(profile_rows) * 100, 1),
            "categories": categories, "latency_ms": {"p50": latencies[len(latencies)//2], "p95": p95, "max": max(latencies)},
            "over_10s": sum(r["elapsed_ms"] > 10000 for r in profile_rows),
            "dimension_scores": dimension_scores,
            "failures": [r["task_id"] for r in profile_rows if not r["ok"]],
        }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
