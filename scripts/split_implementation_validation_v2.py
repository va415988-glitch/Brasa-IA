#!/usr/bin/env python3
"""Fix a diverse validation split before generation-aware checkpoint selection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "datasets/implementation_sft_compact_extended_v1"
OUTPUT = ROOT / "datasets/implementation_sft_compact_extended_v2"
VALIDATION_IDS = {"palindrome", "rectangle_area", "last_word", "vowel_count"}


def main() -> None:
    train = [json.loads(line) for line in (SOURCE / "train.jsonl").read_text(encoding="utf-8").splitlines() if line]
    original_validation = [json.loads(line) for line in (SOURCE / "validation.jsonl").read_text(encoding="utf-8").splitlines() if line]
    heldout = [json.loads(line) for line in (SOURCE / "heldout.jsonl").read_text(encoding="utf-8").splitlines() if line]
    validation = original_validation + [row for row in train if row["id"] in VALIDATION_IDS]
    train = [row for row in train if row["id"] not in VALIDATION_IDS]
    ids = [{row["id"] for row in split} for split in (train, validation, heldout)]
    if ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2]:
        raise ValueError("splits sobrepostos")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": "brasa-implementation-validation-split/v2", "validation_ids": sorted(ids[1]),
                "counts": {"train": len(train), "validation": len(validation), "heldout": len(heldout)},
                "sha256": {}}
    for name, rows in (("train", train), ("validation", validation), ("heldout", heldout)):
        path = OUTPUT / f"{name}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        manifest["sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
