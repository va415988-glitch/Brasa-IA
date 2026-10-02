#!/usr/bin/env python3
"""Executa a bateria Top 5 adaptada contra uma API AgentCore já iniciada."""
from __future__ import annotations

import argparse
import errno
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "agent_tool_eval_top5.json"


def post_json(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def observed_tools(events: list[dict]) -> list[str]:
    """Reúne chamadas reais nos formatos de evento usados pelo AgentCore e pelo runtime."""
    tools: list[str] = []
    for event in events:
        kind = str(event.get("kind", ""))
        payload = event.get("payload") or {}

        # A inspeção inicial usa RuntimeHttpPorts.workspace.inspect(), que chama
        # o endpoint inspect_project antes do loop do planejador.
        if kind == "workspace.inspected":
            tools.append("inspect_project")

        # Chamadas do planejador aparecem como transições brain.state; o runtime
        # publica eventos tool.started/tool.completed no feed legado.
        state = str(payload.get("state", ""))
        if kind == "brain.state" and state in {"executing", "verifying"}:
            name = payload.get("tool")
            if isinstance(name, str) and name:
                tools.append(name)

        name = payload.get("tool") or event.get("tool")
        if kind.startswith("tool.") and isinstance(name, str) and name:
            tools.append(name)

        operation = str(
            event.get("task_id")
            or event.get("taskId")
            or event.get("operation")
            or payload.get("legacy_operation")
            or ""
        )
        match = re.match(r"tool:([^:]+)(?::|$)", operation)
        if match:
            tools.append(match.group(1))

    # Preserva a ordem observada e evita contar transições da mesma chamada duas vezes.
    return list(dict.fromkeys(tools))


def inspect_case(case: dict, response: dict) -> dict:
    report = response.get("report") or {}
    text = str(report.get("finalText") or "")
    events = report.get("events") or []
    attempted_read_paths = [
        str(event.get("detail")) for event in events
        if event.get("kind") == "analysis.file.read" and event.get("detail")
    ]
    read_paths = []
    for event in events:
        payload = event.get("payload") or {}
        if event.get("kind") != "brain.state" or payload.get("state") != "verifying" or payload.get("ok") is not True:
            continue
        for evidence in payload.get("evidence") or []:
            match = re.match(r"(?:read_file|extract_document_text|inspect_media|inspect_code):\s*(.+)$", str(evidence))
            if match:
                read_paths.append(match.group(1))
    tools_called = observed_tools(events)
    expect = case["expect"]
    checks: dict[str, bool] = {}
    if "status" in expect:
        checks["status"] = report.get("status") == expect["status"]
    for field, values in (("text_all", "text_all"), ("text_none", "text_none")):
        if field in expect:
            checks[field] = all(value.lower() in text.lower() for value in expect[field]) if field == "text_all" else all(value.lower() not in text.lower() for value in expect[field])
    if "read_paths_all" in expect:
        checks["read_paths_all"] = all(path in read_paths for path in expect["read_paths_all"])
    if "read_paths_none" in expect:
        checks["read_paths_none"] = all(path not in read_paths for path in expect["read_paths_none"])
    if "tools_called_all" in expect:
        checks["tools_called_all"] = all(tool in tools_called for tool in expect["tools_called_all"])
    if "tools_called_any" in expect:
        checks["tools_called_any"] = any(tool in tools_called for tool in expect["tools_called_any"])
    if "tools_called_none" in expect:
        checks["tools_called_none"] = all(tool not in tools_called for tool in expect["tools_called_none"])
    if "inspection" in expect:
        inspection = report.get("inspection") or {}
        checks["inspection"] = all(isinstance(inspection.get(key), list) for key in expect["inspection"])
    if "text_regex" in expect:
        checks["text_regex"] = re.search(expect["text_regex"], text, re.IGNORECASE) is not None
    mutation_events = [event for event in events if event.get("kind") == "mutation.effect.observed"]
    checks["no_mutations"] = case.get("read_only") is not True or not mutation_events
    return {
        "id": case["id"],
        "source_dataset": case["source_dataset"],
        "status": report.get("status", "no-report"),
        "passed": bool(checks) and all(checks.values()),
        "checks": checks,
        "read_paths": read_paths,
        "attempted_read_paths": attempted_read_paths,
        "tools_called": tools_called,
        "mutation_events": len(mutation_events),
        "final_text": text,
        "error": report.get("error"),
        "elapsed_ms": response.get("elapsed_ms"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:3000/api/v1/agent/pursue")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--output", type=Path, default=Path("/tmp/agent-tool-eval-top5-results.json"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    results = []
    for index, case in enumerate(manifest["cases"], start=1):
        payload = {
            "prompt": case["prompt"],
            "objective": case["objective"],
            "workspaceRoot": args.workspace,
            "operationId": f"agent-core-top5-{int(time.time())}-{index}",
        }
        started = time.monotonic()
        connection_refused = False
        try:
            response = post_json(args.url, payload, args.timeout)
            result = inspect_case(case, response)
            result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            reason = getattr(error, "reason", None)
            connection_refused = (
                isinstance(reason, ConnectionRefusedError)
                or getattr(reason, "errno", None) == errno.ECONNREFUSED
            )
            result = {"id": case["id"], "source_dataset": case["source_dataset"], "status": "transport-error", "passed": False, "checks": {}, "error": str(error), "elapsed_ms": round((time.monotonic() - started) * 1000)}
        results.append(result)
        print(f"{'PASS' if result['passed'] else 'FAIL'} {result['id']}: {result['status']} ({result['elapsed_ms']} ms)")
        if not result["passed"]:
            print("  Checks:", json.dumps(result.get("checks", {}), ensure_ascii=False))
            if result.get("error"):
                print("  Erro:", result["error"])
        if connection_refused:
            print("Servidor local indisponível. Inicie ./start.sh em outro terminal, mantenha-o aberto e execute o benchmark novamente.")
            for skipped in manifest["cases"][index:]:
                results.append({
                    "id": skipped["id"], "source_dataset": skipped["source_dataset"],
                    "status": "not-run-service-unavailable", "passed": False,
                    "checks": {}, "error": "A bateria parou porque o servidor local recusou a conexão.",
                })
            break
    output = {
        "schema": "agent-tool-eval-result/v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "url": args.url,
        "workspace": args.workspace,
        "evaluation_only": True,
        "passed": sum(result["passed"] for result in results),
        "total": len(manifest["cases"]),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Resultado: {output['passed']}/{output['total']}. JSON: {args.output}")
    return 0 if output["passed"] == output["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
