#!/usr/bin/env python3
"""Bateria do checkpoint neural sem memória, ferramentas ou fallback.

Este relatório responde a uma pergunta diferente dos benchmarks do agente:
"o que os pesos conseguem produzir sozinhos?". Um caso reprovado não é
substituído por memória curada nem por uma resposta de quality-gate.
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

from model_server import ModelService, assess_generation_quality


def default_checkpoint() -> Path:
    """Follow the same active-checkpoint selection as start.sh."""
    configured = os.environ.get("IA_LOCAL_CHECKPOINT", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else ROOT / path
    try:
        state = json.loads((ROOT / "model" / "godmode" / "state.json").read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("estado do checkpoint fora do formato esperado")
        checkpoint = str(state.get("checkpoint") or "")
        candidate = ROOT / checkpoint
        if state.get("status") == "active" and checkpoint and candidate.is_file():
            return candidate
    except (OSError, ValueError, TypeError):
        pass
    return ROOT / "model" / "checkpoints" / "compact-08-gate-focus.pt"


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def structural_checks(answer: str, prompt: str, terms: list[str]) -> dict[str, bool]:
    text = str(answer or "").strip()
    normalized = text.casefold()
    words = text.split()
    term_hits = sum(term.casefold() in normalized for term in terms)
    return {
        "nonempty": len(text) >= 24,
        "no_control_markers": "<|" not in text and "|>" not in text,
        "no_protocol_fragment": not bool(re.search(r"\{\s*[\"']?(?:role|assistant|user|content|tool)\b", text, flags=re.I)),
        "no_replacement_character": "�" not in text,
        "no_control_characters": not any(ord(char) < 32 and char not in "\n\r\t" for char in text),
        "not_obviously_repetitive": not bool(
            re.search(r"(?i)([a-zà-ÿ]{2,12})(?:\1){2,}", text)
            or re.search(r"(?i)([a-z0-9-]{2,20})(?:\1){2,}", text)
            or re.search(r"\b(\w+)(?:\s+\1){1,}\b", text)
        ),
        "enough_vocabulary": len(set(words)) >= max(4, min(12, len(words) // 2)) if words else False,
        "relevant_terms": term_hits >= max(1, min(2, len(terms))),
    }


def checkpoint_reply(service: ModelService, prompt: str,
                     messages: list[dict[str, str]] | None = None) -> str | None:
    """Call only the loaded checkpoint; skip curated datasets and memory adapters."""
    request_messages = messages or [{"role": "user", "content": prompt}]
    return service.local_reply(request_messages, knowledge=None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Avalia somente a geração neural do checkpoint local")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=default_checkpoint(),
    )
    parser.add_argument("--eval", type=Path, default=ROOT / "model" / "eval_generation.jsonl")
    parser.add_argument("--report", type=Path, default=ROOT / "model" / "neural_generation_report.json")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--limit", type=int, default=None, help="avalia somente os primeiros N casos")
    args = parser.parse_args()

    os.environ.setdefault("IA_LOCAL_NUM_PREDICT", str(args.max_tokens))
    service = ModelService(args.checkpoint, trace_path=None)
    rows = []
    cases = load_cases(args.eval)
    if args.limit is not None:
        cases = cases[:max(0, args.limit)]
    for case in cases:
        messages = case.get("messages")
        if not isinstance(messages, list) or not messages:
            messages = [{"role": "user", "content": str(case["prompt"])}]
        else:
            messages = [
                {"role": item.get("role"), "content": str(item.get("content") or "")}
                for item in messages
                if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
            ]
        prompt = next((item["content"] for item in reversed(messages) if item["role"] == "user"),
                      str(case.get("prompt") or ""))
        started = time.perf_counter()
        answer = checkpoint_reply(service, prompt, messages)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        generation = dict(service.last_generation or {})
        checks = structural_checks(answer or "", prompt, list(case.get("terms", [])))
        if answer:
            quality_ok, quality_reason = assess_generation_quality(answer, prompt)
            checks["runtime_quality_gate"] = quality_ok
        else:
            quality_reason = generation.get("quality_reason") or "no-answer"
            checks["runtime_quality_gate"] = False
        ok = all(checks.values())
        rows.append({
            "id": case["id"],
            "prompt": prompt,
            "input_messages": messages,
            "answer": answer,
            "backend": "raw-local-checkpoint" if service.local_model is not None else "none",
            "checks": checks,
            "quality_reason": quality_reason,
            "generation": generation,
            "elapsed_ms": elapsed_ms,
            "ok": ok,
        })

    passed = sum(row["ok"] for row in rows)
    report = {
        "version": "neural-generation/v1",
        "mode": "neural-only",
        "source": "local-checkpoint-weights",
        "checkpoint": str(args.checkpoint),
        "local_model_loaded": service.local_model is not None,
        "local_model_error": service.local_model_error,
        "context_length": (service.local_config or {}).get("context_length"),
        "memory_entries_loaded_but_not_used": len(service.memory),
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
