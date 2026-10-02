#!/usr/bin/env python3
"""Convert verified JSON file plans to a simpler two-file text protocol."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "datasets/implementation_sft_compact_extended_v1"
OUTPUT = ROOT / "datasets/implementation_markdown_probe_v1"


def markdown_plan(plan: dict) -> str:
    files = {op["arguments"]["path"]: op["arguments"]["content"]
             for op in plan["operations"] if op["tool"] == "create_file"}
    return "".join(f"### {path}\n```python\n{files[path].rstrip()}\n```\n" for path in ("app.py", "test_app.py"))


def convert(row: dict, heldout: bool) -> dict:
    old_prompt = row["messages"][0]["content"]
    request = old_prompt.split("Pedido: ", 1)[1].split("\n\n", 1)[0]
    prompt = ("Implemente o pedido em Python. Responda somente com os arquivos completos "
              "em blocos `### app.py` e `### test_app.py`, cada um seguido de código Python. "
              "Não afirme que executou testes. Pedido: " + request)
    old_answer = row.get("reference_answer") if heldout else row["messages"][-1]["content"]
    answer = markdown_plan(json.loads(old_answer))
    converted = {"id": row["id"], "messages": [{"role": "user", "content": prompt}]}
    if heldout:
        converted["reference_answer"] = answer
    else:
        converted["messages"].append({"role": "assistant", "content": answer})
    return converted


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": "brasa-markdown-implementation-probe/v1", "source": str(SOURCE.relative_to(ROOT)), "splits": {}, "sha256": {}}
    for split in ("train", "validation", "heldout"):
        source_rows = [json.loads(line) for line in (SOURCE / f"{split}.jsonl").read_text(encoding="utf-8").splitlines() if line]
        rows = [convert(row, split == "heldout") for row in source_rows]
        path = OUTPUT / f"{split}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        manifest["splits"][split] = len(rows)
        manifest["sha256"][split] = hashlib.sha256(path.read_bytes()).hexdigest()
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
