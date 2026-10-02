"""Registro e preparação de traces do agente local.

Traces de execução são dados de supervisão para o planejador: registram o pedido,
os candidatos, a decisão, o resultado e a próxima etapa. Eles ficam separados
do conhecimento factual e podem ser exportados para JSONL depois de revisão.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRACE_PATH = ROOT / "logs" / "agent_traces.jsonl"
DEFAULT_CANDIDATE_PATH = ROOT / "corpus" / "raw" / "workflow_planner_candidates.jsonl"
# Compatibility name for callers from before exports were moved behind review.
DEFAULT_SFT_PATH = DEFAULT_CANDIDATE_PATH
MAX_TEXT = 4000
MAX_PRIOR_ACTIONS = 16
MAX_PRIOR_OBSERVATIONS = 8

_SECRET_PATTERNS = (
    (re.compile(r"""(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|authorization)\b(\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)"""), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)\bBearer\s+\S+"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[REDACTED_JWT]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
)


def _redact_text(value: str) -> str:
    text = str(value)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _clip(value: Any, limit: int = MAX_TEXT) -> Any:
    """Reduz recursivamente os dados do log e omite campos sensíveis comuns."""
    if isinstance(value, str):
        value = _redact_text(value)
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


def _compact_candidate_value(value: Any, limit: int = 600) -> Any:
    """Limita a largura de observações para caber em um único exemplo útil."""
    if isinstance(value, str):
        return _clip(value, limit)
    if isinstance(value, dict):
        return {str(key): _compact_candidate_value(item, limit)
                for key, item in list(value.items())[:20]}
    if isinstance(value, (list, tuple)):
        return [_compact_candidate_value(item, limit) for item in value[:8]]
    return value


def _redact_brave_context(tool: str, data: Any) -> Any:
    """Keep retrieved Brave payloads out of durable operational traces."""
    if tool not in {"search_web", "research_web"} or not isinstance(data, dict):
        return data
    has_brave_context = data.get("source") == "brave-llm-context-api"
    for field in ("results", "search_results"):
        items = data.get(field)
        if isinstance(items, list) and any(
            isinstance(item, dict) and (item.get("context_text") or item.get("source_metadata"))
            for item in items
        ):
            has_brave_context = True
    pages = data.get("pages")
    if isinstance(pages, list) and any(
        isinstance(page, dict) and page.get("pre_extracted") is True for page in pages
    ):
        has_brave_context = True
    if not has_brave_context:
        return data
    redacted = dict(data)
    for field in ("results", "search_results", "pages", "answer", "citation_ids", "grounded"):
        redacted.pop(field, None)
    redacted["context_payload_redacted"] = True
    return redacted


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
        tool = result.get("tool")
        data = _redact_brave_context(str(tool or ""), result.get("data"))
        self.record(
            "tool_result",
            trace_id,
            step=step,
            tool=tool,
            ok=result.get("ok"),
            data=data,
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


def _trace_context(events: list[dict[str, Any]], start: dict[str, Any], terminal: dict[str, Any], trace_id: str) -> list[dict[str, Any]]:
    """Cria um candidato supervisionado por decisão, preservando observações anteriores.

    O resultado é material bruto para revisão, nunca uma aprovação automática
    de qualidade ou segurança da trajetória.
    """
    objective = str(start.get("question") or "")
    previous_actions: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    for event in events:
        kind = event.get("event")
        if kind == "plan_selected" and event.get("tool"):
            # Traces antigos podem conter uma chamada emitida sem evento de
            # turno inicial completo. Sem o objetivo do usuário, o rótulo não
            # ensina uma decisão reproduzível e deve ficar fora do corpus.
            if not objective.strip():
                previous_actions.append({
                    "tool": str(event["tool"]),
                    "arguments": _clip(event.get("arguments") if isinstance(event.get("arguments"), dict) else {}),
                    "reason": str(event.get("reason") or "")[:1000],
                })
                continue
            context = _compact_candidate_value({
                "objective": objective,
                "previous_actions": previous_actions[-MAX_PRIOR_ACTIONS:],
                "observations": observations[-MAX_PRIOR_OBSERVATIONS:],
                "available_tool_candidates": (event.get("planner") or {}).get("candidates", []),
                "instruction": "Escolha a próxima ação com base no objetivo e nas evidências observadas; não presuma resultados ainda não verificados.",
            })
            context["objective"] = _clip(objective, 1800)
            # O usuário recebe JSON estruturado. Nunca corte a string serializada
            # no meio: remova primeiro o contexto mais antigo e só então reduza
            # o objetivo, preservando a observação mais recente.
            context_text = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            while len(context_text) > MAX_TEXT - 64:
                if len(context.get("observations", [])) > 1:
                    context["observations"].pop(0)
                elif len(context.get("previous_actions", [])) > 1:
                    context["previous_actions"].pop(0)
                elif context.get("previous_actions"):
                    context["previous_actions"].pop(0)
                elif context.get("observations"):
                    context["observations"][-1] = _compact_candidate_value(context["observations"][-1], 160)
                elif isinstance(context.get("available_tool_candidates"), list) and context["available_tool_candidates"]:
                    context["available_tool_candidates"].pop()
                else:
                    current = str(context.get("objective") or "")
                    if not current:
                        break
                    context["objective"] = current[:max(0, len(current) - max(128, len(context_text) - MAX_TEXT + 64))]
                context_text = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            target = _clip({
                "name": str(event["tool"]),
                "arguments": event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
            })
            rows.append({
                "schema": "workflow-planner-candidate/v1",
                "messages": [
                    {"role": "system", "content": "Planeje uma única próxima ação de ferramenta. O contexto do usuário contém o objetivo, as ações anteriores e as observações reais. Selecione apenas uma ferramenta disponível e use argumentos válidos."},
                    {"role": "user", "content": context_text},
                    {"role": "assistant", "tool_call": target},
                ],
                "metadata": {
                    "trace_id": event.get("trace_id"),
                    "decision_index": len(rows) + 1,
                    "source": f"workflow-trace:{trace_id}",
                "planner": _clip(event.get("planner", {})),
                    "trajectory_status": terminal.get("status"),
                    "trajectory_backend": terminal.get("backend"),
                    "review_status": "pending",
                    "requires_human_review": True,
                },
            })
            previous_actions.append({
                "tool": str(event["tool"]),
                "arguments": _clip(event.get("arguments") if isinstance(event.get("arguments"), dict) else {}),
                "reason": str(event.get("reason") or "")[:1000],
            })
        elif kind == "tool_result":
            observations.append({
                "step": event.get("step"),
                "tool": event.get("tool"),
                "ok": event.get("ok"),
                "data": _clip(event.get("data", {})),
                "error": _clip(event.get("error")),
                "verification_done": event.get("verification_done", False),
                "artifacts": _clip(event.get("artifacts", [])),
            })

    return rows


def export_workflow_candidates(input_path: Path | str = DEFAULT_TRACE_PATH, output_path: Path | str = DEFAULT_CANDIDATE_PATH) -> dict[str, int]:
    """Exporta todas as decisões contextualizadas para revisão humana fora do treino."""
    events = _load_events(Path(input_path))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        trace_id = event.get("trace_id")
        if not trace_id:
            continue
        grouped.setdefault(str(trace_id), []).append(event)

    rows: list[dict[str, Any]] = []
    trajectories = 0
    skipped_missing_objective = 0
    for trace_id, trajectory in grouped.items():
        start = next((event for event in trajectory if event.get("event") == "turn_started"), None)
        terminal = next((event for event in reversed(trajectory) if event.get("event") == "turn_completed"), None)
        if not start or not terminal:
            continue
        trajectory_rows = _trace_context(trajectory, start, terminal, trace_id)
        planned_decisions = sum(
            1 for event in trajectory
            if event.get("event") == "plan_selected" and event.get("tool")
        )
        skipped_missing_objective += max(0, planned_decisions - len(trajectory_rows))
        if trajectory_rows:
            trajectories += 1
            rows.extend(trajectory_rows)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(_clip(row), ensure_ascii=False) + "\n")
    return {
        "events": len(events),
        "completed_trajectories": trajectories,
        "decision_candidates": len(rows),
        "examples": len(rows),
        "pending_human_review": len(rows),
        "skipped_missing_objective": skipped_missing_objective,
    }


def export_planner_sft(input_path: Path | str = DEFAULT_TRACE_PATH, output_path: Path | str = DEFAULT_CANDIDATE_PATH) -> dict[str, int]:
    """Compatibility wrapper; exports review candidates, not active training data."""
    return export_workflow_candidates(input_path, output_path)


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
    parser.add_argument("command", choices=("report", "export", "export-workflow"))
    parser.add_argument("--input", default=str(DEFAULT_TRACE_PATH))
    parser.add_argument("--output", default=str(DEFAULT_CANDIDATE_PATH))
    args = parser.parse_args()
    result = report(args.input) if args.command == "report" else export_workflow_candidates(args.input, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
