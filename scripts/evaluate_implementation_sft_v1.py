#!/usr/bin/env python3
"""Compare isolated implementation proposals on unseen requests and tests."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from model_server import ModelService
from proactive_implementation import parse_implementation_plan


def markdown_to_plan(answer: str) -> str:
    pattern = re.compile(r"(?ms)^### (app\.py|test_app\.py)\n```python\n(.*?)\n```\s*")
    matches = list(pattern.finditer(answer))
    if len(matches) != 2 or "".join(match.group(0) for match in matches).strip() != answer.strip():
        raise ValueError("esperados exatamente dois blocos de arquivo completos")
    files = {match.group(1): match.group(2) + "\n" for match in matches}
    if set(files) != {"app.py", "test_app.py"}:
        raise ValueError("arquivos ausentes ou repetidos")
    return json.dumps({"assumptions": [], "operations": [
        {"tool": "create_file", "arguments": {"path": path, "content": files[path]}}
        for path in ("app.py", "test_app.py")
    ]}, ensure_ascii=False)


def evaluate(checkpoint: str, cases: list[dict], output_format: str) -> list[dict]:
    service = ModelService(checkpoint)
    results = []
    for row in cases:
        prompt = row["messages"][0]["content"]
        if output_format == "markdown":
            # Score the generated artifact even if the prose relevance filter
            # dislikes it; the strict file parser and executable tests decide.
            with patch("model_server.assess_generation_quality", return_value=(True, "accepted")):
                answer = service.local_reply([{"role": "user", "content": prompt}])
        else:
            answer = service.local_reply([{"role": "user", "content": prompt}])
        generation = service.last_generation or {}
        result = {"id": row["id"], "json_valid": False, "contract_valid": False,
                  "reference_tests_pass": False, "generated_tokens": generation.get("generated_tokens"),
                  "quality_reason": generation.get("quality_reason"),
                  "quality_stop_reason": generation.get("quality_stop_reason"),
                  "stop_reason": generation.get("stop_reason"),
                  "quality_gate_result": generation.get("quality_gate_result"),
                  "answer_excerpt": str(answer or "")[:500]}
        if answer:
            try:
                plan_text = markdown_to_plan(answer) if output_format == "markdown" else answer
                value = json.loads(plan_text)
                result["json_valid"] = isinstance(value, dict)
                plan = parse_implementation_plan(plan_text, existing_paths=set(), existing_directories=set())
                result["contract_valid"] = True
                reference_answer = row.get("reference_answer") or row["messages"][-1]["content"]
                reference = json.loads(markdown_to_plan(reference_answer) if output_format == "markdown" else reference_answer)
                reference_test = next(op["arguments"]["content"] for op in reference["operations"]
                                      if op["arguments"]["path"] == "test_app.py")
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    for operation in plan["operations"]:
                        args = operation["arguments"]
                        if operation["tool"] == "create_directory":
                            (root / args["path"]).mkdir(parents=True, exist_ok=True)
                        elif operation["tool"] == "create_file":
                            path = root / args["path"]
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_text(args["content"], encoding="utf-8")
                    (root / "test_app.py").write_text(reference_test, encoding="utf-8")
                    completed = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", "test_app.py"],
                                               cwd=root, capture_output=True, text=True, timeout=10)
                    result["reference_tests_pass"] = completed.returncode == 0
                    if completed.returncode:
                        result["verification_excerpt"] = completed.stderr[-600:]
            except (ValueError, KeyError, StopIteration, subprocess.TimeoutExpired) as error:
                result["parse_error"] = str(error)[:300]
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--heldout", default="datasets/implementation_sft_v1/heldout.jsonl")
    parser.add_argument("--output", default="model/training/implementation-sft-v1/run-01/heldout_evaluation.json")
    parser.add_argument("--max-tokens", type=int, default=384)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    if args.max_tokens < 1:
        parser.error("--max-tokens precisa ser positivo")
    os.environ["IA_LOCAL_NUM_PREDICT"] = str(args.max_tokens)
    cases = [json.loads(line) for line in Path(args.heldout).read_text(encoding="utf-8").splitlines() if line]
    report = {"schema": "brasa-implementation-heldout/v1", "cases": len(cases), "max_tokens": args.max_tokens,
              "format": args.format,
              "baseline": evaluate(args.baseline, cases, args.format), "candidate": evaluate(args.candidate, cases, args.format),
              "promoted": False}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
