"""Avalia um checkpoint no held-out do SFT agêntico (datasets/agent_sft_v1/heldout.jsonl.gz).

Gera pelo mesmo caminho do servidor (ModelService.local_reply com structured_decision, decodificação
gulosa) e confere a saída com o validador do produto sobre o quadro original. Métricas por tipo:
  valid      JSON aceito por validate_decision (envelope, catálogo, evidência, caminhos)
  decision   mesma decisão do alvo (answer/consult/blocked)
  tool       em consultas: mesma ferramenta e mesmo argumento principal (path, query, url, pattern)
  evidence   em respostas: mesmos evidence_ids
  overlap    F1 de palavras entre o texto gerado e o do alvo (respostas)
"""

import argparse
import gzip
import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

MAIN_ARGUMENT = ("path", "query", "url", "pattern")


def words(text):
    return re.findall(r"\w+", text.lower())


def overlap(predicted, expected):
    a, b = words(predicted), words(expected)
    if not a or not b:
        return 0.0
    common = sum(min(a.count(w), b.count(w)) for w in set(a))
    if not common:
        return 0.0
    precision, recall = common / len(a), common / len(b)
    return 2 * precision * recall / (precision + recall)


def score(raw, row, registry):
    from cognitive_dialogue import build_frame, decision_shape, validate_decision
    target = json.loads(row["messages"][1]["content"])
    request = row["request"]
    frame = build_frame(request["messages"], {"schema": "agent-cognition/v1", "available_tools": request["tools"]}, registry)
    result = {"valid": False, "decision": False, "tool": None, "evidence": None, "overlap": None}
    try:
        value = decision_shape(raw)
    except ValueError:
        return result
    try:
        validate_decision(raw, frame, registry)
        result["valid"] = True
    except (ValueError, KeyError, TypeError):
        pass
    result["decision"] = value["decision"] == target["decision"]
    if target["decision"] == "consult":
        call, expected = value.get("tool_call") or {}, target["tool_call"]
        key = next((k for k in MAIN_ARGUMENT if k in expected["arguments"]), None)
        result["tool"] = (call.get("tool") == expected["tool"]
                          and (key is None or (call.get("arguments") or {}).get(key) == expected["arguments"][key]))
    elif target["decision"] == "answer":
        result["evidence"] = sorted(value["evidence_ids"]) == sorted(target["evidence_ids"])
        result["overlap"] = overlap(value["text"], target["text"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--heldout", default=str(ROOT / "datasets/agent_sft_v1/heldout.jsonl.gz"))
    parser.add_argument("--per-kind", type=int, default=20, help="exemplos por tipo (0 = todos)")
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--report", required=True)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    os.environ["IA_LOCAL_NUM_PREDICT"] = str(args.max_tokens)
    from model_server import ModelService

    rows = [json.loads(line) for line in gzip.open(args.heldout, "rt", encoding="utf-8")]
    by_kind = defaultdict(list)
    for row in rows:
        by_kind[row["kind"]].append(row)
    rng = random.Random(args.seed)
    chosen = []
    for kind in sorted(by_kind):
        items = by_kind[kind]
        rng.shuffle(items)
        chosen += items[:args.per_kind] if args.per_kind else items

    service = ModelService(args.checkpoint, trace_path=None, cognitive_router_manifest=None)
    if service.local_model is None:
        raise SystemExit("checkpoint indisponível: " + str(service.local_model_error))
    cases, totals = [], defaultdict(lambda: defaultdict(list))
    for number, row in enumerate(chosen, 1):
        started = time.monotonic()
        raw = service.local_reply([row["messages"][0]], max_tokens_limit=args.max_tokens,
                                  structured_decision=True, capture_rejected=True) or ""
        raw = raw or (service.last_generation or {}).get("raw_output", "")
        result = score(raw, row, service.tools)
        cases.append({"id": row["id"], "kind": row["kind"], **result, "raw": raw[:2000],
                      "target": row["messages"][1]["content"], "seconds": round(time.monotonic() - started, 2)})
        for name, value in result.items():
            if value is not None:
                totals[row["kind"]][name].append(float(value))
                totals["TOTAL"][name].append(float(value))
        print(f"[{number}/{len(chosen)}] {row['kind']}: " + " ".join(f"{k}={v}" for k, v in result.items() if v is not None), flush=True)

    summary = {kind: {name: round(sum(v) / len(v), 3) for name, v in metrics.items()} | {"n": len(next(iter(metrics.values())))}
               for kind, metrics in totals.items()}
    report = {"schema": "agent-sft-eval/v1", "checkpoint": args.checkpoint, "heldout": args.heldout,
              "max_tokens": args.max_tokens, "summary": summary, "cases": cases}
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{'tipo':22} {'n':>4} {'valid':>6} {'decisão':>8} {'ferram.':>8} {'evid.':>6} {'overlap':>8}")
    for kind, values in sorted(summary.items(), key=lambda item: item[0] == "TOTAL"):
        cell = lambda name: f"{values[name]:.2f}" if name in values else "-"
        print(f"{kind:22} {values['n']:>4} {cell('valid'):>6} {cell('decision'):>8} {cell('tool'):>8} {cell('evidence'):>6} {cell('overlap'):>8}")


if __name__ == "__main__":
    main()
