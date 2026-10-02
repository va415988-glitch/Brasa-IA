#!/usr/bin/env python3
"""Summarize the dominant failure patterns in a raw neural benchmark report."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _iter_cases(report: dict[str, Any]) -> list[dict[str, Any]]:
    cases = report.get("cases")
    return cases if isinstance(cases, list) else []


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    cases = _iter_cases(report)
    pass_count = sum(1 for case in cases if case.get("ok") is True)
    fail_count = len(cases) - pass_count
    reason_counts: Counter[str] = Counter()
    check_fail_counts: Counter[str] = Counter()

    for case in cases:
        reason = str(case.get("quality_reason") or "unknown")
        if case.get("ok") is False:
            reason_counts[reason] += 1

        checks = case.get("checks")
        if isinstance(checks, dict):
            for name, value in checks.items():
                if value is False:
                    check_fail_counts[str(name)] += 1

    return {
        "total_cases": len(cases),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "reason_counts": dict(sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))),
        "check_fail_counts": dict(sorted(check_fail_counts.items(), key=lambda item: (-item[1], item[0]))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize dominant failure reasons in a neural benchmark report.")
    parser.add_argument("--report", type=Path, default=Path(__file__).resolve().parents[1] / "model" / "neural_generation_report.json")
    args = parser.parse_args()

    payload = json.loads(args.report.read_text(encoding="utf-8"))
    summary = summarize_report(payload)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
