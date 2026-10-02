#!/usr/bin/env python3
"""Executa e compara 50 perguntas pelo mesmo endpoint de chat usado pela interface."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "tests/data/avaliacao_dialogo_50.json"
DEFAULT_OUTPUT_DIR = ROOT / "logs/dialogue-evaluations"
RUN_SCHEMA = "dialogue-evaluation-run/v1"


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFD", str(value or "").lower())
    return "".join(char for char in value if unicodedata.category(char) != "Mn")


def contains_phrase(text: str, phrase: str) -> bool:
    normalized_text = normalize(text)
    normalized_phrase = normalize(phrase).strip()
    if not normalized_phrase:
        return False
    pattern = re.escape(normalized_phrase).replace(r"\ ", r"\s+")
    if normalized_phrase[0].isalnum():
        pattern = r"\b" + pattern
    if normalized_phrase[-1].isalnum():
        pattern += r"\b"
    return re.search(pattern, normalized_text) is not None


def validate_suite(suite: object) -> list[dict]:
    if not isinstance(suite, dict) or not isinstance(suite.get("cases"), list):
        raise ValueError("arquivo de avaliação inválido: esperava-se um objeto com cases")
    cases = suite["cases"]
    if len(cases) != 50:
        raise ValueError(f"o conjunto precisa ter exatamente 50 perguntas; encontrado: {len(cases)}")
    ids = set()
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"caso {index} não é um objeto")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise ValueError(f"id ausente ou repetido no caso {index}")
        ids.add(case_id)
        if not isinstance(case.get("messages"), list) or not case["messages"]:
            raise ValueError(f"{case_id}: messages precisa conter o histórico da conversa")
        if not isinstance(case.get("checks"), list) or not case["checks"]:
            raise ValueError(f"{case_id}: cada pergunta precisa de critérios verificáveis")
        for message in case["messages"]:
            if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
                raise ValueError(f"{case_id}: papel de mensagem inválido")
            if not isinstance(message.get("content"), str):
                raise ValueError(f"{case_id}: conteúdo de mensagem inválido")
        for check in case["checks"]:
            if (not isinstance(check, dict) or not isinstance(check.get("name"), str)
                    or not isinstance(check.get("any"), list) or not check["any"]):
                raise ValueError(f"{case_id}: cada critério precisa de name e any")
    return cases


def request_json(url: str, *, method: str = "GET", payload: dict | None = None,
                 timeout: float = 15.0) -> tuple[int, dict]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        body = error.read()
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise ConnectionError(str(error)) from error
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        decoded = {"error": body.decode("utf-8", errors="replace")[:1000]}
    if not isinstance(decoded, dict):
        decoded = {"error": "a API retornou JSON fora do formato esperado"}
    return status, decoded


def evaluate_case(case: dict, status_code: int, response: dict, elapsed_ms: float) -> dict:
    answer = response.get("text") if isinstance(response.get("text"), str) else ""
    actual_speech_act = ((response.get("dialogue") or {}).get("speech_act")
                         if isinstance(response.get("dialogue"), dict) else None)
    response_ok = status_code == 200 and response.get("ok") is True and bool(answer.strip())
    actual_intent = response.get("intent")
    expected_intent = case.get("expected_intent", "conversation")
    intent_match = response_ok and actual_intent == expected_intent
    expected_speech_act = case.get("expected_speech_act")
    speech_act_match = (actual_speech_act == expected_speech_act
                        if expected_speech_act and actual_speech_act else None)
    signals = []
    for check in case["checks"]:
        matched = [term for term in check["any"] if contains_phrase(answer, term)]
        signals.append({
            "name": check["name"],
            "passed": bool(matched),
            "matched": matched,
        })

    normalized_answer = normalize(answer)
    generic_patterns = (
        "como modelo de linguagem",
        "como uma ia",
        "como inteligencia artificial",
        "posso ajudar em algo mais",
        "espero ter ajudado",
    )
    found_generic = [phrase for phrase in generic_patterns if contains_phrase(normalized_answer, phrase)]
    min_chars = int(case.get("min_chars", 35))
    text_is_specific = len(answer.strip()) >= min_chars and not found_generic
    scored_checks = [
        {"name": "resposta recebida", "passed": response_ok},
        {"name": f"rota: {expected_intent}", "passed": intent_match},
        *signals,
        {"name": "resposta específica e não excessivamente curta", "passed": text_is_specific},
    ]
    passed = sum(item["passed"] for item in scored_checks)
    score = round(100.0 * passed / len(scored_checks), 1)
    return {
        "id": case["id"],
        "category": case["category"],
        "question": case["messages"][-1]["content"],
        "messages": case["messages"],
        "expected_intent": expected_intent,
        "actual_intent": actual_intent,
        "expected_speech_act": expected_speech_act,
        "actual_speech_act": actual_speech_act,
        "speech_act_match": speech_act_match,
        "http_status": status_code,
        "response_ok": response_ok,
        "answer": answer,
        "backend": response.get("backend"),
        "provider": (response.get("dialogue") or {}).get("provider"),
        "model": (response.get("dialogue") or {}).get("model"),
        "generation": response.get("generation"),
        "tool_call": response.get("tool_call"),
        "tool_calls": response.get("tool_calls"),
        "error": response.get("error"),
        "error_code": response.get("error_code"),
        "elapsed_ms": round(elapsed_ms, 1),
        "checks": scored_checks,
        "signals": signals,
        "generic_phrases": found_generic,
        "answer_chars": len(answer.strip()),
        "score_pct": score,
    }


def complete_read_only_research(base_url: str, case: dict, response: dict,
                                timeout: float) -> tuple[dict, dict | None]:
    """Execute at most one explicitly read-only research call, then synthesize it."""
    call = response.get("tool_call")
    if not isinstance(call, dict) or call.get("tool") != "research_web":
        return response, None
    arguments = call.get("arguments")
    allowed = {"query", "topic", "max_results", "save_to_corpus", "category"}
    if (not isinstance(arguments, dict) or arguments.get("save_to_corpus") is not False
            or set(arguments) - allowed or not str(arguments.get("query") or "").strip()):
        return response, {"status": "not_executed", "reason": "research_call_not_read_only"}

    execution_started = time.monotonic()
    call_payload = {
        "tool": "research_web",
        "arguments": arguments,
        "request_id": str(call.get("id") or "dialogue-eval-research")[:160],
    }
    try:
        tool_status, tool_result = request_json(
            base_url + "/api/tool-call", method="POST", payload=call_payload,
            timeout=min(timeout, 180.0),
        )
    except ConnectionError as error:
        return response, {"status": "failed", "error": str(error)}
    result_data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
    tool_ok = tool_status == 200 and tool_result.get("ok") is True
    execution = {
        "status": "completed" if tool_ok else "failed",
        "http_status": tool_status,
        "tool_elapsed_ms": tool_result.get("elapsed_ms"),
        "page_count": len(result_data.get("pages") or []),
        "citation_ids": result_data.get("citation_ids") or [],
        "source_urls": [page.get("url") for page in result_data.get("pages") or []
                        if isinstance(page, dict) and page.get("url")],
        "saved_to_corpus": result_data.get("saved_to_corpus"),
        "error": tool_result.get("error"),
    }
    observation = {
        "ok": tool_ok,
        "tool": "research_web",
        "data": result_data,
        "error": tool_result.get("error"),
    }
    messages = [*case["messages"], {
        "role": "tool",
        "content": json.dumps(observation, ensure_ascii=False),
    }]
    follow_payload = {
        "request_id": str(call.get("id") or "dialogue-eval-research")[:160] + "-synthesis",
        "messages": messages,
    }
    try:
        synthesis_started = time.monotonic()
        follow_status, follow_response = request_json(
            base_url + "/api/chat", method="POST", payload=follow_payload,
            timeout=timeout,
        )
    except ConnectionError as error:
        execution["synthesis_elapsed_ms"] = round((time.monotonic() - synthesis_started) * 1000, 1)
        execution["total_elapsed_ms"] = round((time.monotonic() - execution_started) * 1000, 1)
        execution["synthesis_error"] = str(error)
        return response, execution
    execution["synthesis_elapsed_ms"] = round((time.monotonic() - synthesis_started) * 1000, 1)
    execution["total_elapsed_ms"] = round((time.monotonic() - execution_started) * 1000, 1)
    if follow_status == 200 and follow_response.get("ok") is True and str(follow_response.get("text") or "").strip():
        execution["synthesis_status"] = "completed"
        return follow_response, execution
    execution["synthesis_status"] = "failed"
    execution["synthesis_error"] = follow_response.get("error") or f"HTTP {follow_status}"
    return response, execution


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    count = lambda predicate: sum(1 for row in rows if predicate(row))
    signal_checks = [signal for row in rows for signal in row.get("signals", [])]
    signal_total = len(signal_checks)
    response_count = count(lambda row: row["response_ok"])
    fallback_markers = (
        "não consegui formular uma resposta confiável para",
        "não consegui iniciar uma investigação útil para",
        "não consegui produzir uma resposta confiável para",
        "ainda não tenho evidência local suficiente.",
        "contexto estruturado local:",
    )
    completed_answers = count(
        lambda row: row["response_ok"]
        and not row.get("tool_call")
        and not row.get("tool_calls")
        and not any(marker in normalize(row.get("answer") or "") for marker in fallback_markers)
    )
    labelled_speech_acts = [row for row in rows if row["response_ok"] and row["actual_speech_act"]]
    category_rows: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        category_rows[row["category"]].append(row)
    categories = {
        name: {
            "count": len(items),
            "score_pct": round(sum(item["score_pct"] for item in items) / len(items), 1),
            "intent_accuracy_pct": round(100 * sum(item["actual_intent"] == item["expected_intent"]
                                                     and item["response_ok"] for item in items) / len(items), 1),
        }
        for name, items in sorted(category_rows.items())
    }
    latencies = sorted(float(row.get("elapsed_ms", 0.0)) for row in rows)
    p50 = latencies[(len(latencies) - 1) // 2] if latencies else 0.0
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else 0.0
    metrics = {
        "questions_total": total,
        "responses_received": response_count,
        "response_rate_pct": round(100 * response_count / total, 1) if total else 0.0,
        "completed_answer_rate_pct": round(100 * completed_answers / total, 1) if total else 0.0,
        "intent_accuracy_pct": round(100 * count(lambda row: row["response_ok"] and row["actual_intent"] == row["expected_intent"]) / total, 1) if total else 0.0,
        "speech_act_label_coverage_pct": round(100 * len(labelled_speech_acts) / response_count, 1) if response_count else 0.0,
        "speech_act_accuracy_pct": round(100 * sum(row["speech_act_match"] is True for row in labelled_speech_acts) / len(labelled_speech_acts), 1) if labelled_speech_acts else None,
        "content_signal_coverage_pct": round(100 * sum(item["passed"] for item in signal_checks) / signal_total, 1) if signal_total else 0.0,
        "specific_answer_rate_pct": round(100 * count(lambda row: row["checks"][-1]["passed"]) / total, 1) if total else 0.0,
        "generic_answer_count": count(lambda row: bool(row["generic_phrases"])),
        "average_case_score_pct": round(sum(row["score_pct"] for row in rows) / total, 1) if total else 0.0,
        "latency_ms": {"p50": round(p50, 1), "p95": round(p95, 1), "max": round(max(latencies), 1) if latencies else 0.0},
        "backend_counts": dict(sorted(Counter(row.get("backend") or "unknown" for row in rows).items())),
        "read_only_research": {
            "planned": count(lambda row: row.get("tool_execution") is not None),
            "executed": count(lambda row: (row.get("tool_execution") or {}).get("status") in {"completed", "failed"}),
            "synthesized": count(lambda row: (row.get("tool_execution") or {}).get("synthesis_status") == "completed"),
        },
    }
    # O índice agrega indicadores observáveis; não é uma nota subjetiva de inteligência.
    metrics["composite_observed_score_pct"] = round(
        metrics["intent_accuracy_pct"] * 0.5
        + metrics["content_signal_coverage_pct"] * 0.4
        + metrics["specific_answer_rate_pct"] * 0.1,
        1,
    )

    failures = Counter()
    failure_cases: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for check in row["checks"]:
            if not check["passed"]:
                failures[check["name"]] += 1
                failure_cases[check["name"]].append(row["id"])
    negatives = [
        {"criterion": name, "failures": number, "failure_rate_pct": round(100 * number / total, 1),
         "case_ids": failure_cases[name][:12]}
        for name, number in failures.most_common(12)
    ]
    weakest_cases = sorted(rows, key=lambda row: (row["score_pct"], row["id"]))[:10]
    return {"metrics": metrics, "categories": categories, "negative_points": negatives,
            "weakest_cases": [{"id": row["id"], "category": row["category"],
                               "score_pct": row["score_pct"], "failed_checks": [
                                   check["name"] for check in row["checks"] if not check["passed"]
                               ]} for row in weakest_cases]}


def find_previous_run(output_dir: Path, suite_id: str, baseline_arg: str | None,
                      current_path: Path) -> dict | None:
    candidates = [Path(baseline_arg).expanduser() if baseline_arg else None]
    if not baseline_arg and output_dir.is_dir():
        candidates.extend(sorted(output_dir.glob("run-*.json"), key=lambda item: item.stat().st_mtime, reverse=True))
    for path in candidates:
        if path is None or not path.is_file() or path.resolve() == current_path.resolve():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("schema") == RUN_SCHEMA and data.get("suite_id") == suite_id:
            return data
    return None


def compare(previous: dict | None, current_rows: list[dict], current_summary: dict) -> dict | None:
    if not previous:
        return None
    old_metrics = previous.get("summary", {}).get("metrics", {})
    new_metrics = current_summary["metrics"]
    metric_names = (
        "composite_observed_score_pct", "intent_accuracy_pct", "content_signal_coverage_pct",
        "specific_answer_rate_pct", "response_rate_pct",
    )
    deltas = {name: round(float(new_metrics.get(name, 0)) - float(old_metrics.get(name, 0)), 1)
              for name in metric_names}
    previous_rows = {row.get("id"): row for row in previous.get("results", []) if isinstance(row, dict)}
    current_by_id = {row["id"]: row for row in current_rows}
    recovered = []
    regressed = []
    for case_id, current in current_by_id.items():
        old = previous_rows.get(case_id)
        if not old:
            continue
        old_pass = float(old.get("score_pct", 0)) >= 80
        new_pass = float(current.get("score_pct", 0)) >= 80
        if new_pass and not old_pass:
            recovered.append(case_id)
        elif old_pass and not new_pass:
            regressed.append(case_id)
    old_categories = previous.get("summary", {}).get("categories", {})
    category_deltas = {}
    for name, metrics in current_summary["categories"].items():
        if name in old_categories:
            category_deltas[name] = round(float(metrics["score_pct"])
                                          - float(old_categories[name].get("score_pct", 0)), 1)
    return {
        "previous_run_id": previous.get("run_id"),
        "metric_deltas_percentage_points": deltas,
        "category_deltas_percentage_points": category_deltas,
        "recovered_cases": recovered,
        "regressed_cases": regressed,
    }


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    metrics = summary["metrics"]
    speech_act_accuracy = (f"{metrics['speech_act_accuracy_pct']}%"
                           if metrics["speech_act_accuracy_pct"] is not None else "n/a")
    lines = [
        "# Avaliação de diálogo: 50 perguntas",
        "",
        f"- Execução: `{report['run_id']}`",
        f"- Rota testada: `{report['base_url']}/api/chat`; modo de provedor: `{report.get('provider_mode') or 'não informado'}`",
        f"- Modelo(s) disponíveis: `{', '.join(report.get('configured_models') or []) or 'nenhum informado'}`",
        f"- Perguntas respondidas: **{metrics['responses_received']}/{metrics['questions_total']}**",
        "",
        "> A pontuação é baseada em sinais e critérios automáticos definidos por caso. Ela ajuda a localizar regressões; não mede sozinha nuance, verdade factual ou qualidade humana da conversa. “Específica” mede somente um limite de tamanho e algumas frases genéricas conhecidas.",
        "",
        "O índice observado composto pondera roteamento em 50%, sinais de conteúdo por pergunta em 40% e especificidade mínima em 10%. A cobertura dos rótulos de fala é medida à parte quando o chat fornece esse metadado.",
        "",
        "## Indicadores",
        "",
        "| Indicador | Resultado |",
        "|---|---:|",
        f"| Índice observado composto | {metrics['composite_observed_score_pct']}% |",
        f"| Intenção reconhecida | {metrics['intent_accuracy_pct']}% |",
        f"| Resposta final sem contingência nem chamada pendente | {metrics['completed_answer_rate_pct']}% |",
        f"| Rótulos de fala disponíveis | {metrics['speech_act_label_coverage_pct']}% |",
        f"| Acerto do rótulo de fala | {speech_act_accuracy} |",
        f"| Sinais de conteúdo presentes | {metrics['content_signal_coverage_pct']}% |",
        f"| Passa no limite heurístico de tamanho | {metrics['specific_answer_rate_pct']}% |",
        f"| Respostas genéricas detectadas | {metrics['generic_answer_count']} |",
        f"| Latência p50 / p95 / máximo | {metrics['latency_ms']['p50']} / {metrics['latency_ms']['p95']} / {metrics['latency_ms']['max']} ms |",
        f"| Pesquisa só de leitura (planejada / executada / sintetizada) | {metrics['read_only_research']['planned']} / {metrics['read_only_research']['executed']} / {metrics['read_only_research']['synthesized']} |",
        "",
        "Backends observados: " + ", ".join(f"`{name}` {amount}" for name, amount in metrics["backend_counts"].items()) + ".",
        "",
        "## Resultado por tema",
        "",
        "| Tema | Casos | Nota média | Intenção |",
        "|---|---:|---:|---:|",
    ]
    for category, values in summary["categories"].items():
        lines.append(f"| {category} | {values['count']} | {values['score_pct']}% | {values['intent_accuracy_pct']}% |")
    lines.extend(["", "## Pontos que falharam mais", ""])
    if summary["negative_points"]:
        lines.extend(["| Critério | Falhas | Casos |", "|---|---:|---|"])
        for item in summary["negative_points"]:
            lines.append(f"| {item['criterion']} | {item['failures']} ({item['failure_rate_pct']}%) | {', '.join(item['case_ids'])} |")
    else:
        lines.append("Nenhum critério falhou nesta execução.")
    comparison = report.get("comparison")
    lines.extend(["", "## Mudança desde a execução anterior", ""])
    if comparison:
        lines.append(f"Execução comparada: `{comparison['previous_run_id']}`.")
        lines.append("")
        lines.append("| Indicador | Variação |")
        lines.append("|---|---:|")
        for name, delta in comparison["metric_deltas_percentage_points"].items():
            label = name.replace("_pct", "").replace("_", " ")
            lines.append(f"| {label} | {delta:+.1f} p.p. |")
        if comparison["recovered_cases"]:
            lines.append(f"\nCasos que passaram de abaixo para acima de 80%: {', '.join(comparison['recovered_cases'])}.")
        if comparison["regressed_cases"]:
            lines.append(f"\nCasos que caíram de 80% ou mais para abaixo de 80%: {', '.join(comparison['regressed_cases'])}.")
        if not comparison["recovered_cases"] and not comparison["regressed_cases"]:
            lines.append("\nNenhum caso atravessou o limite de 80% nesta comparação.")
    else:
        lines.append("Ainda não há outra execução desta mesma bateria para calcular melhora ou regressão. Rode novamente depois de uma mudança; a comparação será automática.")
    lines.extend(["", "## Casos com menor pontuação", ""])
    lines.extend(["| Caso | Nota | Critérios que falharam |", "|---|---:|---|"])
    rows_by_id = {row["id"]: row for row in report["results"]}
    for item in summary["weakest_cases"]:
        row = rows_by_id[item["id"]]
        failed = ", ".join(item["failed_checks"]) or "—"
        lines.append(f"| {item['id']} | {item['score_pct']}% | {failed} |")
        if row.get("answer"):
            excerpt = " ".join(row["answer"].split())[:360].replace("|", "\\|")
            lines.append(f"| ↳ resposta |  | {excerpt} |")
    lines.extend(["", "## Respostas completas", "", f"As 50 respostas, critérios caso a caso, latência e metadados estão em `{report['json_file']}`.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000",
                        help="endereço do runtime local (padrão: http://127.0.0.1:3000)")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="tempo máximo por pergunta em segundos (padrão: 120)")
    parser.add_argument("--pause-seconds", type=float, default=0.2,
                        help="pausa entre perguntas; a avaliação roda em série")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help="pasta para relatórios; cada execução gera JSON e Markdown")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE,
                        help="arquivo JSON da bateria de 50 perguntas")
    parser.add_argument("--baseline", help="relatório JSON anterior para comparar explicitamente")
    parser.add_argument("--validate-only", action="store_true",
                        help="confere a bateria de 50 perguntas sem chamar a IA")
    args = parser.parse_args()

    if args.timeout <= 0 or args.pause_seconds < 0:
        parser.error("--timeout deve ser positivo e --pause-seconds não pode ser negativo")
    try:
        suite = json.loads(args.suite.read_text(encoding="utf-8"))
        cases = validate_suite(suite)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Erro ao carregar a bateria: {error}", file=sys.stderr)
        return 2
    if args.validate_only:
        print(f"Bateria válida: {len(cases)} perguntas em {len({case['category'] for case in cases})} temas.")
        return 0

    base_url = args.base_url.rstrip("/")
    try:
        status_code, provider_status = request_json(
            base_url + "/api/v1/dialogue/providers", timeout=min(args.timeout, 15.0),
        )
    except ConnectionError as error:
        print(f"Runtime local indisponível em {base_url}: {error}", file=sys.stderr)
        return 2
    if status_code != 200 or provider_status.get("ok") is False:
        print(f"Não consegui consultar os provedores do runtime (HTTP {status_code}): {provider_status.get('error', 'sem detalhe')}", file=sys.stderr)
        return 2
    provider_mode = provider_status.get("mode")

    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"run-{run_id}.json"
    markdown_path = output_dir / f"run-{run_id}.md"
    models = []
    info = provider_status.get("own_checkpoint")
    if isinstance(info, dict) and info.get("configured"):
        models.append(f"{info.get('id', 'checkpoint próprio')}:{info.get('checkpoint') or 'caminho não informado'}")
    print(f"Executando {len(cases)} perguntas em série pelo chat da interface em {base_url} (modo de provedor: {provider_mode or 'não informado'}).", flush=True)

    results = []
    for index, case in enumerate(cases, start=1):
        payload = {
            "request_id": f"dialogue-eval-{run_id}-{case['id']}",
            "messages": case["messages"],
        }
        started = time.monotonic()
        try:
            status_code, response = request_json(
                base_url + "/api/chat", method="POST", payload=payload,
                timeout=args.timeout,
            )
        except ConnectionError as error:
            status_code, response = 0, {"ok": False, "error": str(error)}
        elapsed_ms = (time.monotonic() - started) * 1000
        response, tool_execution = complete_read_only_research(
            base_url, case, response, args.timeout,
        )
        if tool_execution:
            elapsed_ms += float(tool_execution.get("total_elapsed_ms") or 0.0)
        row = evaluate_case(case, status_code, response, elapsed_ms)
        row["tool_execution"] = tool_execution
        results.append(row)
        status_label = f"{row['score_pct']:.0f}%" if row["response_ok"] else f"falha HTTP {status_code}"
        print(f"[{index:02d}/50] {case['id']}: {status_label} · {elapsed_ms / 1000:.1f}s", flush=True)
        if args.pause_seconds and index < len(cases):
            time.sleep(args.pause_seconds)

    summary = summarize(results)
    previous = find_previous_run(output_dir, suite.get("suite_id", "dialogue-intent-50-v1"),
                                 args.baseline, json_path)
    report = {
        "schema": RUN_SCHEMA,
        "suite_id": suite.get("suite_id", "dialogue-intent-50-v1"),
        "suite_description": suite.get("description"),
        "run_id": run_id,
        "created_at": now.isoformat(),
        "base_url": base_url,
        "provider_mode": provider_mode,
        "configured_models": models,
        "provider_status": provider_status,
        "summary": summary,
        "comparison": compare(previous, results, summary),
        "results": results,
        "scoring_note": "Critérios automáticos por caso e heurística de resposta específica; revisar manualmente nuance, correção factual e naturalidade.",
        "json_file": str(json_path),
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"\nNota observada: {summary['metrics']['composite_observed_score_pct']}% · intenção: {summary['metrics']['intent_accuracy_pct']}% · sinais: {summary['metrics']['content_signal_coverage_pct']}%")
    print("Backends observados: " + ", ".join(
        f"{name}={amount}" for name, amount in summary["metrics"]["backend_counts"].items()
    ))
    print("Esta bateria mede o chat integrado; não mede isoladamente a qualidade dos pesos neurais.")
    if report["comparison"]:
        delta = report["comparison"]["metric_deltas_percentage_points"]["composite_observed_score_pct"]
        print(f"Variação do índice observado contra {report['comparison']['previous_run_id']}: {delta:+.1f} p.p.")
    else:
        print("Sem execução anterior desta bateria: esta rodada será a linha de base.")
    print(f"Relatório: {markdown_path}")
    print(f"Respostas completas: {json_path}")
    return 0 if all(row["response_ok"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
