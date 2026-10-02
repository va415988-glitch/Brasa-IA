#!/usr/bin/env python3
"""Gate do assistente em produção: conhecimento, workflow e latência."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (str(ROOT), str(ROOT / "python")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)
from model_server import ModelService
from active_checkpoint import selected_checkpoint


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Avalia o workflow real do assistente local")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=selected_checkpoint(),
        help="checkpoint real usado pelo serviço",
    )
    parser.add_argument("--eval", type=Path, default=ROOT / "model" / "eval_generation.jsonl")
    parser.add_argument("--report", type=Path, default=ROOT / "model" / "workflow_gate_report.json")
    args = parser.parse_args()

    service = ModelService(args.checkpoint, trace_path=None)
    rows = []
    for case in load_cases(args.eval):
        started = time.perf_counter()
        result = service.reply([{"role": "user", "content": case["prompt"]}])
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        text = str(result.get("text") or "").lower()
        hits = sum(term.lower() in text for term in case.get("terms", []))
        workflow = result.get("workflow") or {}
        actual_tool = (result.get("tool_call") or {}).get("tool")
        expected_tool = case.get("expected_tool")
        tool_ok = actual_tool == expected_tool if expected_tool else actual_tool is None
        ok = hits == len(case.get("terms", [])) and bool(workflow) and tool_ok
        rows.append({
            "id": case["id"],
            "prompt": case["prompt"],
            "backend": result.get("backend"),
            "workflow": workflow,
            "actual_tool": actual_tool,
            "expected_tool": expected_tool,
            "term_hits": hits,
            "term_total": len(case.get("terms", [])),
            "tool_ok": tool_ok,
            "ok": ok,
            "elapsed_ms": elapsed_ms,
        })

    elapsed = [row["elapsed_ms"] for row in rows]
    passed = sum(row["ok"] for row in rows)
    report = {
        "version": "workflow-gate/v1",
        "model": {
            "checkpoint": str(args.checkpoint),
            "local_model_loaded": service.local_model is not None,
            "local_model_error": service.local_model_error,
            "context_length": (service.local_config or {}).get("context_length"),
            "memory_entries": len(service.memory),
            "mode": "workflow-with-fallbacks",
        },
        "passed": passed,
        "total": len(rows),
        "pass_rate": round(passed / len(rows), 3) if rows else 0.0,
        "latency_ms": {
            "avg": round(sum(elapsed) / len(elapsed), 2) if elapsed else 0.0,
            "p95": round(sorted(elapsed)[min(len(elapsed) - 1, int(len(elapsed) * 0.95))], 2) if elapsed else 0.0,
        },
        "cases": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()
