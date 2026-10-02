#!/usr/bin/env python3
"""Evaluate an isolated OpenAI-compatible reference on Brasa's heldout tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from proactive_implementation import parse_implementation_plan


def generate(endpoint: str, model: str, prompt: str, max_tokens: int) -> tuple[str, dict]:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": max_tokens}
    url = endpoint.rstrip("/") + "/v1/chat/completions"
    payload = json.dumps(body).encode("utf-8")
    request = Request(url, data=payload,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=300) as response:
        data = json.load(response)
    return data["choices"][0]["message"]["content"], data.get("usage", {})


def score(answer: str, row: dict) -> dict:
    result = {"json_valid": False, "contract_valid": False, "reference_tests_pass": False,
              "generated_tests_pass": False}
    try:
        try:
            result["json_valid"] = isinstance(json.loads(answer), dict)
        except ValueError:
            pass
        plan = parse_implementation_plan(answer, existing_paths=set(), existing_directories=set())
        result["contract_valid"] = True
        reference = json.loads(row["reference_answer"])
        reference_test = next(op["arguments"]["content"] for op in reference["operations"]
                              if op["arguments"]["path"] == "test_app.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for operation in plan["operations"]:
                args = operation["arguments"]
                path = root / args["path"]
                if operation["tool"] == "create_directory":
                    path.mkdir(parents=True, exist_ok=True)
                elif operation["tool"] == "create_file":
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(args["content"], encoding="utf-8")
            generated_test = root / "test_app.py"
            if generated_test.is_file():
                generated_run = subprocess.run(
                    [sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", "test_app.py"],
                    cwd=root, capture_output=True, text=True, timeout=10,
                )
                result["generated_tests_pass"] = generated_run.returncode == 0 and "Ran 0 tests" not in generated_run.stderr
            (root / "test_app.py").write_text(reference_test, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", "test_app.py"],
                cwd=root, capture_output=True, text=True, timeout=10,
            )
            result["reference_tests_pass"] = completed.returncode == 0
            if completed.returncode:
                result["verification_excerpt"] = completed.stderr[-600:]
    except (ValueError, KeyError, StopIteration, subprocess.TimeoutExpired) as error:
        result["parse_error"] = str(error)[:300]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:3107")
    parser.add_argument("--model", required=True)
    parser.add_argument("--heldout", default="datasets/implementation_sft_compact_extended_v2/heldout.jsonl")
    parser.add_argument("--output", default="planning/baseline-v1/REFERENCE_IMPLEMENTATION_COMPARISON.json")
    parser.add_argument("--max-tokens", type=int, default=1024)
    args = parser.parse_args()
    cases = [json.loads(line) for line in Path(args.heldout).read_text(encoding="utf-8").splitlines() if line]
    results = []
    for row in cases:
        prompt = row["messages"][0]["content"]
        start = time.monotonic()
        answer, usage = generate(args.endpoint, args.model, prompt, args.max_tokens)
        result = {"id": row["id"], "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                  "elapsed_seconds": round(time.monotonic() - start, 2), "usage": usage,
                  "answer": answer, **score(answer, row)}
        results.append(result)
        print(json.dumps({key: result[key] for key in ("id", "json_valid", "contract_valid", "generated_tests_pass", "reference_tests_pass", "elapsed_seconds")}, ensure_ascii=False), flush=True)
    report = {"schema": "brasa-reference-implementation/v1", "model": args.model,
              "endpoint": args.endpoint,
              "heldout": args.heldout, "max_tokens": args.max_tokens, "cases": results,
              "totals": {key: sum(bool(row[key]) for row in results)
                         for key in ("json_valid", "contract_valid", "generated_tests_pass", "reference_tests_pass")}}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
