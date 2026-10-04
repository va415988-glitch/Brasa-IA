"""Avalia um checkpoint no held-out do SFT agêntico (datasets/agent_sft_v1/heldout.jsonl.gz).

Backends: ``server`` gera pelo mesmo caminho do servidor (ModelService.local_reply com
structured_decision, CPU); ``direct`` gera direto no modelo (GPU no Colab) com as mesmas regras de
decodificação (gulosa, sem tokens de controle, para no <eos>, corta nos marcadores de papel), sem os
filtros de qualidade extras do servidor. A saída é conferida com o validador do produto. Métricas por tipo:
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
ROLE_MARKER = re.compile(r"<\|(?:user|system|context|eos)\|>")


class DirectGenerator:
    """Decodificação gulosa direto no checkpoint, no formato de prompt do servidor."""

    def __init__(self, checkpoint, device):
        import torch
        from checkpoint_io import load_checkpoint
        from model import build_model
        from tokenizer import ByteBPETokenizer
        self.torch = torch
        data = load_checkpoint(checkpoint)
        self.config = data["config"]
        tokenizer_path = ROOT / self.config.get("tokenizer_path", "model/pretrained/tokenizer.json")
        if not tokenizer_path.exists():
            raise SystemExit(f"tokenizer não encontrado em {tokenizer_path} (copie o tokenizer.json dos dados para lá)")
        self.tokenizer = ByteBPETokenizer.load(tokenizer_path)
        self.model = build_model(self.config)
        self.model.load_state_dict(data["state_dict"])
        self.model.to(device).eval()
        self.device = device
        self.context = int(self.config["context_length"])
        specials = self.tokenizer.special_tokens
        self.eos = specials.get("<eos>", 2)
        self.blocked = [specials[name] for name in ("<pad>", "<bos>", "<unk>") if name in specials]
        self.last = {}

    def __call__(self, prompt, max_tokens):
        torch = self.torch
        ids = self.tokenizer.encode_fast(f"<|user|>\n{prompt}\n<|assistant|>\n")
        budget = max(1, min(max_tokens, self.context - len(ids) - 1))
        produced = []
        with torch.inference_mode():
            logits, caches, length = self.model.prefill_with_cache(
                torch.tensor([ids], device=self.device), cache_capacity=min(self.context, len(ids) + budget + 1))
            for _ in range(budget):
                scores = logits[0].float()
                scores[self.blocked] = float("-inf")
                token = int(scores.argmax())
                if token == self.eos:
                    break
                produced.append(token)
                if len(produced) >= 15 and len(set(produced[-12:])) <= 2:
                    break
                if length + 1 >= self.context:
                    break
                logits, caches = self.model.forward_next_with_cache(
                    torch.tensor([token], device=self.device), length, caches, length)
                length += 1
        self.last = {"input_tokens": len(ids), "output_tokens": len(produced)}
        text = ROLE_MARKER.split(self.tokenizer.decode(produced))[0]
        return text.replace("<eos>", "").strip()


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
    parser.add_argument("--backend", choices=("server", "direct"), default="server")
    parser.add_argument("--device", default="cuda" if __import__("torch").cuda.is_available() else "cpu",
                        help="só para --backend direct")
    parser.add_argument("--heldout", default=str(ROOT / "datasets/agent_sft_v1/heldout.jsonl.gz"))
    parser.add_argument("--per-kind", type=int, default=20, help="exemplos por tipo (0 = todos)")
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--report", required=True)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    os.environ["IA_LOCAL_NUM_PREDICT"] = str(args.max_tokens)

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

    from tool_registry import ToolRegistry
    registry = ToolRegistry()
    if args.backend == "server":
        from model_server import ModelService
        service = ModelService(args.checkpoint, trace_path=None, cognitive_router_manifest=None)
        if service.local_model is None:
            raise SystemExit("checkpoint indisponível: " + str(service.local_model_error))
        registry = service.tools

        def generate(prompt):
            raw = service.local_reply([{"role": "user", "content": prompt}], max_tokens_limit=args.max_tokens,
                                      structured_decision=True, capture_rejected=True) or ""
            return raw or (service.last_generation or {}).get("raw_output", "")
    else:
        direct = DirectGenerator(args.checkpoint, args.device)

        def generate(prompt):
            return direct(prompt, args.max_tokens)
    cases, totals = [], defaultdict(lambda: defaultdict(list))
    for number, row in enumerate(chosen, 1):
        started = time.monotonic()
        raw = generate(row["messages"][0]["content"])
        result = score(raw, row, registry)
        cases.append({"id": row["id"], "kind": row["kind"], **result, "raw": raw[:2000],
                      "target": row["messages"][1]["content"], "seconds": round(time.monotonic() - started, 2)})
        for name, value in result.items():
            if value is not None:
                totals[row["kind"]][name].append(float(value))
                totals["TOTAL"][name].append(float(value))
        print(f"[{number}/{len(chosen)}] {row['kind']}: " + " ".join(f"{k}={v}" for k, v in result.items() if v is not None), flush=True)

    summary = {kind: {name: round(sum(v) / len(v), 3) for name, v in metrics.items()} | {"n": len(next(iter(metrics.values())))}
               for kind, metrics in totals.items()}
    report = {"schema": "agent-sft-eval/v1", "checkpoint": args.checkpoint, "heldout": args.heldout, "backend": args.backend,
              "max_tokens": args.max_tokens, "summary": summary, "cases": cases}
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{'tipo':22} {'n':>4} {'valid':>6} {'decisão':>8} {'ferram.':>8} {'evid.':>6} {'overlap':>8}")
    for kind, values in sorted(summary.items(), key=lambda item: item[0] == "TOTAL"):
        cell = lambda name: f"{values[name]:.2f}" if name in values else "-"
        print(f"{kind:22} {values['n']:>4} {cell('valid'):>6} {cell('decision'):>8} {cell('tool'):>8} {cell('evidence'):>6} {cell('overlap'):>8}")


if __name__ == "__main__":
    main()
