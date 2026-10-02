#!/usr/bin/env python3
"""Extract small, test-backed edit proposals from local Code-Agent traces."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow.ipc as ipc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from proactive_implementation import parse_implementation_plan
from tokenizer import ByteBPETokenizer

SOURCE = ROOT / "datasets/UltraData-Code-Agent"
PATCH_HEADER = re.compile(r"^\*\*\* Update File: (.+)$")
TEST_COMMAND = re.compile(r"\b(?:pytest|unittest|cargo test|npm test|pnpm test|go test)\b", re.I)
PASS_OUTPUT = re.compile(r"(?:\b[1-9]\d* passed\b|\bRan [1-9]\d* tests?\b[\s\S]{0,100}\bOK\b)", re.I)
FAIL_OUTPUT = re.compile(r"(?:\b\d+ failed\b|\bFAILED\b|test result: FAILED|\bERROR\b)", re.I)


def arguments(call: dict) -> dict:
    raw = (call.get("function") or {}).get("arguments") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def one_call(message: dict, name: str) -> dict | None:
    calls = message.get("tool_calls") or []
    if len(calls) != 1 or (calls[0].get("function") or {}).get("name") != name:
        return None
    return arguments(calls[0])


def patch_to_edit(patch: str) -> tuple[str, str, str] | None:
    """Accept one update hunk; include context to make old_text exact."""
    lines = patch.splitlines(keepends=True)
    if len(lines) < 5 or lines[0].strip() != "*** Begin Patch" or lines[-1].strip() != "*** End Patch":
        return None
    headers = [PATCH_HEADER.match(line.rstrip("\n")) for line in lines]
    found = [match for match in headers if match]
    if len(found) != 1 or any(line.startswith(("*** Add File:", "*** Delete File:", "*** Move to:")) for line in lines):
        return None
    path = found[0].group(1).strip()
    if path.startswith("/app/"):
        path = path[5:]
    elif path.startswith("/"):
        return None
    if not path or len(path) > 200 or path.startswith("../"):
        return None
    hunk_indices = [i for i, line in enumerate(lines) if line.startswith("@@")]
    if len(hunk_indices) != 1:
        return None
    old_lines, new_lines = [], []
    deletions = additions = 0
    for line in lines[hunk_indices[0] + 1:-1]:
        if line.startswith(" "):
            old_lines.append(line[1:])
            new_lines.append(line[1:])
        elif line.startswith("-"):
            old_lines.append(line[1:])
            deletions += 1
        elif line.startswith("+"):
            new_lines.append(line[1:])
            additions += 1
        else:
            return None
    old_text, new_text = "".join(old_lines), "".join(new_lines)
    if not deletions or not additions or not old_text.strip() or not new_text.strip():
        return None
    if len(old_text) > 1500 or len(new_text) > 1500:
        return None
    return path, old_text, new_text


def verified_test_after(messages: list[dict], patch_index: int) -> str | None:
    for index in range(patch_index + 2, len(messages) - 1):
        message = messages[index]
        if message.get("role") != "assistant":
            continue
        call = one_call(message, "shell")
        if call is None:
            continue
        command = str(call.get("command") or "")
        if not TEST_COMMAND.search(command):
            continue
        result = messages[index + 1]
        if result.get("role") != "tool":
            continue
        output = str(result.get("content") or "")
        if PASS_OUTPUT.search(output) and not FAIL_OUTPUT.search(output):
            return command[:300]
    return None


def concise_request(raw: str) -> str:
    if "<pr_description>" in raw and "</pr_description>" in raw:
        raw = raw.split("<pr_description>", 1)[1].split("</pr_description>", 1)[0]
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:1000]


def extract(row: dict) -> tuple[dict | None, str]:
    try:
        messages = json.loads(row["messages"])
    except (ValueError, TypeError, KeyError):
        return None, "invalid_messages"
    patches = [(index, one_call(message, "apply_patch"))
               for index, message in enumerate(messages) if message.get("role") == "assistant"
               and any((call.get("function") or {}).get("name") == "apply_patch"
                       for call in message.get("tool_calls") or [])]
    if len(patches) != 1 or patches[0][1] is None:
        return None, "multiple_or_ambiguous_patches"
    index, call = patches[0]
    edit = patch_to_edit(str(call.get("patch") or ""))
    if edit is None:
        return None, "unsupported_patch"
    if index + 1 >= len(messages) or messages[index + 1].get("role") != "tool":
        return None, "patch_result_missing"
    patch_result = str(messages[index + 1].get("content") or "")
    if not ("Updated file:" in patch_result or "Success." in patch_result):
        return None, "patch_not_confirmed"
    test_command = verified_test_after(messages, index)
    if test_command is None:
        return None, "no_confirmed_test"
    user = next((str(message.get("content") or "") for message in messages if message.get("role") == "user"), "")
    if not user.strip():
        return None, "unsupported_request"
    request = concise_request(user)
    path, old_text, new_text = edit
    prompt = ("Corrija o projeto conforme o pedido. O trecho abaixo foi observado no arquivo indicado. "
              "Responda somente com JSON no contrato de operações; edite apenas esse arquivo.\n\n"
              f"Pedido:\n{request}\n\nArquivo: {path}\nTrecho observado:\n```\n{old_text}```\n\n"
              "Formato: {\"assumptions\":[],\"operations\":[{\"tool\":\"edit_file\","
              "\"arguments\":{\"path\":\"...\",\"old_text\":\"...\",\"new_text\":\"...\"}}]}")
    answer = json.dumps({"assumptions": [], "operations": [{"tool": "edit_file",
                          "arguments": {"path": path, "old_text": old_text, "new_text": new_text}}]},
                        ensure_ascii=False, separators=(",", ":"))
    try:
        parse_implementation_plan(answer, existing_paths={path}, readable_sources={path: old_text})
    except ValueError:
        return None, "contract_rejected"
    parts = Path(path).parts
    group = parts[1] if parts[0] == "src" and len(parts) > 1 else parts[0]
    if group in {"conan", "conans"}:
        group = "conan"
    return {"id": str(row["uuid"]), "messages": [{"role": "user", "content": prompt},
                                                    {"role": "assistant", "content": answer}],
            "provenance": {"source": "UltraData-Code-Agent", "group": group,
                           "test_command": test_command}}, "accepted"


def split_for(identifier: str) -> str:
    bucket = int(hashlib.sha256(identifier.encode()).hexdigest()[:8], 16) % 10
    return "validation" if bucket == 8 else "heldout" if bucket == 9 else "train"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-shards", type=int, default=1)
    parser.add_argument("--max-per-group", type=int, default=0,
                        help="limita exemplos por grupo; 0 mantém todos")
    parser.add_argument("--output-dir", default="datasets/implementation_repair_sft_v1")
    args = parser.parse_args()
    if args.max_shards < 1 or args.max_per_group < 0:
        parser.error("--max-shards precisa ser positivo e --max-per-group não pode ser negativo")
    shards = sorted(SOURCE.glob("*.arrow"))[:args.max_shards]
    if not shards:
        parser.error("nenhum shard Code-Agent encontrado")
    rows = {split: [] for split in ("train", "validation", "heldout")}
    counts = Counter()
    tokenizer = ByteBPETokenizer.load(ROOT / "model/godmode/godmode-tokenizer-v1.json")
    for shard in shards:
        with shard.open("rb") as handle:
            for batch in ipc.open_stream(handle):
                for raw in batch.to_pylist():
                    counts["trajectories"] += 1
                    example, reason = extract(raw)
                    counts[reason] += 1
                    if example is not None:
                        prompt = example["messages"][0]["content"]
                        answer = example["messages"][1]["content"]
                        if len(tokenizer.encode_fast(prompt)) + len(tokenizer.encode_fast(answer)) > 2048:
                            counts["over_training_context"] += 1
                            counts["accepted"] -= 1
                            continue
                        split = split_for(example["provenance"]["group"])
                        if split == "heldout":
                            example["reference_answer"] = example["messages"].pop()["content"]
                        rows[split].append(example)
    for split, examples in rows.items():
        by_group: dict[str, list[dict]] = {}
        for example in examples:
            by_group.setdefault(example["provenance"]["group"], []).append(example)
        selected = []
        for group_examples in by_group.values():
            group_examples.sort(key=lambda item: hashlib.sha256(item["id"].encode()).hexdigest())
            selected.extend(group_examples[:args.max_per_group] if args.max_per_group else group_examples)
        selected.sort(key=lambda item: item["id"])
        counts["group_cap_dropped"] += len(examples) - len(selected)
        rows[split] = selected
    output = ROOT / args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    groups = {split: {example["provenance"]["group"] for example in examples}
              for split, examples in rows.items()}
    if (groups["train"] & groups["validation"] or groups["train"] & groups["heldout"]
            or groups["validation"] & groups["heldout"]):
        raise RuntimeError("grupos de repositório sobrepostos entre partições")
    hashes = {}
    for split, examples in rows.items():
        path = output / f"{split}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in examples), encoding="utf-8")
        hashes[split] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {"schema": "brasa-implementation-repair-sft/v1", "source_shards": [x.name for x in shards],
              "counts": dict(counts), "splits": {key: len(value) for key, value in rows.items()},
              "groups": {key: len(value) for key, value in groups.items()},
              "max_per_group": args.max_per_group,
              "max_prompt_plus_answer_tokens": 2048,
              "sha256": hashes,
              "limits": ["Critério de teste vem da trajetória; a verificação não foi reproduzida no repositório original.",
                         "Trechos de entrada são reconstruídos do patch e não substituem observações reais do workspace.",
                         "Grupos são inferidos do caminho do arquivo; projetos com layouts distintos ainda podem atravessar partições.",
                         "Conjunto experimental de reparos; não demonstra criação de projetos do zero."]}
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
