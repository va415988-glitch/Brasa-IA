#!/usr/bin/env python3
"""Select an experimental checkpoint using validation behavior, never heldout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from evaluate_implementation_sft_v1 import evaluate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--validation", required=True)
    parser.add_argument("--max-tokens", type=int, default=384)
    args = parser.parse_args()
    if args.max_tokens < 1:
        parser.error("--max-tokens precisa ser positivo")
    os.environ["IA_LOCAL_NUM_PREDICT"] = str(args.max_tokens)
    run_dir = Path(args.run_dir)
    cases = [json.loads(line) for line in Path(args.validation).read_text(encoding="utf-8").splitlines() if line]
    paths = [Path(args.baseline), *sorted((run_dir / "snapshots").glob("*.safetensors"))]
    if len(paths) < 2:
        raise ValueError("snapshots de validação ausentes")
    records = []
    for path in paths:
        results = evaluate(str(path), cases, "json")
        metadata = json.loads(path.with_name(path.name + ".json").read_text(encoding="utf-8"))
        score = [sum(bool(item[key]) for item in results)
                 for key in ("reference_tests_pass", "contract_valid", "json_valid")]
        record = {"checkpoint": str(path), "step": metadata.get("steps"),
                  "validation_loss": metadata.get("validation_loss", metadata.get("best_val_loss")),
                  "score": score, "cases": results}
        records.append(record)
        print(json.dumps({key: record[key] for key in ("checkpoint", "step", "validation_loss", "score")}, ensure_ascii=False), flush=True)
    selected = max(records, key=lambda item: (*item["score"], -(item["validation_loss"] or float("inf"))))
    report = {"schema": "brasa-implementation-quality-selection/v1", "validation_cases": len(cases),
              "max_tokens": args.max_tokens, "selected": selected["checkpoint"],
              "selected_score": selected["score"], "records": records,
              "heldout_consulted": False}
    (run_dir / "quality_selection.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected": selected["checkpoint"], "score": selected["score"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
