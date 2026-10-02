#!/usr/bin/env python3
"""Stress test CPU da janela de contexto e do cache incremental de geração."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

import torch

from checkpoint_io import load_checkpoint
from model import build_model, extend_position_embeddings
from tokenizer import ByteBPETokenizer


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", default="model/godmode/context-32768-v1/candidate.safetensors")
    parser.add_argument("--context", type=int, default=32768)
    parser.add_argument("--generate", type=int, default=4)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--output", default="model/godmode/context-32768-v1/context_verification.json")
    args = parser.parse_args()
    checkpoint_path = Path(args.checkpoint)
    if not checkpoint_path.is_absolute():
        checkpoint_path = ROOT / checkpoint_path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    if not 1 <= args.generate < args.context:
        parser.error("--generate precisa ser positivo e menor que --context")

    checkpoint = load_checkpoint(checkpoint_path)
    config = dict(checkpoint["config"])
    if args.context > int(config["context_length"]):
        trained = int(config.get("training_context_length") or config["context_length"])
        checkpoint["state_dict"]["position_embedding.weight"] = extend_position_embeddings(
            checkpoint["state_dict"]["position_embedding.weight"], args.context, trained,
        )
        config["context_length"] = args.context
    if args.context > int(config["context_length"]):
        raise ValueError("contexto solicitado maior que os pesos posicionais disponíveis")
    config["attention_chunk_size"] = int(config.get("attention_chunk_size") or 512)
    torch.set_num_threads(max(1, int(args.threads)))
    model = build_model(config).eval()
    model.load_state_dict(checkpoint["state_dict"])
    tokenizer_path = Path(config["tokenizer_path"])
    if not tokenizer_path.is_absolute():
        tokenizer_path = ROOT / tokenizer_path
    tokenizer = ByteBPETokenizer.load(tokenizer_path)

    prompt_length = args.context - args.generate
    text = "Registro de contexto: a informação persistida é 483917 e deve permanecer disponível no fim da janela.\n" * 512
    token_ids = tokenizer.encode_fast(text)
    while len(token_ids) < prompt_length:
        text += text
        token_ids = tokenizer.encode_fast(text)
    prompt_ids = token_ids[-prompt_length:]
    if len(prompt_ids) != prompt_length:
        raise RuntimeError(f"tokenização curta: {len(prompt_ids)} < {prompt_length}")

    started = time.perf_counter()
    with torch.inference_mode():
        logits, cache, cache_length = model.prefill_with_cache(
            torch.tensor([prompt_ids], dtype=torch.long), cache_capacity=args.context,
        )
        generated = 0
        while generated < args.generate:
            next_token = torch.argmax(logits, dim=-1).reshape(1, 1)
            generated += 1
            if generated < args.generate:
                logits, cache = model.forward_next_with_cache(
                    next_token,
                    position=cache_length,
                    caches=cache,
                    cache_length=cache_length,
                )
                cache_length += 1

    elapsed = time.perf_counter() - started
    report = {
        "schema": "context-window-runtime-verification/v1",
        "checkpoint": str(checkpoint_path.relative_to(ROOT) if checkpoint_path.is_relative_to(ROOT) else checkpoint_path),
        "checkpoint_sha256": sha256(checkpoint_path),
        "requested_context_tokens": args.context,
        "prompt_tokens": len(prompt_ids),
        "generated_tokens": generated,
        "total_tokens_exercised": len(prompt_ids) + generated,
        "target_fully_exercised": len(prompt_ids) + generated == args.context,
        "output_logits_finite": bool(torch.isfinite(logits).all()),
        "elapsed_seconds": round(elapsed, 3),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "trained_context_tokens": int(config.get("training_context_length") or config["context_length"]),
        "semantic_quality_evaluated": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["target_fully_exercised"] or not report["output_logits_finite"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
