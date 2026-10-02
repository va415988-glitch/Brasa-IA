#!/usr/bin/env python3
"""Offline evaluation of local tool selection; never executes selected tools."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from agent_planner import AgentPlanner  # noqa: E402
from tool_registry import ToolRegistry  # noqa: E402

DEFAULT_MANIFEST = ROOT / "config/agent_tool_routing_eval.json"


def evaluate_case(planner: AgentPlanner, case: dict[str, Any]) -> dict[str, Any]:
    call = planner.plan(str(case.get("prompt", "")))
    actual_tool = call.get("tool") if call else None
    actual_arguments = call.get("arguments") if call else {}
    expected_tool = case.get("planner_expected_tool")
    checks = {"tool": actual_tool == expected_tool}
    expected_arguments = case.get("planner_expected_arguments") or {}
    if expected_arguments:
        checks["arguments"] = all(actual_arguments.get(key) == value for key, value in expected_arguments.items())
    return {
        "id": case["id"],
        "source_pattern": case.get("source_pattern"),
        "planner_only": case.get("objective") is None,
        "expected_tool": expected_tool,
        "actual_tool": actual_tool,
        "actual_arguments": actual_arguments,
        "planner_confidence": (call.get("planner") or {}).get("confidence") if call else None,
        "passed": all(checks.values()),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=Path("/tmp/agent-tool-routing-local.json"))
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        cases = list(manifest.get("cases", [])) + list(manifest.get("planner_only_cases", []))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Falha ao ler bateria: {error}", file=sys.stderr)
        return 2

    planner = AgentPlanner(ToolRegistry())
    results = [evaluate_case(planner, case) for case in cases]
    report = {
        "schema": "agent-tool-routing-eval-result/v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "manifest": str(args.manifest.resolve()),
        "evaluation_only": True,
        "tools_executed": False,
        "passed": sum(result["passed"] for result in results),
        "total": len(results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for result in results:
        actual = result["actual_tool"] or "nenhuma ferramenta"
        expected = result["expected_tool"] or "nenhuma ferramenta"
        print(f"{'PASS' if result['passed'] else 'FAIL'} {result['id']}: esperado {expected}; escolhido {actual}")
    print(f"Resultado: {report['passed']}/{report['total']}. JSON: {args.output}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
