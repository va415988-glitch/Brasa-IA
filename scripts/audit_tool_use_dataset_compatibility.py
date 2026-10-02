#!/usr/bin/env python3
"""Audit local tool-use trajectories against the agent's tool contracts.

This is an analysis-only adapter: it never invokes tools or promotes examples
to training data. The source data stays in Hugging Face quarantine.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "corpus/quarantine/huggingface/4d26ebfd6ae8fec8/prepared.jsonl"
DEFAULT_CONTRACTS = ROOT / "contracts"

ERROR_RE = re.compile(r"\b(error|invalid|failed|exception|traceback)\b", re.IGNORECASE)

# These are candidate analogies for evaluation design, not executable aliases.
TOOL_HINTS: dict[str, dict[str, Any]] = {
    "tool_exec": {
        "candidates": ["terminal_run"],
        "status": "restricted_profile_only",
        "reason": "A chamada externa aceita execução genérica; terminal_run só aceita perfis fixos e exige aprovação.",
    },
    "execute_bash_command": {
        "candidates": ["terminal_run"],
        "status": "restricted_profile_only",
        "reason": "Comandos Bash livres não correspondem às operações enumeradas de terminal_run.",
    },
    "tool_gather": {
        "candidates": ["list_files", "list_tree", "read_file", "search_files"],
        "status": "ambiguous",
        "reason": "A intenção de coleta não identifica qual leitura local deve ser feita.",
    },
    "search": {
        "candidates": ["search_files", "search_web", "research_web"],
        "status": "ambiguous",
        "reason": "A busca pode ser local ou externa; o nome sozinho não define a fonte.",
    },
    "analyze_code_vulnerabilities": {
        "candidates": ["inspect_code"],
        "status": "partial_capability",
        "reason": "inspect_code lista símbolos e imports; não é um scanner de vulnerabilidades.",
    },
    "execute_query": {
        "candidates": [],
        "status": "no_local_equivalent",
        "reason": "O catálogo local não oferece execução genérica de consultas a banco de dados.",
    },
}


def load_contract_names(directory: Path) -> set[str]:
    names: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        try:
            contract = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = contract.get("name") if isinstance(contract, dict) else None
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def parse_message_sequence(serialized: str) -> tuple[list[dict[str, Any]], bool]:
    """Parse concatenated JSON message objects stored in prepared.jsonl."""
    decoder = json.JSONDecoder()
    messages: list[dict[str, Any]] = []
    position = 0
    had_error = False
    while position < len(serialized):
        while position < len(serialized) and serialized[position].isspace():
            position += 1
        if position >= len(serialized):
            break
        try:
            value, end = decoder.raw_decode(serialized, position)
        except json.JSONDecodeError:
            had_error = True
            next_object = serialized.find("{", position + 1)
            if next_object < 0:
                break
            position = next_object
            continue
        if isinstance(value, dict):
            messages.append(value)
        position = end
    return messages, had_error


def analyze_record(text: str) -> dict[str, Any]:
    messages, parse_error = parse_message_sequence(text)
    calls: list[tuple[str, Any]] = []
    retry_after_error = False
    unresolved_error = False
    pending_error = False

    for message in messages:
        role = str(message.get("role", message.get("from", ""))).casefold()
        tool_calls = message.get("tool_calls")
        if role in {"assistant", "gpt", "model"} and isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name", "")).strip()
                if name:
                    calls.append((name, call.get("arguments", {})))
                    if pending_error:
                        retry_after_error = True
                        pending_error = False
        if role == "tool":
            content = str(message.get("content", message.get("value", "")))
            if ERROR_RE.search(content):
                pending_error = True

    unresolved_error = pending_error
    return {
        "messages": messages,
        "calls": calls,
        "parse_error": parse_error,
        "retry_after_error": retry_after_error,
        "unresolved_error": unresolved_error,
    }


def build_report(input_path: Path, contracts_dir: Path) -> dict[str, Any]:
    local_names = load_contract_names(contracts_dir)
    manifest_path = Path(f"{input_path.parent}.json")
    source_manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            loaded_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded_manifest, dict):
                source_manifest = loaded_manifest
        except (OSError, json.JSONDecodeError):
            source_manifest = {}
    tool_counts: Counter[str] = Counter()
    rows = 0
    source_rows = 0
    rows_with_calls = 0
    rows_with_retry = 0
    rows_with_unresolved_error = 0
    parse_errors = 0
    total_calls = 0
    exact_calls = 0

    with input_path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            rows += 1
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"JSONL inválido na linha {line_number}: {error}") from error
            if item.get("source_file") != "data/train-00000-of-00001.parquet":
                continue
            source_rows += 1
            result = analyze_record(str(item.get("text", "")))
            parse_errors += int(result["parse_error"])
            rows_with_calls += int(bool(result["calls"]))
            rows_with_retry += int(result["retry_after_error"])
            rows_with_unresolved_error += int(result["unresolved_error"])
            for name, _arguments in result["calls"]:
                total_calls += 1
                tool_counts[name] += 1
                if name in local_names:
                    exact_calls += 1

    tools = []
    for name, count in tool_counts.most_common():
        hint = TOOL_HINTS.get(name, {
            "candidates": [],
            "status": "unmapped_external_tool",
            "reason": "Nenhum mapeamento explícito foi definido.",
        })
        candidates = [candidate for candidate in hint["candidates"] if candidate in local_names]
        tools.append({
            "external_name": name,
            "calls": count,
            "exact_local_name_match": name in local_names,
            "candidate_local_tools": candidates,
            "mapping_status": hint["status"],
            "reason": hint["reason"],
        })

    return {
        "schema": "tool-use-dataset-compatibility/v1",
        "source": str(input_path.resolve()),
        "source_license": (source_manifest.get("license") or {}).get("value"),
        "license_review_required": (source_manifest.get("license") or {}).get("requires_review"),
        "content_review_required": (source_manifest.get("preparation") or {}).get("review_required"),
        "evaluation_only": True,
        "training_eligible": False,
        "external_calls_are_executed": False,
        "records": rows,
        "trajectory_records": source_rows,
        "records_with_calls": rows_with_calls,
        "tool_calls": total_calls,
        "exact_local_tool_name_calls": exact_calls,
        "exact_name_coverage": exact_calls / total_calls if total_calls else 0.0,
        "records_with_retry_after_tool_error": rows_with_retry,
        "records_with_unresolved_tool_error": rows_with_unresolved_error,
        "records_with_message_parse_warnings": parse_errors,
        "local_tool_count": len(local_names),
        "external_tools": tools,
        "interpretation": (
            "Use como fonte de padrões de decisão e recuperação após adaptação humana. "
            "Não reproduza chamadas externas diretamente: nomes e contratos diferem dos locais."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    parser.add_argument("--output", type=Path, help="opcional: salva o relatório JSON")
    args = parser.parse_args()
    try:
        report = build_report(args.input, args.contracts)
    except (OSError, ValueError) as error:
        print(f"Falha na auditoria: {error}", file=sys.stderr)
        return 1
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Relatório salvo em: {args.output}")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
