"""Pipeline controlado de aprendizado do agente local.

O pipeline transforma experiências verificadas em um candidato de planejador,
mede o candidato contra um conjunto de validação e executa as baterias de
regressão do agente. A versão ativa nunca é alterada sem ``--promote`` e sem
todos os gates aprovados.

O aprendizado aqui é deliberadamente especializado: o artefato produzido é
o índice leve do planejador. Ele não substitui nem afirma ter treinado o
modelo conversacional.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_traces import _load_events
from agent_planner import learned_feature_score
from dialogue import normalize
from train_planner import _rows, train


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRACE = ROOT / "logs" / "agent_traces.jsonl"
DEFAULT_CORPUS = ROOT / "corpus" / "training"
DEFAULT_ACTIVE_INDEX = ROOT / "model" / "planner" / "planner_index.json"
DEFAULT_RUNS = ROOT / "model" / "planner" / "runs"
DEFAULT_REPORT = ROOT / "model" / "planner" / "last_run.json"

SUCCESS_STATUSES = {"completed", "success", "succeeded"}

BATTERY_FILES = (
    "tests/test_agent_traces.py",
    "tests/test_agent_contract.py",
    "tests/test_agent_execution_gates.py",
    "tests/test_agent_runs.py",
    "tests/test_agent_behavior_gates.py",
    "tests/run_agent_benchmark.py",
    "tests/benchmark_model_suite.py",
    "tests/benchmark_workflow_suite.py",
    "tests/benchmark_agent_harness.py",
    "tests/benchmark_agentic_curriculum.py",
)


@dataclass(frozen=True)
class TrainingExample:
    question: str
    tool: str
    source: str
    group_id: str | None = None


def _json_dump(path: Path, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        stream = temporary.open("w", encoding="utf-8")
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
        stream.close()
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return count


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scrub_text(value: str) -> str:
    """Remove padrões óbvios de credenciais antes de criar dados de treino."""
    text = str(value or "")[:4000]
    return re.sub(
        r"(?i)(api[_-]?key|token|password|passwd|secret|authorization)\s*[:=]\s*[^\s,;]+",
        r"\1=[omitido]",
        text,
    )


def _canonical_tool_for_training(question: str, tool: str) -> str:
    """Corrige rótulos históricos quando a primeira ação é inequívoca.

    Alguns traces antigos registraram diagnóstico ou testes como primeira
    ferramenta em pedidos que claramente começam pela criação de uma página.
    O runtime atual mantém a sequência create -> verify; esses traces não
    devem ensinar o índice a inverter essa ordem.
    """
    text = normalize(question)
    page_request = bool(re.search(
        r'\b(?:pagina|página|tela|interface|vitrine|landing page|site|dashboard|formulario|formulário)\b',
        text,
    )) and bool(re.search(
        r'\b(?:crie|cria|criar|construa|construir|desenvolva|desenvolver|gere|gerar|transforme|transformar|melhore|melhorar)\b',
        text,
    ))
    explicit_diagnosis = bool(re.search(r'\b(?:diagnostique|diagnosticar|debug|depure|depurar|por que falha|falha nos testes)\b', text))
    if page_request and not explicit_diagnosis and tool in {'diagnose_project', 'project_checks', 'research_web', 'knowledge_base'}:
        return 'create_web_page'
    local_file_request = bool(re.search(r'\b(?:script|arquivo)\s+[\w./-]+\.(?:sh|py|ts|js|rs|json|md)\b', text))
    local_action = bool(re.search(r'\b(?:melhore|melhorar|corrija|corrigir|edite|editar|analise|analisar|revise|revisar|conserte|consertar)\b', text))
    if local_file_request and local_action and tool in {'research_web', 'knowledge_base'}:
        return 'read_file'
    return tool


def _load_rows(path: Path) -> tuple[list[TrainingExample], int]:
    """Lê exemplos aceitos pelo treinador e conta linhas inválidas."""
    if not path.exists():
        return [], 0
    examples: list[TrainingExample] = []
    invalid = 0
    try:
        parsed = list(_rows(path) or ())
    except (OSError, UnicodeError):
        return [], 1
    for question, tool, source in parsed:
        question = _scrub_text(str(question).strip())
        tool = str(tool).strip()
        if not question or not tool:
            invalid += 1
            continue
        source = str(source or path.name)
        group_id = source if source.startswith(("workflow-trace:", "verified-trace:", "verified-trace-canonicalized:")) else None
        examples.append(TrainingExample(question, tool, source, group_id))
    return examples, invalid


def _trace_groups(events: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    groups: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for event in events:
        trace_id = str(event.get("trace_id") or "").strip()
        if not trace_id:
            continue
        bucket = groups.setdefault(trace_id, {"turn_started": [], "plan_selected": [], "tool_result": [], "turn_completed": []})
        name = str(event.get("event") or "")
        if name in bucket:
            bucket[name].append(event)
    return groups


def verified_trace_examples(path: Path) -> tuple[list[TrainingExample], dict[str, int]]:
    """Seleciona traces completos, positivos e explicitamente aprovados por pessoa.

    Uma conversa interrompida, bloqueada ou com ferramenta falha não vira
    supervisão positiva por acidente. Sucesso técnico sozinho também não é
    aprovação para treino. Traces sem revisão ficam pendentes; os candidatos
    revisados podem ser ingeridos como linhas do corpus de workflow.
    """
    events = _load_events(path)
    groups = _trace_groups(events)
    examples: list[TrainingExample] = []
    rejected = {
        "traces": len(groups),
        "complete": 0,
        "accepted": 0,
        "no_tool_turns": 0,
        "missing_start": 0,
        "missing_plan": 0,
        "missing_result": 0,
        "failed_result": 0,
        "unreviewed": 0,
        "unfinished": 0,
    }
    for trace_id, group in groups.items():
        if not group["turn_started"]:
            rejected["missing_start"] += 1
            continue
        if not group["plan_selected"]:
            if group["tool_result"]:
                rejected["missing_plan"] += 1
            elif group["turn_completed"]:
                # Conversation, retrieval, or an explicit abstention can
                # correctly finish without invoking an action tool.
                rejected["no_tool_turns"] += 1
            else:
                rejected["unfinished"] += 1
            continue
        if not group["turn_completed"]:
            rejected["unfinished"] += 1
            continue
        completion = group["turn_completed"][-1]
        status = str(completion.get("status") or "").lower()
        if status and status not in SUCCESS_STATUSES:
            rejected["unfinished"] += 1
            continue
        if not group["tool_result"]:
            rejected["missing_result"] += 1
            continue
        if any(result.get("ok") is not True for result in group["tool_result"]):
            rejected["failed_result"] += 1
            continue
        rejected["complete"] += 1
        if str(completion.get("review_status") or "").lower() != "approved":
            rejected["unreviewed"] += 1
            continue
        start = group["turn_started"][0]
        plan = group["plan_selected"][-1]
        question = _scrub_text(str(start.get("question") or "").strip())
        raw_tool = str(plan.get("tool") or "").strip()
        tool = _canonical_tool_for_training(question, raw_tool)
        if not question or not tool:
            rejected["missing_plan"] += 1
            continue
        source = f"verified-trace:{trace_id}"
        if tool != raw_tool:
            source = f"verified-trace-canonicalized:{trace_id}"
        examples.append(TrainingExample(question, tool, source, source))
        rejected["accepted"] += 1
    return examples, rejected


def _deduplicate(examples: Iterable[TrainingExample]) -> list[TrainingExample]:
    seen: set[tuple[str, str]] = set()
    result: list[TrainingExample] = []
    for example in examples:
        key = (normalize(example.question), example.tool)
        if not key[0] or key in seen:
            continue
        seen.add(key)
        result.append(example)
    return result


def _example_group(example: TrainingExample) -> str:
    return example.group_id or f"prompt:{normalize(example.question)}"


def _split(examples: list[TrainingExample], validation_ratio: float) -> tuple[list[TrainingExample], list[TrainingExample]]:
    if not examples:
        return [], []
    ratio = min(0.5, max(0.0, validation_ratio))
    if ratio == 0:
        return list(examples), []
    grouped: dict[str, list[TrainingExample]] = {}
    for example in examples:
        # Keep every decision from one execution together. For curated rows
        # without a trace id, identical normalized prompts are one scenario.
        group_id = _example_group(example)
        grouped.setdefault(group_id, []).append(example)
    if len(grouped) < 2:
        # A single trajectory cannot support an honest held-out evaluation.
        return list(examples), []
    ordered = sorted(
        grouped.items(),
        key=lambda item: hashlib.sha256(item[0].encode("utf-8")).digest(),
    )
    validation_count = min(len(ordered) - 1, max(1, int(round(len(ordered) * ratio))))
    validation_groups = {key for key, _ in ordered[:validation_count]}
    # A ferramenta precisa aparecer no treino para que a validação meça
    # generalização de pedidos, não uma classe que o planejador nunca viu.
    tools = {example.tool for example in examples}
    for tool in tools:
        matching = [key for key, group in ordered if any(item.tool == tool for item in group)]
        if matching and all(key in validation_groups for key in matching):
            validation_groups.remove(matching[0])
    if not validation_groups:
        for key, group in ordered:
            if all(sum(any(item.tool == tool for item in grouped[candidate])
                       for candidate in grouped if candidate != key) >= 1
                   for tool in {item.tool for item in group}):
                validation_groups.add(key)
                break
    validation = [example for key, group in grouped.items() if key in validation_groups for example in group]
    training = [example for key, group in grouped.items() if key not in validation_groups for example in group]
    return training, validation


def _as_jsonl(example: TrainingExample) -> dict[str, Any]:
    return {
        "instruction": example.question,
        "output": example.tool,
        "tool": example.tool,
        "source": example.source,
        "group_id": example.group_id,
    }


def _parse_examples(paths: Iterable[Path]) -> tuple[list[TrainingExample], list[dict[str, Any]]]:
    all_examples: list[TrainingExample] = []
    manifest: list[dict[str, Any]] = []
    for path in paths:
        examples, invalid = _load_rows(path)
        all_examples.extend(examples)
        manifest.append({
            "path": str(path),
            "exists": path.exists(),
            "sha256": _sha256(path),
            "examples": len(examples),
            "invalid": invalid,
        })
    return _deduplicate(all_examples), manifest


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _quality_summary(
    corpus_examples: list[TrainingExample],
    trace_examples: list[TrainingExample],
    trace_stats: dict[str, int],
    dataset_manifest: list[dict[str, Any]],
) -> dict[str, Any]:
    """Mede a saúde do material antes de permitir que ele influencie o agente."""
    raw_examples = len(corpus_examples) + len(trace_examples)
    unique_examples = len(_deduplicate([*corpus_examples, *trace_examples]))
    total_traces = int(trace_stats.get("traces") or 0)
    tool_related_traces = max(0, total_traces - int(trace_stats.get("no_tool_turns") or 0))
    complete_tool_traces = int(trace_stats.get("complete") or 0)
    accepted = int(trace_stats.get("accepted") or 0)
    warnings: list[str] = []
    recommendations: list[str] = []

    pending_review = int(trace_stats.get("unreviewed") or 0)
    if pending_review:
        recommendations.append(f"revisar manualmente {pending_review} trace(s) pendente(s); nenhum deles entrou no treino")
    if int(trace_stats.get("missing_plan") or 0):
        recommendations.append("corrigir a instrumentação do planejador antes de coletar mais dados")
    if int(trace_stats.get("unfinished") or 0):
        recommendations.append("classificar bloqueios e registrar uma conclusão explícita, inclusive em falhas")
    if unique_examples < 100:
        warnings.append("a diversidade supervisionada ainda é pequena")
        recommendations.append("coletar tarefas inéditas por ferramenta e por domínio antes de promover")
    invalid_rows = sum(int(item.get("invalid") or 0) for item in dataset_manifest)
    if invalid_rows:
        warnings.append(f"{invalid_rows} linhas inválidas foram descartadas dos datasets")
        recommendations.append("corrigir os datasets inválidos e preservar a procedência das correções")
    if not dataset_manifest:
        warnings.append("nenhum dataset de treinamento foi encontrado")
        recommendations.append("adicionar corpus curado ou executar o pipeline somente com traces verificados")

    return {
        "status": "healthy" if not warnings else "needs-attention",
        "raw_examples": raw_examples,
        "unique_examples": unique_examples,
        "duplicates_removed": max(0, raw_examples - unique_examples),
        "corpus_examples": len(corpus_examples),
        "verified_trace_examples": len(trace_examples),
        "unreviewed_traces": pending_review,
        "trace_count": total_traces,
        "non_tool_turns": int(trace_stats.get("no_tool_turns") or 0),
        "tool_related_traces": tool_related_traces,
        "trace_diagnostics": {
            "missing_plan": int(trace_stats.get("missing_plan") or 0),
            "missing_result": int(trace_stats.get("missing_result") or 0),
            "failed_result": int(trace_stats.get("failed_result") or 0),
            "unfinished": int(trace_stats.get("unfinished") or 0),
        },
        "accepted_trace_rate": _ratio(accepted, complete_tool_traces),
        "completion_rate": _ratio(complete_tool_traces, tool_related_traces),
        "invalid_dataset_rows": invalid_rows,
        "warnings": warnings,
        "recommendations": recommendations,
        "promotion_policy": "blocking; quality needs-attention impede elegibilidade",
    }


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path if path.is_dir() else path.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _write_target(path: Path) -> dict[str, Any]:
    parent = _nearest_existing_parent(path)
    return {
        "path": str(path),
        "parent": str(parent),
        "parent_exists": parent.exists(),
        "writable": bool(parent.exists() and os.access(parent, os.W_OK)),
    }


def _default_dataset_paths(root: Path, corpus_dir: Path) -> list[Path]:
    """Load curated rows, raw candidates (filtered), and reviewed examples."""
    datasets = sorted(corpus_dir.glob("*.jsonl")) if corpus_dir.exists() else []
    for path in (
        root / "corpus" / "raw" / "workflow_planner_candidates.jsonl",
        root / "corpus" / "review" / "workflow_planner_approved.jsonl",
    ):
        if path.is_file() and path not in datasets:
            datasets.append(path)
    return datasets


def preflight(
    *,
    root: Path = ROOT,
    trace_path: Path = DEFAULT_TRACE,
    corpus_dir: Path = DEFAULT_CORPUS,
    active_index: Path = DEFAULT_ACTIVE_INDEX,
    runs_dir: Path = DEFAULT_RUNS,
    report_path: Path | None = DEFAULT_REPORT,
    python: Path | None = None,
    require_batteries: bool = True,
) -> dict[str, Any]:
    """Verifica dependências e entradas sem criar artefatos ou executar testes."""
    python = python or Path(sys.executable)
    datasets = _default_dataset_paths(root, corpus_dir)
    missing_batteries = [item for item in BATTERY_FILES if not (root / item).is_file()]
    errors: list[str] = []
    warnings: list[str] = []

    checks: dict[str, Any] = {
        "root": {"path": str(root), "exists": root.is_dir()},
        "python": {"path": str(python), "exists": python.is_file(), "executable": os.access(python, os.X_OK)},
        "pipeline": {"path": str(Path(__file__).resolve()), "exists": Path(__file__).is_file()},
        "trace": {"path": str(trace_path), "exists": trace_path.is_file()},
        "corpus": {"path": str(corpus_dir), "exists": corpus_dir.is_dir(), "datasets": len(datasets)},
        "active_index": {"path": str(active_index), "exists": active_index.is_file()},
        "write_targets": {
            "runs": _write_target(runs_dir),
            "report": _write_target(report_path) if report_path else None,
        },
        "batteries": {
            "required": require_batteries,
            "missing": missing_batteries,
            "available": not missing_batteries,
        },
    }

    if not checks["root"]["exists"]:
        errors.append("workspace raiz não existe")
    if not checks["python"]["exists"] or not checks["python"]["executable"]:
        errors.append("interpretador Python executável não encontrado")
    if not checks["pipeline"]["exists"]:
        errors.append("pipeline principal não encontrado")
    if not checks["corpus"]["exists"]:
        warnings.append("diretório de corpus não existe; a rodada ficará sem dados de corpus")
    elif not datasets:
        warnings.append("nenhum dataset JSONL encontrado no corpus")
    if not checks["trace"]["exists"]:
        warnings.append("trace de execução ainda não existe")
    if not checks["active_index"]["exists"]:
        warnings.append("índice ativo ainda não existe; a primeira rodada ficará sem baseline")
    if require_batteries and missing_batteries:
        errors.append(f"{len(missing_batteries)} arquivos de bateria estão ausentes")
    elif missing_batteries:
        warnings.append(f"{len(missing_batteries)} arquivos de bateria estão ausentes, mas as baterias foram dispensadas")
    for target in checks["write_targets"].values():
        if target and not target["writable"]:
            errors.append(f"destino sem permissão de escrita: {target['path']}")

    status = "blocked" if errors else "ready-with-warnings" if warnings else "ready"
    return {
        "schema": "agent-learning-preflight/v1",
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "next": "run" if status != "blocked" else "corrigir os erros e repetir o pré-voo",
    }


def _candidate_scores(artifact: dict[str, Any], question: str) -> dict[str, float]:
    return {
        str(tool): learned_feature_score(question, str(tool), artifact)
        for tool in (artifact.get("tools") or {})
    }


def evaluate_index(artifact: dict[str, Any], examples: Iterable[TrainingExample]) -> dict[str, Any]:
    rows = []
    for example in examples:
        scores = _candidate_scores(artifact, example.question)
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        predicted = ranked[0][0] if ranked and ranked[0][1] > 0 else None
        rows.append({
            "question": example.question,
            "expected_tool": example.tool,
            "predicted_tool": predicted,
            "correct": predicted == example.tool,
            "score": scores.get(example.tool, 0.0),
        })
    correct = sum(1 for row in rows if row["correct"])
    return {
        "scope": "learned-feature-reranker-only",
        "examples": len(rows),
        "correct": correct,
        "accuracy": round(correct / len(rows), 4) if rows else None,
        "cases": rows,
    }


def _load_artifact(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _valid_artifact(path: Path) -> tuple[bool, str]:
    artifact = _load_artifact(path)
    if artifact.get("schema") != "planner-index/v1":
        return False, "schema ausente ou incompatível"
    if artifact.get("method") != "supervised-feature-reranker":
        return False, "método ausente ou incompatível"
    if not isinstance(artifact.get("tools"), dict):
        return False, "catálogo de ferramentas ausente"
    if not isinstance(artifact.get("examples"), list):
        return False, "exemplos ausentes"
    return True, "ok"


def _run_command(command: list[str], cwd: Path, output_dir: Path, name: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
        status = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except OSError as exc:
        status = 127
        stdout = ""
        stderr = str(exc)
    (output_dir / f"{name}.stdout.log").write_text(stdout, encoding="utf-8")
    (output_dir / f"{name}.stderr.log").write_text(stderr, encoding="utf-8")
    return {
        "name": name,
        "command": command,
        "status": status,
        "passed": status == 0,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-2000:],
    }


def run_batteries(root: Path, python: Path, output_dir: Path, skip: bool = False) -> list[dict[str, Any]]:
    if skip:
        return [{"name": "batteries", "status": None, "passed": None, "skipped": True}]
    commands = [
        ("unit-agent-core", [str(python), "-m", "unittest", "tests.test_agent_traces", "tests.test_agent_contract", "tests.test_agent_execution_gates", "tests.test_agent_runs", "tests.test_agent_behavior_gates"]),
        ("agent-routing", [str(python), "tests/run_agent_benchmark.py"]),
        ("model-suite", [str(python), "tests/benchmark_model_suite.py", "--report", str(output_dir / "model_eval_suite.json")]),
        ("workflow-gate", [str(python), "tests/benchmark_workflow_suite.py", "--report", str(output_dir / "workflow_gate.json")]),
        ("agent-harness", [str(python), "tests/benchmark_agent_harness.py"]),
        ("agentic-heldout", [str(python), "tests/benchmark_agentic_curriculum.py"]),
    ]
    return [_run_command(command, root, output_dir, name) for name, command in commands]


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _promote(candidate: Path, active: Path, run_dir: Path) -> dict[str, Any]:
    active.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if active.exists():
        backup = run_dir / "previous_planner_index.json"
        shutil.copy2(active, backup)
    temporary = active.with_name(f".{active.name}.{os.getpid()}.tmp")
    shutil.copy2(candidate, temporary)
    os.replace(temporary, active)
    return {
        "promoted": True,
        "active_index": str(active),
        "active_sha256": _sha256(active),
        "backup": str(backup) if backup else None,
    }


def run_pipeline(
    *,
    root: Path = ROOT,
    trace_path: Path = DEFAULT_TRACE,
    corpus_dir: Path = DEFAULT_CORPUS,
    active_index: Path = DEFAULT_ACTIVE_INDEX,
    runs_dir: Path = DEFAULT_RUNS,
    report_path: Path | None = None,
    python: Path | None = None,
    datasets: list[Path] | None = None,
    validation_ratio: float = 0.2,
    skip_batteries: bool = False,
    promote: bool = False,
) -> dict[str, Any]:
    python = python or Path(sys.executable)
    readiness = preflight(
        root=root,
        trace_path=trace_path,
        corpus_dir=corpus_dir,
        active_index=active_index,
        runs_dir=runs_dir,
        report_path=report_path,
        python=python,
        require_batteries=not skip_batteries,
    )
    if readiness["status"] == "blocked":
        blocked = {
            "schema": "agent-learning-run/v1",
            "status": "blocked-preflight",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "preflight": readiness,
            "promotion": {"requested": promote, "promoted": False, "eligible": False},
        }
        report_target = readiness["checks"]["write_targets"].get("report")
        if report_path and report_target and report_target["writable"]:
            _json_dump(report_path, blocked)
        return blocked
    run_id = _new_run_id()
    run_dir = runs_dir / run_id
    while run_dir.exists():
        run_id = f"{_new_run_id()}-{os.getpid()}"
        run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    if datasets is None:
        datasets = _default_dataset_paths(root, corpus_dir)
    trace_examples, trace_stats = verified_trace_examples(trace_path)
    trace_export = run_dir / "verified_traces.jsonl"
    _write_jsonl(trace_export, [_as_jsonl(example) for example in trace_examples])

    corpus_examples, dataset_manifest = _parse_examples(datasets)
    all_examples = _deduplicate([*corpus_examples, *trace_examples])
    training, validation = _split(all_examples, validation_ratio)
    quality = _quality_summary(corpus_examples, trace_examples, trace_stats, dataset_manifest)
    _write_jsonl(run_dir / "training.jsonl", [_as_jsonl(example) for example in training])
    _write_jsonl(run_dir / "validation.jsonl", [_as_jsonl(example) for example in validation])

    report: dict[str, Any] = {
        "schema": "agent-learning-run/v1",
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "trace": {"path": str(trace_path), **trace_stats, "export": str(trace_export)},
        "datasets": dataset_manifest,
        "data": {
            "unique_examples": len(all_examples),
            "training_examples": len(training),
            "validation_examples": len(validation),
            "training_groups": len({_example_group(example) for example in training}),
            "validation_groups": len({_example_group(example) for example in validation}),
            "validation_unit": "whole-trajectory-or-identical-prompt",
            "validation_ratio": validation_ratio,
        },
        "quality": quality,
        "preflight": readiness,
        "active_index_before": {"path": str(active_index), "sha256": _sha256(active_index)},
        "promotion": {"requested": promote, "promoted": False, "eligible": False},
    }
    _json_dump(run_dir / "manifest.json", report)

    if not training:
        report["status"] = "blocked-no-training-data"
        report["gates"] = {"training_data": False}
        _json_dump(run_dir / "report.json", report)
        if report_path:
            _json_dump(report_path, report)
        return report

    candidate = run_dir / "planner_index.json"
    train_stats = train([run_dir / "training.jsonl"], candidate)
    candidate_artifact = _load_artifact(candidate)
    valid, validation_reason = _valid_artifact(candidate)
    baseline_artifact = _load_artifact(active_index)
    report["training"] = train_stats
    report["candidate"] = {"path": str(candidate), "sha256": _sha256(candidate), "valid": valid, "validation": validation_reason}
    report["evaluation"] = {
        "candidate": evaluate_index(candidate_artifact, validation),
        "baseline": evaluate_index(baseline_artifact, validation) if baseline_artifact else {"examples": len(validation), "accuracy": None, "cases": []},
    }

    batteries_dir = run_dir / "batteries"
    batteries_dir.mkdir(parents=True, exist_ok=True)
    batteries = run_batteries(root, python, batteries_dir, skip=skip_batteries)
    report["batteries"] = batteries
    battery_runs = [item for item in batteries if not item.get("skipped")]
    battery_passed = bool(battery_runs) and all(item.get("passed") is True for item in battery_runs)
    candidate_accuracy = report["evaluation"]["candidate"].get("accuracy")
    baseline_accuracy = report["evaluation"]["baseline"].get("accuracy")
    validation_gate = bool(validation) and candidate_accuracy is not None and (baseline_accuracy is None or candidate_accuracy >= baseline_accuracy)
    gates = {
        "training_data": bool(training),
        "training_quality": quality.get("status") == "healthy",
        "candidate_integrity": valid,
        "validation_nonempty": bool(validation),
        "validation_no_regression": validation_gate,
        "batteries": battery_passed,
    }
    eligible = all(gates.values())
    report["gates"] = gates
    report["promotion"]["eligible"] = eligible
    report["status"] = "candidate-ready" if eligible else "candidate-blocked"

    if promote and eligible:
        report["promotion"].update(_promote(candidate, active_index, run_dir))
        report["active_index_after"] = {"path": str(active_index), "sha256": _sha256(active_index)}
        report["status"] = "promoted"
    elif promote:
        report["promotion"]["reason"] = "um ou mais gates falharam; o índice ativo não foi alterado"

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    _json_dump(run_dir / "report.json", report)
    if report_path:
        _json_dump(report_path, report)
    return report


def report_only(trace_path: Path, corpus_dir: Path, report_path: Path | None = None) -> dict[str, Any]:
    examples, trace_stats = verified_trace_examples(trace_path)
    datasets = _default_dataset_paths(ROOT, corpus_dir)
    corpus_examples, manifest = _parse_examples(datasets)
    quality = _quality_summary(corpus_examples, examples, trace_stats, manifest)
    result = {
        "schema": "agent-learning-report/v1",
        "trace": {"path": str(trace_path), **trace_stats},
        "datasets": manifest,
        "corpus_examples": len(corpus_examples),
        "verified_trace_examples": len(examples),
        "unique_examples": len(_deduplicate([*corpus_examples, *examples])),
        "quality": quality,
    }
    if report_path:
        _json_dump(report_path, result)
    return result


def _path_arg(value: str) -> Path:
    return Path(value).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pipeline seguro de aprendizado do agente local")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report = subparsers.add_parser("report", help="audita traces e datasets sem treinar")
    report.add_argument("--trace", type=_path_arg, default=DEFAULT_TRACE)
    report.add_argument("--corpus-dir", type=_path_arg, default=DEFAULT_CORPUS)
    report.add_argument("--report", type=_path_arg)

    check = subparsers.add_parser("preflight", help="verifica ambiente e entradas sem escrever")
    check.add_argument("--trace", type=_path_arg, default=DEFAULT_TRACE)
    check.add_argument("--corpus-dir", type=_path_arg, default=DEFAULT_CORPUS)
    check.add_argument("--active-index", type=_path_arg, default=DEFAULT_ACTIVE_INDEX)
    check.add_argument("--runs-dir", type=_path_arg, default=DEFAULT_RUNS)
    check.add_argument("--report", type=_path_arg, default=DEFAULT_REPORT)
    check.add_argument("--skip-batteries", action="store_true")

    run = subparsers.add_parser("run", help="gera candidato, valida e executa baterias")
    run.add_argument("--trace", type=_path_arg, default=DEFAULT_TRACE)
    run.add_argument("--corpus-dir", type=_path_arg, default=DEFAULT_CORPUS)
    run.add_argument("--active-index", type=_path_arg, default=DEFAULT_ACTIVE_INDEX)
    run.add_argument("--runs-dir", type=_path_arg, default=DEFAULT_RUNS)
    run.add_argument("--report", type=_path_arg, default=DEFAULT_REPORT)
    run.add_argument("--dataset", type=_path_arg, action="append", default=None)
    run.add_argument("--validation-ratio", type=float, default=0.2)
    run.add_argument("--skip-batteries", action="store_true")
    run.add_argument("--promote", action="store_true", help="promove somente se todos os gates passarem")
    run.add_argument("--dry-run", action="store_true", help="executa somente o pré-voo, sem criar uma rodada")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "report":
        result = report_only(args.trace, args.corpus_dir, args.report)
    elif args.command == "preflight":
        result = preflight(
            trace_path=args.trace,
            corpus_dir=args.corpus_dir,
            active_index=args.active_index,
            runs_dir=args.runs_dir,
            report_path=args.report,
            require_batteries=not args.skip_batteries,
        )
    elif args.dry_run:
        result = preflight(
            trace_path=args.trace,
            corpus_dir=args.corpus_dir,
            active_index=args.active_index,
            runs_dir=args.runs_dir,
            report_path=args.report,
            require_batteries=not args.skip_batteries,
        )
        result["dry_run"] = True
    else:
        result = run_pipeline(
            trace_path=args.trace,
            corpus_dir=args.corpus_dir,
            active_index=args.active_index,
            runs_dir=args.runs_dir,
            report_path=args.report,
            datasets=args.dataset,
            validation_ratio=args.validation_ratio,
            skip_batteries=args.skip_batteries,
            promote=args.promote,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "preflight" or (args.command == "run" and args.dry_run):
        return 1 if result.get("status") == "blocked" else 0
    if args.command == "run":
        if args.promote and result.get("status") != "promoted":
            return 2
        if result.get("status") in {"candidate-blocked", "blocked-preflight"}:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
