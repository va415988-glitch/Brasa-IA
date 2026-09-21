"""Pré-voo seguro para treinamento de checkpoints com contexto longo."""

import argparse
import json
import os
from pathlib import Path

from train_model import load_tokens


def estimate(config: dict, tokens: int, batch: int) -> dict:
    context = int(config.get("context_length", 0))
    hidden = int(config.get("hidden_size", 0))
    layers = int(config.get("layers", 0))
    windows = max(0, tokens - context - 1)
    # Estimativa conservadora para logits, estados e atenção em float32.
    # O custo efetivo inclui gradientes, autograd, projeções QKV e cópias do
    # Transformer. A matriz isolada subestima brutalmente o pico.
    attention_mb = batch * layers * 2 * context * context * 4 * 8 / (1024 * 1024)
    activation_mb = batch * context * hidden * layers * 16 * 4 / (1024 * 1024)
    available_mb = None
    try:
        available_mb = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:"))) // 1024
    except (OSError, StopIteration, ValueError):
        pass
    return {
        "context_tokens": context,
        "corpus_tokens": tokens,
        "training_windows": windows,
        "batch_size": batch,
        "estimated_attention_mb": round(attention_mb, 1),
        "estimated_activation_mb": round(activation_mb, 1),
        "estimated_peak_mb": round(attention_mb + activation_mb, 1),
        "safe_first_batch": 1 if context >= 8192 else batch,
        "available_memory_mb": available_mb,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="model/config-context-8192.json")
    parser.add_argument("--tokens", default="model/train_tokens.bin")
    parser.add_argument("--batch-size", type=int, default=1)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    count = len(load_tokens(args.tokens))
    report = estimate(config, count, args.batch_size)
    gates = {
        "minimum_context": report["context_tokens"] >= 8192,
        "enough_corpus_for_window": report["training_windows"] > 0,
        "batch_is_safe_default": args.batch_size == report["safe_first_batch"],
        "memory_headroom": report["available_memory_mb"] is None or report["estimated_peak_mb"] < report["available_memory_mb"] * 0.45,
    }
    strategy = "direct-attention" if gates["memory_headroom"] else "chunked-memory"
    result = {"schema": "long-context-preflight/v1", "config": args.config, "tokens": args.tokens, "estimate": report, "gates": gates, "strategy": strategy, "ready": all(gates.values())}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
