#!/usr/bin/env python3
"""Gate reproduzível de estabilização da IA Local do Zero."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
REPORT_DIR = ROOT / "model" / "checkpoint"
DEFAULT_REPORT = REPORT_DIR / "stabilization_report.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tail(text: str, limit: int = 5000) -> str:
    return (text or "")[-limit:]


def run_case(name: str, command: list[str], *, cwd: Path = ROOT, timeout: int = 300) -> dict:
    started = time.perf_counter()
    record = {
        "name": name,
        "command": command,
        "cwd": str(cwd),
        "started_at": utc_now(),
        "timeout_seconds": timeout,
    }
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
        record.update(
            status="passed" if completed.returncode == 0 else "failed",
            exit_code=completed.returncode,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            output_tail=tail(output),
        )
    except FileNotFoundError as error:
        record.update(
            status="failed",
            exit_code=None,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            output_tail=f"Executável não encontrado: {error.filename}",
        )
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or "") + ("\n" + error.stderr if error.stderr else "")
        record.update(
            status="failed",
            exit_code=None,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            output_tail=f"Timeout após {timeout}s.\n{tail(output)}",
        )
    return record


def commands() -> list[tuple[str, list[str], Path, int]]:
    if not PYTHON.exists():
        return [("python_environment", [str(PYTHON), "--version"], ROOT, 10)]
    report_dir = str(REPORT_DIR)
    return [
        ("python_syntax", [str(PYTHON), "-m", "py_compile",
                           "python/model_server.py", "python/evaluate_checkpoint.py",
                           "python/agent_runs.py", "python/agent_contract.py",
                           "tests/benchmark_checkpoint_suite.py"], ROOT, 90),
        ("python_unit_tests", [str(PYTHON), "-m", "unittest", "discover",
                               "-s", "tests", "-p", "test*.py"], ROOT, 600),
        ("runtime_javascript_syntax", ["node", "--check", "runtime/static/app.js"], ROOT, 60),
        ("browser_smoke", ["node", "tests/browser_smoke.cjs"], ROOT, 180),
        ("runtime_rust_tests", ["cargo", "test", "--manifest-path", "runtime/Cargo.toml"], ROOT, 600),
        ("agent_core_check", ["npm", "run", "check"], ROOT / "agent-core", 120),
        ("agent_core_tests", ["npm", "test"], ROOT / "agent-core", 300),
        ("model_suite", [str(PYTHON), "tests/benchmark_model_suite.py",
                         "--report", f"{report_dir}/model_suite.json"], ROOT, 300),
        ("workflow_suite", [str(PYTHON), "tests/benchmark_workflow_suite.py",
                            "--report", f"{report_dir}/workflow.json"], ROOT, 300),
        ("agentic_curriculum", [str(PYTHON), "tests/benchmark_agentic_curriculum.py"], ROOT, 300),
        ("agent_harness", [str(PYTHON), "tests/benchmark_agent_harness.py"], ROOT, 300),
        ("attached_books", [str(PYTHON), "tests/benchmark_attached_books.py"], ROOT, 300),
        ("deep_learning", [str(PYTHON), "tests/benchmark_deep_learning_book.py"], ROOT, 300),
        ("hf_datasets", [str(PYTHON), "tests/benchmark_hf_datasets_curriculum.py"], ROOT, 300),
        ("open_programming", [str(PYTHON), "tests/benchmark_open_programming.py"], ROOT, 300),
        ("neural_gate", [str(PYTHON), "python/evaluate_checkpoint.py",
                         "--checkpoint", "model/checkpoints/compact-08-gate-focus.pt",
                         "--eval", "model/eval_generation.jsonl", "--tokens", "128"], ROOT, 600),
    ]


def markdown(report: dict) -> str:
    lines = [
        "# Checkpoint de estabilização",
        "",
        f"Executado em: {report['finished_at']}",
        f"Status: **{report['status']}**",
        f"Etapas aprovadas: **{report['passed']}/{report['total']}**",
        "",
        "| Etapa | Resultado | Duração |",
        "|---|---:|---:|",
    ]
    for item in report["cases"]:
        lines.append(
            f"| {item['name']} | {'OK' if item['status'] == 'passed' else 'FALHOU'} "
            f"| {item.get('elapsed_seconds', 0):.3f}s |"
        )
    lines += [
        "",
        "## Critério",
        "",
        "O checkpoint só passa quando todas as etapas retornam código zero. "
        "Os benchmarks medem capacidades específicas; não representam uma prova "
        "de inteligência geral.",
        "",
        "Os detalhes completos ficam em stabilization_report.json.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Executa o gate completo de estabilização")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    cases = []
    for name, command, cwd, timeout in commands():
        print(f"[checkpoint] {name} ...", flush=True)
        result = run_case(name, command, cwd=cwd, timeout=timeout)
        cases.append(result)
        print(f"[checkpoint] {name}: {result['status']}", flush=True)
    passed = sum(item["status"] == "passed" for item in cases)
    report = {
        "schema": "stabilization-checkpoint/v1",
        "project": str(ROOT),
        "started_at": started,
        "finished_at": utc_now(),
        "status": "passed" if passed == len(cases) else "failed",
        "passed": passed,
        "total": len(cases),
        "pass_rate": round(passed / max(1, len(cases)), 3),
        "tooling": {"python": str(PYTHON), "node": shutil.which("node"), "npm": shutil.which("npm")},
        "cases": cases,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "passed": passed, "total": len(cases),
                      "report": str(args.report)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
