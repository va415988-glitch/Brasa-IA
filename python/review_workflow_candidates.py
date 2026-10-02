"""Revisa manualmente ações candidatas do planejador local.

Os candidatos brutos permanecem imutáveis. Decisões são registradas em um
ledger append-only e o dataset aprovado é materializado separadamente para
que o pipeline de aprendizado nunca confunda coleta com aprovação humana.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_traces import DEFAULT_TRACE_PATH, _clip, _load_events, _redact_text


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATES = ROOT / "corpus" / "raw" / "workflow_planner_candidates.jsonl"
DEFAULT_REVIEWS = ROOT / "corpus" / "review" / "workflow_planner_decisions.jsonl"
DEFAULT_APPROVED = ROOT / "corpus" / "review" / "workflow_planner_approved.jsonl"
_REVIEW_LOCK = threading.Lock()
_TRACE_CACHE_LOCK = threading.Lock()
_TRACE_CACHE_KEY: tuple[int, int] | None = None
_TRACE_EVENTS_BY_ID: dict[str, list[dict[str, Any]]] = {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError(f"JSON inválido em {path}:{number}: {error.msg}") from error
            if not isinstance(value, dict):
                raise ValueError(f"Registro não é um objeto JSON em {path}:{number}.")
            rows.append(value)
    return rows


def _candidate_id(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return f"{metadata.get('trace_id', '')}#{metadata.get('decision_index', '')}"


def _candidate_parts(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    messages = row.get("messages") or []
    user_message = next((item for item in messages if item.get("role") == "user"), {})
    target_message = next((item for item in messages if item.get("role") == "assistant" and item.get("tool_call")), {})
    try:
        context = json.loads(user_message.get("content") or "{}")
    except (TypeError, json.JSONDecodeError):
        context = {}
    target = target_message.get("tool_call") or {}
    return context if isinstance(context, dict) else {}, target if isinstance(target, dict) else {}


def _latest_reviews(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        key = str(row.get("candidate_id") or "")
        if key:
            latest[key] = row
    return latest


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _materialize_approved(
    candidates_path: Path,
    reviews_path: Path,
    approved_path: Path,
) -> int:
    candidates = _read_jsonl(candidates_path)
    reviews = _latest_reviews(reviews_path)
    approved: list[dict[str, Any]] = []
    for original in candidates:
        decision = reviews.get(_candidate_id(original))
        if not decision or decision.get("review_status") != "approved":
            continue
        row = json.loads(json.dumps(original))
        metadata = row.setdefault("metadata", {})
        metadata.update({
            "review_status": "approved",
            "requires_human_review": True,
            "human_reviewer": decision.get("reviewer"),
            "reviewed_at": decision.get("reviewed_at"),
            "review_rationale": decision.get("rationale"),
        })
        approved.append(row)
    _write_jsonl_atomic(approved_path, approved)
    return len(approved)


def _display_candidate(row: dict[str, Any], decision: dict[str, Any] | None = None) -> None:
    context, target = _candidate_parts(row)
    metadata = row.get("metadata") or {}
    print(f"ID: {_candidate_id(row)}")
    print(f"Status: {(decision or {}).get('review_status', 'pending')}")
    print(f"Objetivo: {context.get('objective') or '[ausente — não aprovar]'}")
    print(f"Ação alvo: {target.get('name') or '[ausente]'}")
    print("Argumentos alvo:")
    print(json.dumps(target.get("arguments") or {}, ensure_ascii=False, indent=2))
    available = context.get("available_tool_candidates") or []
    names = [item.get("tool") or item.get("name") for item in available if isinstance(item, dict)]
    print("Ferramentas disponíveis:", ", ".join(str(name) for name in names if name) or "[não registradas]")
    print("Ações anteriores:")
    print(json.dumps(context.get("previous_actions") or [], ensure_ascii=False, indent=2))
    print("Observações anteriores:")
    print(json.dumps(context.get("observations") or [], ensure_ascii=False, indent=2))
    print("Trajetória:", metadata.get("trajectory_status"), "| backend:", metadata.get("trajectory_backend"))
    if decision:
        print("Revisão:", decision.get("reviewer"), "|", decision.get("rationale"))


def _select(candidates: list[dict[str, Any]], candidate_id: str) -> dict[str, Any]:
    matches = [row for row in candidates if _candidate_id(row) == candidate_id]
    if not matches:
        raise ValueError(f"Candidato não encontrado: {candidate_id}")
    return matches[0]


def _trajectory_review(trace_id: str, trace_path: Path = DEFAULT_TRACE_PATH) -> dict[str, Any]:
    """Obtém ações, resultados e resposta final como evidência somente de revisão."""
    global _TRACE_CACHE_KEY, _TRACE_EVENTS_BY_ID
    try:
        stat = trace_path.stat()
    except OSError:
        return {"available": False, "reason": "trace local não encontrado"}
    cache_key = (stat.st_mtime_ns, stat.st_size)
    with _TRACE_CACHE_LOCK:
        if cache_key != _TRACE_CACHE_KEY:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for event in _load_events(trace_path):
                event_id = str(event.get("trace_id") or "")
                if event_id:
                    grouped.setdefault(event_id, []).append(event)
            _TRACE_EVENTS_BY_ID = grouped
            _TRACE_CACHE_KEY = cache_key
        events = _TRACE_EVENTS_BY_ID.get(trace_id, [])

    if not events:
        return {"available": False, "reason": "trace correspondente não encontrado"}
    timeline = []
    for event in events:
        if event.get("event") == "plan_selected":
            timeline.append({
                "event": "action",
                "step": sum(1 for item in timeline if item.get("event") == "action") + 1,
                "tool": event.get("tool"),
                "arguments": event.get("arguments") or {},
                "reason": event.get("reason"),
            })
        elif event.get("event") == "tool_result":
            timeline.append({
                "event": "observation",
                "step": event.get("step"),
                "tool": event.get("tool"),
                "ok": event.get("ok"),
                "data": event.get("data") or {},
                "error": event.get("error"),
                "verification_done": event.get("verification_done", False),
                "artifacts": event.get("artifacts") or [],
            })
    terminal = next((event for event in reversed(events) if event.get("event") == "turn_completed"), {})
    return _clip({
        "available": True,
        "timeline": timeline,
        "final": {
            "status": terminal.get("status"),
            "backend": terminal.get("backend"),
            "steps": terminal.get("steps"),
            "text": terminal.get("text", ""),
        },
    }, 1600)


def workflow_review_snapshot(
    *,
    offset: int = 0,
    limit: int = 1,
    status: str = "pending",
    candidates_path: Path = DEFAULT_CANDIDATES,
    reviews_path: Path = DEFAULT_REVIEWS,
) -> dict[str, Any]:
    """Retorna contagens e uma página curta para a fila de revisão da interface."""
    if status not in {"pending", "approved", "rejected", "all"}:
        raise ValueError("status de revisão inválido")
    if not 0 <= offset <= 100_000 or not 1 <= limit <= 10:
        raise ValueError("página de revisão fora dos limites")
    with _REVIEW_LOCK:
        candidates = _read_jsonl(candidates_path)
        reviews = _latest_reviews(reviews_path)
    counts: Counter[str] = Counter()
    selected: list[tuple[dict[str, Any], str]] = []
    for row in candidates:
        review = reviews.get(_candidate_id(row))
        current_status = str((review or {}).get("review_status") or "pending")
        counts[current_status] += 1
        if status == "all" or current_status == status:
            selected.append((row, current_status))

    page = []
    for row, current_status in selected[offset:offset + limit]:
        context, target = _candidate_parts(row)
        metadata = row.get("metadata") or {}
        page.append(_clip({
            "candidate_id": _candidate_id(row),
            "status": current_status,
            "objective": context.get("objective") or "",
            "target": {"name": target.get("name"), "arguments": target.get("arguments") or {}},
            "previous_actions": context.get("previous_actions") or [],
            "observations": context.get("observations") or [],
            "available_tool_candidates": context.get("available_tool_candidates") or [],
            "trajectory_status": metadata.get("trajectory_status"),
            "trajectory_backend": metadata.get("trajectory_backend"),
            "trajectory_review": _trajectory_review(str(metadata.get("trace_id") or "")),
            "review": reviews.get(_candidate_id(row)),
        }, 1600))
    return {
        "ok": True,
        "counts": {
            "total": len(candidates),
            "pending": counts["pending"],
            "approved": counts["approved"],
            "rejected": counts["rejected"],
        },
        "status": status,
        "offset": offset,
        "limit": limit,
        "items": page,
    }


def record_workflow_review(
    payload: dict[str, Any],
    *,
    candidates_path: Path = DEFAULT_CANDIDATES,
    reviews_path: Path = DEFAULT_REVIEWS,
    approved_path: Path = DEFAULT_APPROVED,
) -> dict[str, Any]:
    """Registra uma decisão humana no ledger e atualiza o snapshot aprovado."""
    if not isinstance(payload, dict):
        raise ValueError("a decisão deve ser um objeto JSON")
    candidate_id = str(payload.get("candidate_id") or "").strip()
    review_status = str(payload.get("review_status") or "").strip()
    reviewer = _redact_text(str(payload.get("reviewer") or "").strip())[:100]
    rationale = _redact_text(str(payload.get("rationale") or "").strip())[:1000]
    if not candidate_id or len(candidate_id) > 200:
        raise ValueError("identificador de candidato inválido")
    if review_status not in {"approved", "rejected"}:
        raise ValueError("decisão deve ser approved ou rejected")
    if not reviewer or len(rationale) < 12:
        raise ValueError("informe revisor e justificativa de pelo menos 12 caracteres")

    with _REVIEW_LOCK:
        row = _select(_read_jsonl(candidates_path), candidate_id)
        context, target = _candidate_parts(row)
        if review_status == "approved" and (not context.get("objective") or not target.get("name")):
            raise ValueError("não aprove um registro sem objetivo ou ação-alvo verificável")
        metadata = row.get("metadata") or {}
        decision = {
            "schema": "workflow-planner-review/v1",
            "candidate_id": candidate_id,
            "trace_id": metadata.get("trace_id"),
            "decision_index": metadata.get("decision_index"),
            "review_status": review_status,
            "reviewer": reviewer,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "rationale": rationale,
        }
        reviews_path.parent.mkdir(parents=True, exist_ok=True)
        with reviews_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(decision, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        approved_count = _materialize_approved(candidates_path, reviews_path, approved_path)
    return {"ok": True, "decision": decision, "approved_count": approved_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="Curadoria humana dos candidatos de workflow")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--reviews", type=Path, default=DEFAULT_REVIEWS)
    parser.add_argument("--approved", type=Path, default=DEFAULT_APPROVED)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="Lista candidatos e estado da revisão")
    listing.add_argument("--status", choices=("pending", "approved", "rejected", "all"), default="pending")
    listing.add_argument("--limit", type=int, default=50)
    showing = commands.add_parser("show", help="Exibe contexto e ação-alvo de um candidato")
    showing.add_argument("candidate_id")
    for name in ("approve", "reject"):
        action = commands.add_parser(name, help="Registra a decisão humana para um candidato")
        action.add_argument("candidate_id")
        action.add_argument("--reviewer", required=True, help="Nome ou identificador de quem revisou")
        action.add_argument("--rationale", required=True, help="Justificativa da decisão")
    args = parser.parse_args()

    try:
        candidates = _read_jsonl(args.candidates)
        reviews = _latest_reviews(args.reviews)
        if args.command == "list":
            rows = []
            all_statuses: Counter[str] = Counter()
            for row in candidates:
                decision = reviews.get(_candidate_id(row))
                status = str((decision or {}).get("review_status") or "pending")
                all_statuses[status] += 1
                if args.status == "all" or status == args.status:
                    context, target = _candidate_parts(row)
                    objective = " ".join(str(context.get("objective") or "").split())
                    rows.append((row, status, objective, target.get("name", "?")))
            print(
                f"Candidatos: {len(candidates)} | pendentes: {all_statuses['pending']} | "
                f"aprovados: {all_statuses['approved']} | rejeitados: {all_statuses['rejected']} | "
                f"exibindo: {min(len(rows), max(args.limit, 0))} | arquivo aprovado: {args.approved}"
            )
            for row, status, objective, tool in rows[:max(args.limit, 0)]:
                print(f"{_candidate_id(row)}\t{status}\t{tool}\t{objective[:180]}")
            return 0

        row = _select(candidates, args.candidate_id)
        if args.command == "show":
            _display_candidate(row, reviews.get(args.candidate_id))
            return 0

        reviewer = args.reviewer.strip()
        rationale = _redact_text(args.rationale.strip())[:1000]
        if not reviewer or len(rationale) < 12:
            raise ValueError("Informe um revisor e uma justificativa de pelo menos 12 caracteres.")
        context, target = _candidate_parts(row)
        if args.command == "approve" and (not context.get("objective") or not target.get("name")):
            raise ValueError("Não aprove um registro sem objetivo ou ação-alvo verificável.")
        decision = {
            "schema": "workflow-planner-review/v1",
            "candidate_id": args.candidate_id,
            "trace_id": (row.get("metadata") or {}).get("trace_id"),
            "decision_index": (row.get("metadata") or {}).get("decision_index"),
            "review_status": "approved" if args.command == "approve" else "rejected",
            "reviewer": reviewer,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "rationale": rationale,
        }
        args.reviews.parent.mkdir(parents=True, exist_ok=True)
        with args.reviews.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(decision, ensure_ascii=False, separators=(",", ":")) + "\n")
        approved_count = _materialize_approved(args.candidates, args.reviews, args.approved)
        print(f"Decisão registrada: {decision['review_status']} | candidatos aprovados no dataset: {approved_count}")
        print("Fonte bruta preservada:", args.candidates)
        print("Ledger de revisão:", args.reviews)
        print("Dataset aprovado:", args.approved)
        return 0
    except (OSError, ValueError) as error:
        parser.error(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
