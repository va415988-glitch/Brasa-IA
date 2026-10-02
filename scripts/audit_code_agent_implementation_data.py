#!/usr/bin/env python3
"""Count candidate verified edits in local Code-Agent shards without training on them."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import pyarrow.ipc as ipc

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "datasets/UltraData-Code-Agent"
TEST_COMMAND = re.compile(r"\b(?:pytest|unittest|cargo test|npm test|pnpm test|go test)\b", re.I)
PASS_OUTPUT = re.compile(r"(?:\b[1-9]\d* passed\b|\bRan [1-9]\d* tests?\b[\s\S]{0,100}\bOK\b)", re.I)
FAIL_OUTPUT = re.compile(r"(?:\b\d+ failed\b|\bFAILED\b|test result: FAILED)", re.I)


def arguments(call: dict) -> dict:
    raw = (call.get("function") or {}).get("arguments") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def classify(messages: list[dict]) -> dict[str, bool]:
    patch_indices = []
    patches = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if (call.get("function") or {}).get("name") == "apply_patch":
                patch_indices.append(index)
                patches.append(str(arguments(call).get("patch") or ""))
    if not patches:
        return {"patch": False}
    last_patch = max(patch_indices)
    later = messages[last_patch + 1:]
    test_commands = []
    for item in later:
        if item.get("role") != "assistant":
            continue
        for call in item.get("tool_calls") or []:
            fn = call.get("function") or {}
            if fn.get("name") == "shell" and TEST_COMMAND.search(str(arguments(call).get("command") or "")):
                test_commands.append(call)
    outputs = [str(item.get("content") or "") for item in later if item.get("role") == "tool"]
    explicit_pass = any(PASS_OUTPUT.search(output) and not FAIL_OUTPUT.search(output) for output in outputs)
    added_paths = re.findall(r"(?m)^\*\*\* Add File: (.+)$", "\n".join(patches))
    relative_add = any(not path.startswith(("/", "..")) and ":" not in path[:3]
                       for path in added_paths)
    nonempty_add = any(re.search(r"(?m)^\*\*\* Add File: [^\n]+\n\+[^\n]+", patch) for patch in patches)
    return {"patch": True, "update_file": any("*** Update File:" in patch for patch in patches),
            "add_file": bool(added_paths), "nonempty_add": nonempty_add,
            "relative_nonempty_add": relative_add and nonempty_add,
            "test_command_after_patch": bool(test_commands),
            "explicit_pass_after_patch": bool(test_commands) and explicit_pass,
            "multi_patch": len(patches) > 1}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-shards", type=int, default=1)
    parser.add_argument("--output", default="planning/baseline-v1/CODE_AGENT_DATA_AUDIT.json")
    args = parser.parse_args()
    if args.max_shards < 1:
        parser.error("--max-shards precisa ser positivo")
    counts = Counter()
    examples: dict[str, list[str]] = {key: [] for key in
        ("nonempty_add", "relative_nonempty_add", "update_with_explicit_pass")}
    shards = sorted(SOURCE.glob("*.arrow"))[:args.max_shards]
    for path in shards:
        with path.open("rb") as handle:
            for batch in ipc.open_stream(handle):
                for row in batch.to_pylist():
                    counts["trajectories"] += 1
                    try:
                        messages = json.loads(row["messages"])
                    except (ValueError, TypeError):
                        counts["invalid_messages"] += 1
                        continue
                    flags = classify(messages)
                    for key, value in flags.items():
                        counts[key] += bool(value)
                    if flags.get("relative_nonempty_add") and len(examples["relative_nonempty_add"]) < 10:
                        examples["relative_nonempty_add"].append(str(row["uuid"]))
                    if flags.get("nonempty_add") and len(examples["nonempty_add"]) < 10:
                        examples["nonempty_add"].append(str(row["uuid"]))
                    if flags.get("update_file") and flags.get("explicit_pass_after_patch") and len(examples["update_with_explicit_pass"]) < 10:
                        examples["update_with_explicit_pass"].append(str(row["uuid"]))
    report = {"schema": "brasa-code-agent-data-audit/v1", "shards": [path.name for path in shards],
              "counts": dict(counts), "sample_ids_for_review": examples,
              "limits": ["Contagens indicam candidatos, não exemplos aprovados para treino.",
                         "Saída de teste posterior não prova que valida exatamente o último patch.",
                         "Nenhum conteúdo do corpus é copiado por este relatório."]}
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
