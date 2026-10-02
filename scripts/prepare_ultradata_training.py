#!/usr/bin/env python3
"""Prepare a deterministic balanced SFT sample from the local UltraData corpus.

System prompts, hidden reasoning, tool calls, and raw tool outputs are excluded:
the current checkpoint is text-only and cannot safely learn external tool schemas.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVAL = ROOT / "model" / "eval_generation.jsonl"
DEFAULT_HELDOUT = ROOT / "model" / "training" / "senior-creative-v1" / "heldout.jsonl"
SOURCES = (
    ("General-Agent", ROOT / "datasets/UltraData-SFT-Agent-2609/data/General_Agent", "jsonl"),
    ("Tool-Use", ROOT / "datasets/UltraData-SFT-Agent-2609/data/Tool_Use", "jsonl"),
    ("Search-Agent", ROOT / "datasets/UltraData-SFT-Agent-2609/data/Search_Agent", "jsonl"),
    ("Code-Agent", ROOT / "datasets/UltraData-Code-Agent", "arrow"),
)


def normalize_question(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text)).casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return value.rstrip(" .?!\t\r\n")


def load_excluded_questions(paths: Iterable[Path]) -> set[str]:
    excluded: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                prompt = row.get("prompt")
                if not prompt:
                    prompt = next((m.get("content", "") for m in row.get("messages", [])
                                   if m.get("role") == "user"), "")
                key = normalize_question(prompt)
                if key:
                    excluded.add(key)
    return excluded


def decode_messages(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        return []
    return [message for message in value if isinstance(message, dict)]


def has_tool_interaction(row: dict[str, Any]) -> bool:
    """Whether the row contains an actual tool call or tool result."""
    for message in decode_messages(row.get("messages")):
        role = str(message.get("role", "")).casefold()
        if role in {"tool", "function"}:
            return True
        if any(message.get(key) for key in ("tool_calls", "tool_call", "function_call")):
            return True
    return False


def conversation_pairs(row: dict[str, Any], source: str,
                       excluded_questions: set[str] | None = None, *,
                       max_context_messages: int = 6,
                       max_prompt_chars: int = 12000,
                       max_answer_chars: int = 6000) -> list[dict[str, Any]]:
    """Extract plain text turns; tool-dependent conversations are excluded."""
    if has_tool_interaction(row):
        return []
    excluded_questions = excluded_questions or set()
    visible: list[dict[str, str]] = []
    extracted: list[dict[str, Any]] = []

    for message in decode_messages(row.get("messages")):
        role = str(message.get("role", "")).casefold()
        value = message.get("content")
        content = value.strip() if isinstance(value, str) else ""

        if role == "user" and content:
            visible.append({"role": "user", "content": content})
            continue
        if role == "assistant" and content and not message.get("tool_calls"):
            if len(content) < 24 or len(content) > max_answer_chars:
                visible.append({"role": "assistant", "content": content[:max_answer_chars]})
                continue
            user_turns = [item for item in visible if item["role"] == "user"]
            if not user_turns:
                visible.append({"role": "assistant", "content": content})
                continue
            latest_user = user_turns[-1]["content"]
            if normalize_question(latest_user) in excluded_questions:
                visible.append({"role": "assistant", "content": content})
                continue

            context = visible[-max_context_messages:]
            prompt = "\n".join(f"{item['role'].upper()}: {item['content']}" for item in context)
            if len(prompt) < 8 or len(prompt) > max_prompt_chars:
                visible.append({"role": "assistant", "content": content})
                continue
            extracted.append({
                "messages": [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": content},
                ],
                "domain": str(row.get("domain") or source),
                "dataset_source": source,
                "dataset_uuid": str(row.get("uuid") or ""),
            })
            visible.append({"role": "assistant", "content": content})
        # system/developer prompts, hidden reasoning, tool calls, and tool
        # outputs are not copied into the model-facing transcript.
    return extracted


def stable_priority(source: str, row: dict[str, Any], turn_index: int) -> int:
    identity = "\0".join((source, str(row.get("dataset_uuid", "")), str(turn_index),
                          row["messages"][0]["content"], row["messages"][1]["content"]))
    return int.from_bytes(hashlib.sha256(identity.encode("utf-8")).digest()[:8], "big")


def retain_lowest_hash(heap: list[tuple[int, str]], limit: int,
                       priority: int, serialized_row: str) -> None:
    """Keep the lowest deterministic hash ranks with bounded memory."""
    item = (-priority, serialized_row)
    if len(heap) < limit:
        heapq.heappush(heap, item)
    elif item > heap[0]:
        heapq.heapreplace(heap, item)


def iter_jsonl_rows(directory: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(directory.glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)


def iter_arrow_rows(directory: Path) -> Iterable[dict[str, Any]]:
    try:
        import pyarrow.ipc as ipc
    except ImportError as error:
        raise RuntimeError("pyarrow é necessário para ler os shards Arrow do Code-Agent") from error
    for path in sorted(directory.glob("*.arrow")):
        with path.open("rb") as handle:
            reader = ipc.open_stream(handle)
            for batch in reader:
                yield from batch.to_pylist()


def prepare(output_path: Path, *, max_per_source: int = 3000,
            excluded_questions: set[str] | None = None) -> dict[str, Any]:
    if max_per_source < 1:
        raise ValueError("max_per_source precisa ser positivo")
    excluded_questions = excluded_questions or set()
    selected: dict[str, list[tuple[int, str]]] = {}
    stats: dict[str, Counter[str]] = {}

    for source, directory, file_format in SOURCES:
        if not directory.is_dir():
            raise FileNotFoundError(f"fonte ausente: {directory}")
        print(json.dumps({"source": source, "status": "scanning", "directory": str(directory)}, ensure_ascii=False), flush=True)
        heap: list[tuple[int, str]] = []
        counters: Counter[str] = Counter()
        rows = iter_jsonl_rows(directory) if file_format == "jsonl" else iter_arrow_rows(directory)
        for row in rows:
            counters["conversations"] += 1
            if counters["conversations"] % 10000 == 0:
                print(json.dumps({
                    "source": source,
                    "status": "progress",
                    "conversations_scanned": counters["conversations"],
                    "eligible_turns": counters["eligible_turns"],
                    "tool_conversations_excluded": counters["tool_interaction_conversations_excluded"],
                }, ensure_ascii=False), flush=True)
            if has_tool_interaction(row):
                counters["tool_interaction_conversations_excluded"] += 1
                continue
            pairs = conversation_pairs(row, source, excluded_questions)
            counters["eligible_turns"] += len(pairs)
            for turn_index, pair in enumerate(pairs):
                priority = stable_priority(source, pair, turn_index)
                serialized = json.dumps(pair, ensure_ascii=False, separators=(",", ":"))
                retain_lowest_hash(heap, max_per_source, priority, serialized)
        selected[source] = heap
        stats[source] = counters
        print(json.dumps({
            "source": source,
            "status": "complete",
            "conversations_scanned": counters["conversations"],
            "eligible_turns": counters["eligible_turns"],
            "tool_conversations_excluded": counters["tool_interaction_conversations_excluded"],
            "sampled_candidates": len(heap),
        }, ensure_ascii=False), flush=True)

    chosen: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for source, heap in selected.items():
        for neg_priority, serialized in heap:
            row = json.loads(serialized)
            key = normalize_question(row["messages"][0]["content"])
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
            if digest in seen:
                stats[source]["cross_source_duplicates"] += 1
                continue
            seen.add(digest)
            chosen.append((-neg_priority, source, serialized))

    chosen.sort(key=lambda item: (item[1], item[0]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for _, _, serialized in chosen:
            handle.write(serialized + "\n")

    manifest = {
        "schema": "ultradata-sft-preparation/v1",
        "selection": "deterministic hash-ranked balanced sample, bounded memory",
        "max_per_source": max_per_source,
        "excluded_eval_question_count": len(excluded_questions),
        "training_rows": len(chosen),
        "output": str(output_path),
        "sources": {
            source: {
                **dict(stats[source]),
                "selected": sum(1 for _, selected_source, _ in chosen if selected_source == source),
            }
            for source, _, _ in SOURCES
        },
        "filters": [
            "system/developer prompts excluded",
            "hidden reasoning excluded",
            "entire conversations containing structured tool calls/results excluded until structured tool SFT exists",
            "empty/short/oversized assistant answers excluded",
            "held-out evaluation questions excluded by exact normalized user prompt",
        ],
    }
    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".agent-state/ultradata-sft/training.jsonl")
    parser.add_argument("--max-per-source", type=int, default=3000,
                        help="balanced sample cap per config; every source shard is scanned")
    parser.add_argument("--eval", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    args = parser.parse_args()
    excluded = load_excluded_questions((args.eval, args.heldout))
    manifest = prepare(args.output, max_per_source=args.max_per_source,
                       excluded_questions=excluded)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
