"""Orquestrador autônomo e limitado do aprendizado local.

Este processo mantém uma fila própria. Ele não precisa receber uma mensagem do
usuário para decidir qual competência estudar, mas também não dispara trabalho
ilimitado: cada ciclo possui orçamento, deduplicação, cooldown e condições de
parada. A execução da pesquisa continua passando pelos contratos do runtime.

Uso:
    .venv/bin/python python/autonomous_learning.py --plan
    .venv/bin/python python/autonomous_learning.py --once
    .venv/bin/python python/autonomous_learning.py --daemon --interval 3600
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_state import AgentState
from competency import learning_contract
from portfolio import find_track, portfolio_manifest


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTROL = ROOT / "corpus" / "agent" / "autonomous_learning.json"
DEFAULT_AUDIT = ROOT / "logs" / "autonomous_learning.jsonl"


@dataclass(frozen=True)
class LearningBudget:
    max_source_pages: int = 6
    max_new_documents: int = 6
    max_practice_tasks: int = 16
    max_tool_steps: int = 24
    max_context_tokens: int = 24000
    max_cycles_per_day: int = 2
    review_after_days: int = 30

    def bounded(self) -> "LearningBudget":
        return LearningBudget(
            max_source_pages=max(1, min(12, int(self.max_source_pages))),
            max_new_documents=max(1, min(12, int(self.max_new_documents))),
            max_practice_tasks=max(1, min(24, int(self.max_practice_tasks))),
            max_tool_steps=max(4, min(32, int(self.max_tool_steps))),
            max_context_tokens=max(4096, min(48000, int(self.max_context_tokens))),
            max_cycles_per_day=max(1, min(4, int(self.max_cycles_per_day))),
            review_after_days=max(7, min(180, int(self.review_after_days))),
        )


class AutonomousLearning:
    def __init__(self, state: AgentState | None = None, control_path: Path | str = DEFAULT_CONTROL,
                 audit_path: Path | str = DEFAULT_AUDIT, budget: LearningBudget | None = None):
        self.state = state or AgentState()
        self.control_path = Path(control_path)
        self.audit_path = Path(audit_path)
        self.budget = (budget or LearningBudget()).bounded()

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _iso(timestamp: float | None = None) -> str:
        return datetime.fromtimestamp(timestamp or time.time(), tz=timezone.utc).isoformat()

    def _load_control(self) -> dict[str, Any]:
        if not self.control_path.exists():
            return {"schema": "autonomous-learning-control/v1", "active": None, "history": []}
        try:
            value = json.loads(self.control_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"schema": "autonomous-learning-control/v1", "active": None, "history": []}
        except (OSError, json.JSONDecodeError):
            return {"schema": "autonomous-learning-control/v1", "active": None, "history": []}

    def _save_control(self, value: dict[str, Any]) -> None:
        self.control_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.control_path.with_suffix(self.control_path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.control_path)

    @contextmanager
    def _cycle_lock(self):
        """Serialize cycle transitions across request threads and processes."""
        self.control_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.control_path.with_suffix(self.control_path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _audit(self, event: dict[str, Any]) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": self._iso(), **event}
        with self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _candidate(self, track: dict[str, Any], skills: dict[str, dict[str, Any]], now: float) -> dict[str, Any]:
        records = [skills.get(topic) for topic in track["topics"] if skills.get(topic)]
        record = min(records, key=lambda item: float((item.get("evaluation") or {}).get("progress") or 0.0), default=None)
        if record is None:
            topic = track["topics"][0]
            progress = 0.0
            status = "not_started"
            gaps = ["pesquisa inicial", "prática", "transferência"]
            updated_at = None
        else:
            topic = str(record.get("topic") or track["topics"][0])
            evaluation = record.get("evaluation") or {}
            progress = float(evaluation.get("progress") or 0.0)
            status = str(record.get("status") or "unknown")
            gaps = list((record.get("concepts") or {}).get("gaps") or [])[:8]
            criteria = evaluation.get("criteria") or {}
            missing_criteria = [name for name, complete in criteria.items() if not complete]
            updated_at = float(record.get("updated_at") or 0.0) or None
        if record is None:
            missing_criteria = ["sources", "practice", "transfer", "integration"]
            criteria = {}
        practice_data = (record or {}).get("practice") or {}
        evidence_data = (record or {}).get("evidence") or {}
        age_days = ((now - updated_at) / 86400) if updated_at else None
        stale = bool(age_days is not None and age_days >= self.budget.review_after_days)
        if status == "mastered" and not stale:
            score = 0.0
            reason = "critérios do laboratório concluídos e evidência recente"
        else:
            deficit = max(0.0, 1.0 - progress)
            score = float(track["priority"]) * 100.0 + deficit * 100.0
            if status == "not_started":
                score += 25.0
            if status in {"studying", "in_progress"}:
                # Um processo reiniciado deve retomar a lacuna interrompida,
                # em vez de abandoná-la por outra trilha virgem.
                score += 60.0
                reason = "retomar ciclo interrompido"
            elif missing_criteria and (practice_data.get("passed") or evidence_data.get("documents")):
                # Prática ou pesquisa já iniciada é trabalho ativo. Sem esse
                # bônus, uma trilha virgem de prioridade ligeiramente maior
                # interromperia Go/Bash antes de fechar os critérios restantes.
                score += 80.0
                recent_activity = (
                    age_days is not None
                    and age_days < 1.0
                    and bool(criteria.get("practice"))
                    and any(item in missing_criteria for item in ("sources", "levels", "coverage"))
                )
                if recent_activity:
                    # A trilha tocada nesta sessão deve ser concluída antes de
                    # o professor abrir outra frente parcialmente conhecida.
                    score += 120.0
                reason = "continuar fechando critérios" + (" (atividade recente)" if recent_activity else "") + ": " + ", ".join(missing_criteria[:4])
            elif stale:
                score += 15.0
                reason = "revisão periódica vencida"
            else:
                reason = "lacunas ainda não fechadas"
        return {
            "track": track["id"], "label": track["label"], "topic": topic,
            "family": track["family"], "uses": track["uses"], "related": track["related"],
            "status": status, "progress": round(progress, 4), "gaps": gaps,
            "missing_criteria": missing_criteria[:8],
            "age_days": round(age_days, 2) if age_days is not None else None,
            "stale": stale, "score": round(score, 4), "reason": reason,
        }

    def candidates(self) -> list[dict[str, Any]]:
        now = self._now()
        skills = self.state.all()
        rows = [self._candidate(track, skills, now) for track in portfolio_manifest()["tracks"]]
        rows.sort(key=lambda item: (-item["score"], item["track"]))
        return rows

    @staticmethod
    def _executor_status(topic: str) -> dict[str, Any]:
        key = str(topic).casefold()
        command = "go" if key in {"go", "golang"} else "bash" if key in {"bash", "shell", "shellbash"} else None
        if command is None:
            return {"required": False, "available": None}
        available = bool(shutil.which(command))
        return {
            "required": True,
            "command": command,
            "available": available,
            "message": None if available else f"executor local ausente: instale ou disponibilize {command} para validar a prática",
        }

    def plan_cycle(self) -> dict[str, Any]:
        rows = self.candidates()
        selected = dict(next((row for row in rows if row["score"] > 0), rows[0]))
        track = find_track(selected["topic"]) or find_track(selected["track"])
        contract = learning_contract(selected["topic"])
        executor = self._executor_status(selected["topic"])
        selected["executor"] = executor
        steps = [
            {"id": "observe", "action": "ler o ledger e as evidências existentes"},
            {"id": "reuse", "action": "reutilizar fundamentos, fontes e práticas compatíveis", "related": (track or {}).get("related", [])},
            {"id": "research", "action": "pesquisar somente as lacunas prioritárias", "query": contract["research"]["query"]},
            {"id": "practice", "action": "executar práticas isoladas ou criar revisão rubricada"},
            {"id": "transfer", "action": "validar uma tarefa inédita"},
            {"id": "integrate", "action": "verificar entrega completa e atualizar o ledger"},
        ]
        if executor.get("required") and not executor.get("available"):
            steps.insert(2, {"id": "recover", "action": "identificar e resolver a ausência do executor local antes de promover a competência", "blocker": executor["message"]})
            selected["blocker"] = executor["message"]
        return {
            "schema": "autonomous-learning-cycle/v1",
            "cycle_id": f"cycle-{uuid.uuid4().hex[:12]}",
            "created_at": self._iso(),
            "mode": "bounded-autonomous",
            "selected": selected,
            "contract": contract,
            "steps": steps,
            "budget": asdict(self.budget),
            "deduplication": {
                "reuse_existing_sources": True,
                "reuse_verified_practice": True,
                "skip_mastered_until_review": True,
                "never_promote_from_retrieval_only": True,
            },
            "stop_conditions": [
                "orçamento de fontes, passos ou contexto esgotado",
                "nenhuma fonte topicalmente relevante validada",
                "executor ou revisão não consegue produzir evidência verificável",
                "falha repetida agendada para recuperação posterior",
            ],
            "candidates": rows[:8],
        }

    def _recent_audit(self, limit: int = 20) -> list[dict[str, Any]]:
        """Lê somente a cauda do log operacional, sem carregar o histórico inteiro."""
        if limit < 1 or not self.audit_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            for line in self.audit_path.read_text(encoding="utf-8").splitlines()[-max(1, min(limit * 3, 200)):]:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
        except OSError:
            return []
        return rows[-limit:]

    @staticmethod
    def _compact_outcome(value: Any) -> dict[str, Any]:
        """Mantém estado operacional útil sem reenviar o job inteiro ao navegador."""
        if not isinstance(value, dict):
            return {}
        compact: dict[str, Any] = {}
        for key in ("id", "status", "topic", "progress", "documents_added", "error"):
            if key in value and value[key] is not None:
                compact[key] = value[key]
        laboratory = value.get("laboratory")
        if isinstance(laboratory, dict) and laboratory.get("status"):
            compact["laboratory_status"] = laboratory["status"]
            if laboratory["status"] == "practice_unavailable":
                details = laboratory.get("laboratories") or {}
                messages = [
                    str(item.get("message"))
                    for item in details.values()
                    if isinstance(item, dict) and item.get("status") == "practice_unavailable" and item.get("message")
                ]
                compact["practice_blocker"] = "; ".join(messages[:3]) or "executor local seguro para prática indisponível"
        skill = value.get("skill")
        if isinstance(skill, dict) and skill.get("status"):
            compact["skill_status"] = skill["status"]
        evaluation = skill.get("evaluation") if isinstance(skill, dict) else None
        if isinstance(evaluation, dict):
            compact["skill_progress"] = evaluation.get("progress", 0)
            compact["ready"] = bool(evaluation.get("ready"))
        logs = value.get("logs")
        if isinstance(logs, list):
            compact["logs"] = logs[-8:]
        return compact

    @classmethod
    def _compact_history(cls, item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        return {
            "cycle_id": item.get("cycle_id"),
            "finished_at": item.get("finished_at"),
            "outcome": cls._compact_outcome(item.get("outcome")),
        }

    @classmethod
    def _compact_audit(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compacted = []
        for item in rows:
            row = {key: item[key] for key in ("at", "event", "cycle_id", "topic", "source", "status", "total", "passed", "failed", "recovered") if key in item}
            if "budget" in item:
                row["budget"] = item["budget"]
            if "outcome" in item:
                row["outcome"] = cls._compact_outcome(item["outcome"])
            compacted.append(row)
        return compacted

    def status(self, history_limit: int = 8, audit_limit: int = 20) -> dict[str, Any]:
        """Expõe o próximo passo e a trilha operacional de forma somente leitura."""
        control = self._load_control()
        history = list(control.get("history") or [])
        return {
            "schema": "autonomous-learning-status/v1",
            "next": self.plan_cycle(),
            "control": {
                "active": control.get("active"),
                "history": [self._compact_history(item) for item in history[-max(1, min(history_limit, 32)):]],
            },
            "audit": self._compact_audit(self._recent_audit(audit_limit)),
        }

    def audit_laboratory(self, topic: str, result: dict[str, Any], source: str = "local-validation") -> None:
        """Registra uma prática local sem confundi-la com um ciclo de pesquisa."""
        tasks = result.get("tasks") or []
        self._audit({
            "event": "laboratory_verified" if result.get("status") == "verified" else "laboratory_failed",
            "topic": topic,
            "source": source,
            "status": result.get("status"),
            "total": int(result.get("total") or len(tasks)),
            "passed": int(result.get("passed") or sum(1 for item in tasks if item.get("passed"))),
            "failed": [item.get("name") for item in tasks if not item.get("passed")],
            "recovered": [item.get("name") for item in tasks if item.get("recovery_attempt")],
        })

    def tick(self) -> dict[str, Any]:
        """Observa ou inicia um ciclo, serializando mudanças no controle."""
        with self._cycle_lock():
            return self._tick_locked()

    def _tick_locked(self) -> dict[str, Any]:
        """Executa uma transição com o lock exclusivo do arquivo de controle."""
        control = self._load_control()
        active = control.get("active")
        if active:
            from learning import snapshot
            job_id = str(active.get("job_id") or "")
            job = snapshot(job_id) if job_id else None
            if job and job.get("status") == "running":
                return {"status": "running", "active": active, "job": job}
            if job:
                outcome = job
                event = "cycle_finished"
                result_status = "finished"
            else:
                outcome = {
                    "status": "interrupted",
                    "job_id": active.get("job_id"),
                    "topic": active.get("topic"),
                    "error": "O executor não encontrou o job ativo após reinício ou interrupção.",
                }
                event = "cycle_interrupted"
                result_status = "interrupted"
            control.setdefault("history", []).append({
                "cycle_id": active.get("cycle_id"),
                "outcome": outcome,
                "finished_at": self._iso(),
            })
            control["history"] = control["history"][-32:]
            control["active"] = None
            self._save_control(control)
            self._audit({"event": event, "cycle_id": active.get("cycle_id"), "outcome": outcome})
            return {"status": result_status, "outcome": outcome}
        today = datetime.now(timezone.utc).date()
        finished_today = 0
        for item in control.get("history", []):
            stamp = str(item.get("finished_at") or "")
            try:
                outcome = item.get("outcome") or {}
                if outcome.get("status") not in {"completed", "failed", "partial", "recovered", "interrupted"}:
                    continue
                if datetime.fromisoformat(stamp.replace("Z", "+00:00")).date() == today:
                    finished_today += 1
            except ValueError:
                continue
        if finished_today >= self.budget.max_cycles_per_day:
            return {
                "status": "cooldown",
                "reason": "limite diário de ciclos atingido",
                "finished_today": finished_today,
                "max_cycles_per_day": self.budget.max_cycles_per_day,
            }
        plan = self.plan_cycle()
        from learning import start
        active = {
            "cycle_id": plan["cycle_id"], "job_id": None,
            "topic": plan["selected"]["topic"], "started_at": self._iso(),
            "status": "starting",
        }
        control["active"] = active
        self._save_control(control)
        self._audit({"event": "cycle_starting", "cycle_id": plan["cycle_id"], "topic": active["topic"], "budget": plan["budget"]})
        try:
            job = start(plan["selected"]["topic"], budget=plan["budget"])
        except Exception as error:
            outcome = {"status": "failed", "topic": active["topic"], "error": str(error)}
            control.setdefault("history", []).append({
                "cycle_id": active["cycle_id"], "outcome": outcome,
                "finished_at": self._iso(),
            })
            control["history"] = control["history"][-32:]
            control["active"] = None
            self._save_control(control)
            self._audit({"event": "cycle_failed", "cycle_id": active["cycle_id"], "outcome": outcome})
            return {"status": "failed", "outcome": outcome}
        active.update({"job_id": job["id"], "status": job.get("status", "running")})
        control["active"] = active
        self._save_control(control)
        self._audit({"event": "cycle_started", "cycle_id": plan["cycle_id"], "topic": active["topic"], "budget": plan["budget"]})
        return {"status": "started", "plan": plan, "job": job}


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Professor autônomo da IA Local")
    parser.add_argument("--plan", action="store_true", help="exibe a próxima trilha sem iniciar pesquisa")
    parser.add_argument("--portfolio", action="store_true", help="exibe o portfólio e fundamentos compartilhados")
    parser.add_argument("--once", action="store_true", help="observa ou inicia um ciclo limitado")
    parser.add_argument("--daemon", action="store_true", help="mantém o professor executando ciclos limitados")
    parser.add_argument("--interval", type=int, default=3600, help="segundos entre observações do daemon")
    args = parser.parse_args()
    orchestrator = AutonomousLearning()
    if args.portfolio:
        _print(portfolio_manifest())
        return 0
    if args.plan or not args.once and not args.daemon:
        _print(orchestrator.plan_cycle())
        return 0
    if args.once:
        result = orchestrator.tick()
        # ``learning.start`` usa uma thread de trabalho. Um processo de
        # rodada única precisa aguardar essa thread; caso contrário, o
        # interpretador encerraria antes da pesquisa e da prática.
        if result.get("status") == "started":
            deadline = time.time() + 1800
            while time.time() < deadline:
                time.sleep(0.5)
                observed = orchestrator.tick()
                if observed.get("status") != "running":
                    result = observed
                    break
            else:
                result = {"status": "running", "message": "ciclo mantido no controle; excedeu a espera desta rodada"}
        _print(result)
        return 0
    interval = max(30, min(86400, args.interval))
    while True:
        _print(orchestrator.tick())
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
