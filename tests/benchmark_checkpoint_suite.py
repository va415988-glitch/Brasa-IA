#!/usr/bin/env python3
"""Executa benchmark comparativo em todos os checkpoints do projeto."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def checkpoint_files(base_dir: Path):
    candidates = []
    for pattern in ("*.pt", "*.safetensors"):
        candidates.extend(sorted(base_dir.glob(pattern)))
    # Remove arquivos de metadados redundantes se houver.
    return [path for path in candidates if not path.name.endswith(".json")]


def run_checkpoint_eval(checkpoint: Path, eval_file: Path, tokenizer_file: Path, tokens: int):
    cmd = [
        # Preserve the venv launcher. Resolving the symlink can silently drop
        # the environment where PyTorch and the project dependencies live.
        sys.executable,
        str(ROOT / "python" / "evaluate_checkpoint.py"),
        "--checkpoint",
        str(checkpoint),
        "--eval",
        str(eval_file),
        "--tokenizer",
        str(tokenizer_file),
        "--tokens",
        str(tokens),
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    match = None
    if stdout:
        for line in reversed(stdout.splitlines()):
            if "resultado:" in line:
                match = line
                break
    if not match:
        match = "resultado: 0/0"
    try:
        passed_text, total_text = match.split("resultado:", 1)[1].strip().split("/", 1)
        passed = int(passed_text.strip())
        total = int(total_text.strip())
        rate = (passed / total) if total else 0.0
    except Exception:
        passed = 0
        total = 0
        rate = 0.0
    return {
        "checkpoint": checkpoint.name,
        "passed": passed,
        "total": total,
        "pass_rate": round(rate, 3),
        "returncode": proc.returncode,
        "stdout_tail": stdout.splitlines()[-8:] if stdout else [],
        "stderr_tail": stderr.splitlines()[-8:] if stderr else [],
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmarks comparativos de checkpoints")
    parser.add_argument("--checkpoint-dir", type=Path, default=ROOT / "model" / "checkpoints")
    parser.add_argument("--eval", type=Path, default=ROOT / "model" / "eval_generation.jsonl")
    parser.add_argument("--tokenizer", type=Path, default=ROOT / "model" / "tokenizer.json")
    parser.add_argument("--tokens", type=int, default=32)
    parser.add_argument("--output", type=Path, default=ROOT / "model" / "checkpoint_benchmark.json")
    args = parser.parse_args()

    checkpoints = checkpoint_files(args.checkpoint_dir)
    if not checkpoints:
        report = {"count": 0, "checkpoints": [], "ranking": []}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0)

    rows = []
    for checkpoint in checkpoints:
        rows.append(run_checkpoint_eval(checkpoint, args.eval, args.tokenizer, args.tokens))

    rows.sort(key=lambda item: (item["pass_rate"], item["passed"]), reverse=True)
    report = {
        "count": len(rows),
        "checkpoints": rows,
        "ranking": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
