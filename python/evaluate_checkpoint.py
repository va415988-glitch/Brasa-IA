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


def generate(model, tokenizer, config, prompt, limit):
    ids = tokenizer.encode(f"<|user|>\n{prompt}\n<|assistant|>\n")
    control_tokens = {
        token_id for token_id in range(config["vocab_size"])
        if any(marker in tokenizer.decode([token_id]) for marker in ("<", ">", "|"))
    }
    with torch.no_grad():
        for _ in range(limit):
            context = ids[-config["context_length"]:]
            logits = model(torch.tensor([context]))[0, -1].clone()
            if control_tokens:
                logits[list(control_tokens)] = float("-inf")
            next_id = int(torch.argmax(logits).item())
            ids.append(next_id)
            if next_id == tokenizer.special_tokens["<eos>"]:
                break
    return tokenizer.decode(ids).split("<|assistant|>\n", 1)[-1].replace("<eos>", "").strip()


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
    parser.add_argument("--tokens", type=int, default=32)
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
