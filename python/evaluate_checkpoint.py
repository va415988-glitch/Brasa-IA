"""Porta de qualidade para geração livre de checkpoints."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import torch

from model import build_model
from tokenizer import ByteBPETokenizer
from checkpoint_io import load_checkpoint
from generation_utils import generation_control_token_ids


def _clean_generated(text):
    """Corta uma continuação que parece outro pedido do conjunto de treino."""
    lines = str(text or '').splitlines()
    for index, line in enumerate(lines[1:], start=1):
        if len('\n'.join(lines[:index]).strip()) < 80:
            continue
        if re.match(r'^\s*(?:o que e(?:\s|$)|o que é(?:\s|$)|como(?:\s|$)|qual(?:\s|$)|explique(?:\s|$)|quando(?:\s|$)|por que(?:\s|$)|porque(?:\s|$))', line, flags=re.I):
            return '\n'.join(lines[:index]).strip()
    return str(text or '').strip()


def _degenerate(text):
    """Detecta repetição antes que ela contamine uma resposta útil."""
    text = str(text or '')
    if _clean_generated(text) != text.strip():
        return True
    words = text.split()
    if len(words) >= 12 and len(set(words)) / len(words) < 0.65:
        return True
    if re.search(r'\b(\w+)(?:\s+\1){1,}\b', text or '', flags=re.I):
        return True
    if re.search(r'(?i)([a-zà-ÿ]{2,12})(?:\1){2,}', text or ''):
        return True
    if re.search(r'(?i)([a-zà-ÿ])\1{3,}', text or ''):
        return True
    return False


def generate(model, tokenizer, config, prompt, limit):
    ids = tokenizer.encode(f"<|user|>\n{prompt}\n<|assistant|>\n")
    control_tokens = generation_control_token_ids(tokenizer, config["vocab_size"])
    with torch.no_grad():
        for _ in range(limit):
            context = ids[-config["context_length"]:]
            logits = model(torch.tensor([context]))[0, -1].clone()
            if control_tokens:
                logits[list(control_tokens)] = float("-inf")
            next_id = int(torch.argmax(logits).item())
            candidate_ids = ids + [next_id]
            candidate = tokenizer.decode(candidate_ids).split("<|assistant|>\n", 1)[-1].replace("<eos>", "").strip()
            # Não incluir o token que inicia um ciclo degenerado: o prefixo
            # anterior pode ser uma resposta válida e será avaliado como tal.
            if _degenerate(candidate):
                break
            ids.append(next_id)
            if next_id == tokenizer.special_tokens["<eos>"]:
                break
    answer = tokenizer.decode(ids).split("<|assistant|>\n", 1)[-1].replace("<eos>", "").strip()
    return _clean_generated(answer)


def assess(answer, terms):
    normalized = answer.lower()
    control = "<|" in answer or "|>" in answer
    repeated = bool(re.search(r"(.{3,})\1{2,}", answer, flags=re.I))
    relevant = sum(term.lower() in normalized for term in terms)
    return {
        "nonempty": len(answer) >= 8,
        "no_control_markers": not control,
        "no_obvious_repetition": not repeated,
        "relevant": relevant >= max(1, min(2, len(terms))),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--eval", default="model/eval_generation.jsonl")
    parser.add_argument("--tokenizer", default="model/tokenizer.json")
    parser.add_argument("--tokens", type=int, default=1024)
    args = parser.parse_args()
    checkpoint = load_checkpoint(args.checkpoint)
    model = build_model(checkpoint["config"])
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    tokenizer = ByteBPETokenizer.load(args.tokenizer)
    rows = [json.loads(line) for line in Path(args.eval).read_text(encoding="utf-8").splitlines() if line.strip()]
    passed = 0
    for row in rows:
        answer = generate(model, tokenizer, checkpoint["config"], row["prompt"], args.tokens)
        checks = assess(answer, row["terms"])
        ok = all(checks.values())
        passed += ok
        print(json.dumps({"id": row["id"], "ok": ok, "checks": checks, "answer": answer}, ensure_ascii=False))
    print(f"resultado: {passed}/{len(rows)}")
    raise SystemExit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()
