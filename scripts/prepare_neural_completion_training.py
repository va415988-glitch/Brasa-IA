#!/usr/bin/env python3
"""Combine raw failure counterexamples and positive examples into a trainable dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POSITIVE_PATH = ROOT / "model" / "training" / "neural-positive-examples-v1.jsonl"
NEGATIVE_PATH = ROOT / "model" / "training" / "neural-failure-counterexamples-v1.jsonl"
OUTPUT_PATH = ROOT / "model" / "training" / "neural-completion-training-v1.jsonl"
MANIFEST_PATH = ROOT / "model" / "training" / "neural-completion-training-v1.manifest.json"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"arquivo ausente: {path}")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def build_training_rows() -> list[dict[str, Any]]:
    positive_rows = _read_jsonl(POSITIVE_PATH)
    negative_rows = _read_jsonl(NEGATIVE_PATH)

    training_rows: list[dict[str, Any]] = []
    for row in positive_rows:
        training_rows.append({
            "id": row["id"],
            "label": "positive",
            "failure_mode": "good-response",
            "prompt": row["prompt"],
            "answer": row["answer"],
            "is_good_example": True,
            "source": "positive",
        })

    for row in negative_rows:
        training_rows.append({
            "id": row["id"],
            "label": "negative",
            "failure_mode": row["failure_mode"],
            "prompt": row["prompt"],
            "answer": row["answer"],
            "is_good_example": bool(row["is_good_example"]),
            "source": "negative",
        })

    return training_rows


def write_training_dataset() -> dict[str, Any]:
    rows = build_training_rows()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    manifest = {
        "schema": "neural-completion-training/v1",
        "positive_examples": sum(1 for row in rows if row["label"] == "positive"),
        "negative_examples": sum(1 for row in rows if row["label"] == "negative"),
        "total_rows": len(rows),
        "failure_modes": sorted({row["failure_mode"] for row in rows if row["label"] == "negative"}),
        "primary_targets": [
            "avoid-repeated-fragments",
            "avoid-too-short-outcomes",
            "keep-answer-relevant-and-complete",
        ],
        "inputs": {
            "positive_dataset": POSITIVE_PATH.relative_to(ROOT).as_posix(),
            "negative_dataset": NEGATIVE_PATH.relative_to(ROOT).as_posix(),
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    manifest = write_training_dataset()
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
