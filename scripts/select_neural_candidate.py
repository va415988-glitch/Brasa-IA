#!/usr/bin/env python3
"""Select the best neural-generation checkpoint by benchmark evidence.

This script ranks candidate checkpoints using the real benchmark report that
measures raw model output quality without memory, tool calls, or fallback logic.
The selection rule is intentionally strict: prefer higher pass-rate, then more
passing cases, then fewer total cases as a tie-breaker.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and number not in (float("inf"), float("-inf")) else default


def _normalize_report(name: str, payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    total = payload.get("total")
    passed = payload.get("passed")
    if not isinstance(total, (int, float)) or not isinstance(passed, (int, float)):
        return None
    total_int = int(total)
    passed_int = int(passed)
    if total_int <= 0:
        return None
    pass_rate = payload.get("pass_rate")
    if not isinstance(pass_rate, (int, float)):
        pass_rate = passed_int / total_int
    pass_rate_value = _safe_float(pass_rate, passed_int / total_int)
    if pass_rate_value < 0 or pass_rate_value > 1.0:
        pass_rate_value = passed_int / total_int
    return {
        "name": name,
        "passed": passed_int,
        "total": total_int,
        "pass_rate": pass_rate_value,
    }


def rank_candidate_reports(candidates: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ranked candidates, best first.

    The typical ordering is: pass rate, then raw passed count, then lower total as
    a deterministic tie-breaker. This keeps a compact, higher-quality benchmark
    above a larger but equally weak dataset when the pass rate is the same.
    """
    ranked = []
    for name, payload in candidates.items():
        normalized = _normalize_report(name, payload)
        if normalized is not None:
            ranked.append(normalized)
    ranked.sort(key=lambda item: (item["pass_rate"], item["passed"], -item["total"]), reverse=True)
    return ranked


def _load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _looks_like_candidate_report(payload: Any, path: Path | None = None) -> bool:
    if not isinstance(payload, dict):
        return False
    if "passed" not in payload or "total" not in payload:
        return False

    path_hint = ""
    if path is not None:
        path_hint = f"{path.name} {path.parent.as_posix()}".lower()

    mode = str(payload.get("mode", "")).lower()
    backend = str(payload.get("backend", "")).lower()
    source = str(payload.get("source", "")).lower()
    combined = f"{path_hint} {mode} {backend} {source}".lower()

    if "neural" in combined or "raw-local-checkpoint" in combined or "local-checkpoint-weights" in combined:
        return True
    return False


def _scan_directory(root: Path) -> dict[str, Any]:
    candidates: dict[str, Any] = {}
    for path in sorted(root.rglob("*.json")):
        path_parts = {part.lower() for part in path.parts}
        if "model" not in path_parts and root.name.lower() != "model":
            continue
        try:
            payload = _load_report(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not _looks_like_candidate_report(payload, path):
            continue
        candidate_name = str(path.parent.relative_to(root)).replace("/", "::") if path.parent != root else path.stem
        normalized = _normalize_report(candidate_name, payload)
        if normalized is not None:
            candidates[candidate_name] = payload
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank raw neural-generation candidates using benchmark evidence.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Base workspace root to scan for candidate benchmark reports.")
    parser.add_argument("--report", type=Path, action="append", default=[], help="Explicit benchmark JSON file to rank. Can be passed multiple times.")
    args = parser.parse_args()

    if args.report:
        candidates = {}
        for path in args.report:
            try:
                payload = _load_report(path)
            except (OSError, json.JSONDecodeError):
                continue
            candidates[path.stem] = payload
    else:
        candidates = _scan_directory(args.root)

    ranked = rank_candidate_reports(candidates)
    if not ranked:
        raise SystemExit("No valid candidate benchmark reports found.")

    print(json.dumps({"ranked_candidates": ranked}, ensure_ascii=False, indent=2))
    top = ranked[0]
    print(json.dumps({"selected_candidate": top["name"], "pass_rate": top["pass_rate"], "passed": top["passed"], "total": top["total"]}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
