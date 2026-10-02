#!/usr/bin/env python3
"""Select a repair SFT snapshot by unseen validation behavior before heldout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from evaluate_repair_sft import evaluate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--validation", default="datasets/implementation_repair_sft_v2/validation.jsonl")
    parser.add_argument("--max-tokens", type=int, default=1150)
    args = parser.parse_args()
    run = Path(args.run_dir)
    snapshots = sorted((run / "snapshots").glob("step-*.safetensors"))
    if not snapshots:
        parser.error("nenhum snapshot de validação encontrado")
    os.environ["IA_LOCAL_NUM_PREDICT"] = str(args.max_tokens)
    cases = [json.loads(line) for line in Path(args.validation).read_text(encoding="utf-8").splitlines() if line]
    history = {row["step"]: row["validation_loss"]
               for row in json.loads((run / "history.json").read_text(encoding="utf-8"))}
    candidates = []
    for checkpoint in snapshots:
        step = int(checkpoint.stem.split("-")[-1])
        cached = run / f"validation_step{step}.json"
        if cached.is_file():
            previous = json.loads(cached.read_text(encoding="utf-8"))
            if (previous.get("heldout") == args.validation and previous.get("max_tokens") == args.max_tokens
                    and previous.get("candidate_checkpoint") == str(checkpoint)):
                results = previous["candidate"]
            else:
                results = evaluate(str(checkpoint), cases)
        else:
            results = evaluate(str(checkpoint), cases)
        totals = {key: sum(bool(row[key]) for row in results)
                  for key in ("json_valid", "contract_valid", "target_edit_exact")}
        candidates.append({"checkpoint": str(checkpoint), "step": step,
                           "validation_loss": history[step], "totals": totals,
                           "cases": results})
    selected = max(candidates, key=lambda item: (
        item["totals"]["target_edit_exact"], item["totals"]["contract_valid"],
        item["totals"]["json_valid"], -item["validation_loss"],
    ))
    report = {"schema": "brasa-repair-checkpoint-selection/v1", "validation": args.validation,
              "max_tokens": args.max_tokens, "candidate_count": len(candidates),
              "selected_checkpoint": selected["checkpoint"], "selected_step": selected["step"],
              "selection_order": ["target_edit_exact", "contract_valid", "json_valid", "validation_loss"],
              "candidates": candidates, "heldout_used_for_selection": False, "promoted": False}
    output = run / "quality_selection.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected_checkpoint": report["selected_checkpoint"],
                      "totals": selected["totals"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
