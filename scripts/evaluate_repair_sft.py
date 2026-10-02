#!/usr/bin/env python3
"""Score isolated repair proposals on disjoint tasks without source repositories."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from model_server import ModelService
from proactive_implementation import parse_implementation_plan


def score(answer: str | None, row: dict) -> dict:
    result = {"json_valid": False, "contract_valid": False, "target_edit_exact": False}
    if not answer:
        return result
    try:
        result["json_valid"] = isinstance(json.loads(answer), dict)
    except ValueError:
        pass
    reference_answer = row.get("reference_answer") or row["messages"][-1]["content"]
    reference = json.loads(reference_answer)["operations"][0]["arguments"]
    path, source = reference["path"], reference["old_text"]
    try:
        plan = parse_implementation_plan(answer, existing_paths={path}, readable_sources={path: source})
        result["contract_valid"] = True
        if len(plan["operations"]) == 1 and plan["operations"][0]["tool"] == "edit_file":
            args = plan["operations"][0]["arguments"]
            result["target_edit_exact"] = (
                args["path"] == path and
                source.replace(args["old_text"], args["new_text"], 1) == reference["new_text"]
            )
    except ValueError as error:
        result["parse_error"] = str(error)[:250]
    return result


def evaluate(checkpoint: str, cases: list[dict]) -> list[dict]:
    service = ModelService(checkpoint)
    results = []
    for row in cases:
        prompt = row["messages"][0]["content"]
        answer = service.local_reply([{"role": "user", "content": prompt}])
        generation = service.last_generation or {}
        result = {"id": row["id"], "group": row["provenance"]["group"],
                  "quality_reason": generation.get("quality_reason"),
                  "generated_tokens": generation.get("generated_tokens"),
                  "answer_excerpt": str(answer or "")[:500], **score(answer, row)}
        results.append(result)
        print(json.dumps({key: result[key] for key in ("id", "json_valid", "contract_valid", "target_edit_exact")}), flush=True)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--heldout", default="datasets/implementation_repair_sft_v1/heldout.jsonl")
    parser.add_argument("--output", default="model/training/implementation-repair-sft-v1/run-01/heldout_evaluation.json")
    parser.add_argument("--max-tokens", type=int, default=1150)
    args = parser.parse_args()
    if args.max_tokens < 1:
        parser.error("--max-tokens precisa ser positivo")
    os.environ["IA_LOCAL_NUM_PREDICT"] = str(args.max_tokens)
    cases = [json.loads(line) for line in Path(args.heldout).read_text(encoding="utf-8").splitlines() if line]
    baseline = evaluate(args.baseline, cases) if args.baseline else []
    candidate = evaluate(args.candidate, cases)
    report = {"schema": "brasa-repair-heldout/v1", "heldout": args.heldout,
              "cases": len(cases), "max_tokens": args.max_tokens,
              "baseline_checkpoint": args.baseline, "candidate_checkpoint": args.candidate,
              "baseline": baseline, "candidate": candidate,
              "totals": {name: {key: sum(bool(row[key]) for row in results)
                                for key in ("json_valid", "contract_valid", "target_edit_exact")}
                         for name, results in (("baseline", baseline), ("candidate", candidate)) if results},
              "promoted": False,
              "limits": ["Edição exata contra o patch de referência; não reproduz testes nos repositórios originais."]}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
