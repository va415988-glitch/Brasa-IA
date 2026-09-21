"""Registro e preparação de traces do agente local.

Traces de execução são dados de supervisão para o planejador: registram o pedido,
os candidatos, a decisão, o resultado e a próxima etapa. Eles ficam separados
do conhecimento factual e podem ser exportados para JSONL depois de revisão.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRACE_PATH = ROOT / "logs" / "agent_traces.jsonl"
DEFAULT_SFT_PATH = ROOT / "corpus" / "training" / "planner_sft.jsonl"
MAX_TEXT = 4000


def _clip(value: Any, limit: int = MAX_TEXT) -> Any:
    """Reduz recursivamente os dados do log e omite campos sensíveis comuns."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…[truncado]"
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(token in normalized for token in ("password", "secret", "token", "api_key", "authorization")):
                redacted[str(key)] = "[omitido]"
            else:
                redacted[str(key)] = _clip(item, limit)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_clip(item, limit) for item in value[:40]]
    return value


class TraceRecorder:
    """Appender pequeno e seguro para traces locais em JSONL."""

    def __init__(self, path: Path | str = DEFAULT_TRACE_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()

    @staticmethod
    def new_id(prefix: str = "trace") -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    def record(self, event: str, trace_id: str, **fields: Any) -> dict[str, Any]:
        payload = {
            "schema": "agent-trace/v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "trace_id": trace_id,
            **_clip(fields),
        }
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line)
        except OSError:
            # Telemetria local nunca pode derrubar uma requisição do usuário.
            pass
        return payload

    def start(self, question: str, request_id: str | None = None) -> str:
        trace_id = self.new_id()
        self.record(
            "turn_started",
            trace_id,
            request_id=request_id,
            question=question,
        )
        return trace_id

    def plan(self, trace_id: str, question: str, call: dict[str, Any], elapsed_ms: float) -> None:
        self.record(
            "plan_selected",
            trace_id,
            question=question,
            tool=call.get("tool"),
            arguments=call.get("arguments", {}),
            reason=call.get("reason", ""),
            contract_version=call.get("contract_version"),
            capabilities=call.get("capabilities", []),
            risk=call.get("risk"),
            requires_approval=call.get("requires_approval", False),
            idempotency_key=call.get("idempotency_key"),
            planner=call.get("planner", {}),
            elapsed_ms=round(elapsed_ms, 3),
        )

    def tool_result(self, trace_id: str, result: dict[str, Any], step: int) -> None:
        self.record(
            "tool_result",
            trace_id,
            step=step,
            tool=result.get("tool"),
            ok=result.get("ok"),
            data=result.get("data"),
            error=result.get("error"),
            verification_done=result.get("verification_done", False),
            lifecycle=result.get("lifecycle", {}),
            artifacts=result.get("artifacts", []),
        )

    def completion(self, trace_id: str, response: dict[str, Any], elapsed_ms: float, steps: int) -> None:
        self.record(
            "turn_completed",
            trace_id,
            backend=response.get("backend"),
            status=(response.get("agent") or {}).get("status"),
            steps=steps,
            text=response.get("text", ""),
            elapsed_ms=round(elapsed_ms, 3),
        )


def _load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("schema") == "agent-trace/v1":
            events.append(item)
    return events


def export_planner_sft(input_path: Path | str = DEFAULT_TRACE_PATH, output_path: Path | str = DEFAULT_SFT_PATH) -> dict[str, int]:
    """Converte somente decisões completas em exemplos de seleção de ferramenta.

    O formato é compatível com os datasets conversacionais existentes, mas o
    alvo do assistente é JSON estruturado. Isso permite trocar o planejador por
    um modelo treinado sem alterar os executores.
    """
    events = _load_events(Path(input_path))
    starts: dict[str, dict[str, Any]] = {}
    plans: dict[str, dict[str, Any]] = {}
    completed: set[str] = set()
    for event in events:
        trace_id = event.get("trace_id")
        if not trace_id:
            continue
        if event.get("event") == "turn_started":
            starts[trace_id] = event
        elif event.get("event") == "plan_selected":
            plans[trace_id] = event
        elif event.get("event") == "turn_completed":
            completed.add(trace_id)

    rows = []
    for trace_id, plan in plans.items():
        start = starts.get(trace_id)
        if not start or trace_id not in completed or not plan.get("tool"):
            continue
        target = {
            "id": f"local-{plan['tool']}",
            "tool": plan["tool"],
            "arguments": plan.get("arguments", {}),
        }
        rows.append({
            "messages": [
                {"role": "system", "content": "Selecione uma ferramenta do catálogo e devolva uma chamada JSON válida."},
                {"role": "user", "content": start.get("question", "")},
                {"role": "assistant", "tool_call": {"name": target["tool"], "arguments": target["arguments"]}},
            ],
            "metadata": {"trace_id": trace_id, "source": "local-agent-trace", "planner": plan.get("planner", {})},
        })
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"events": len(events), "examples": len(rows)}


def report(input_path: Path | str = DEFAULT_TRACE_PATH) -> dict[str, Any]:
    events = _load_events(Path(input_path))
    plans = [event for event in events if event.get("event") == "plan_selected"]
    results = [event for event in events if event.get("event") == "tool_result"]
    completed = [event for event in events if event.get("event") == "turn_completed"]
    by_tool: dict[str, int] = {}
    for event in plans:
        tool = str(event.get("tool") or "unknown")
        by_tool[tool] = by_tool.get(tool, 0) + 1
    successes = sum(1 for event in results if event.get("ok") is True)
    return {
        "schema": "agent-trace/v1",
        "events": len(events),
        "plans": len(plans),
        "tool_results": len(results),
        "completed_turns": len(completed),
        "tool_selection": dict(sorted(by_tool.items())),
        "tool_success_rate": round(successes / len(results), 3) if results else None,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Audita ou exporta traces do agente local")
    parser.add_argument("command", choices=("report", "export"))
    parser.add_argument("--input", default=str(DEFAULT_TRACE_PATH))
    parser.add_argument("--output", default=str(DEFAULT_SFT_PATH))
    args = parser.parse_args()
    result = report(args.input) if args.command == "report" else export_planner_sft(args.input, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
