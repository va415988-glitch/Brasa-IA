"""Pré-treino do zero (sem pesos externos) com a arquitetura do projeto.

Usa python/model.py e grava safetensors + metadados no formato que o servidor
local já lê (checkpoint_io.save_checkpoint). Retomável: guarda o estado
completo em --out/last.pt e continua dele automaticamente.
"""

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

PRESETS = {
    # ~0,9M: só para testar o pipeline em CPU
    "tiny":  dict(layers=2,  hidden_size=128, attention_heads=4,  kv_heads=2, context_length=256),
    # ~40M com vocab 32k: T4 em dias, A100 em horas
    "small": dict(layers=8,  hidden_size=512, attention_heads=8,  kv_heads=4, context_length=1024),
    # ~100M: alvo principal numa A100/L4
    "base":  dict(layers=12, hidden_size=768, attention_heads=12, kv_heads=4, context_length=2048),
    # ~300M: só vale com A100 e vários dias; precisa de bem mais dados
    "large": dict(layers=24, hidden_size=1024, attention_heads=16, kv_heads=4, context_length=2048),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="pretrain_data")
    parser.add_argument("--out", default="pretrain_out")
    parser.add_argument("--preset", choices=PRESETS, default="small")
    parser.add_argument("--tokens", type=float, default=0, help="orçamento de tokens de treino; 0 = 20x os parâmetros")
    parser.add_argument("--batch-tokens", type=int, default=262144, help="tokens por passo de otimização")
    parser.add_argument("--micro-batch", type=int, default=16, help="sequências por passada (reduza se faltar memória)")
    parser.add_argument("--lr", type=float, default=6e-4)
    parser.add_argument("--min-lr-ratio", type=float, default=0.1)
    parser.add_argument("--warmup-steps", type=int, default=200)
    parser.add_argument("--weight-decay", type=float, default=0.1)
    parser.add_argument("--eval-every", type=int, default=250)
    parser.add_argument("--eval-batches", type=int, default=20)
    parser.add_argument("--save-every", type=int, default=500)
    parser.add_argument("--max-hours", type=float, default=0, help="para com segurança após N horas (a sessão do Colab acaba)")
    parser.add_argument("--compile", action="store_true", help="torch.compile: ~1,3x mais rápido na GPU, compila por alguns minutos")
    parser.add_argument("--device", default="cuda" if __import__("torch").cuda.is_available() else "cpu")
    parser.add_argument("--seed", type=int, default=1337)
    args = parser.parse_args()

    import numpy as np
    import torch
    import torch.nn.functional as F
    from checkpoint_io import save_checkpoint
    from model import build_model

    torch.manual_seed(args.seed)
    data, out = Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((data / "meta.json").read_text())
    tokenizer_path = data / "tokenizer.json"
    vocab_size = len(json.loads(tokenizer_path.read_text())["vocab"]) + 4
    config = {
        "name": f"brasa-{args.preset}-from-scratch", "architecture": "decoder_transformer_v2",
        "vocab_size": vocab_size,
        "language_priority": ["pt-BR", "code", "en"], "tool_call_format": "json", "runtime": "python-local",
        "generation_length": 512, "training_context_length": PRESETS[args.preset]["context_length"],
        "tokenizer_path": "model/pretrained/tokenizer.json", "system_prompt_trained": False,
        "training_policy": {"method": "pretrain-from-scratch", "external_llm": False, "ollama": False},
        **PRESETS[args.preset],
    }
    ctx = config["context_length"]

    device = torch.device(args.device)
    model = build_model(config).to(device)
    params = sum(p.numel() for p in model.parameters())
    budget = int(args.tokens) or 20 * params
    budget = min(budget, int(meta["train_tokens"] * 4))  # no máximo ~4 épocas
    accum = max(1, args.batch_tokens // (args.micro_batch * ctx))
    tokens_per_step = accum * args.micro_batch * ctx
    total_steps = max(1, budget // tokens_per_step)
    print(f"modelo {args.preset}: {params / 1e6:.1f}M parâmetros, vocab {vocab_size}, ctx {ctx}")
    print(f"dados: {meta['train_tokens'] / 1e6:.0f}M tokens; orçamento {budget / 1e6:.0f}M "
          f"({budget / meta['train_tokens']:.2f} épocas); {total_steps} passos de {tokens_per_step} tokens")

    train = np.memmap(data / "train.bin", dtype=np.uint16, mode="r")
    val = np.memmap(data / "val.bin", dtype=np.uint16, mode="r")

    def batch(split, size, generator):
        source = train if split == "train" else val
        starts = torch.randint(0, len(source) - ctx - 1, (size,), generator=generator).tolist()
        x = torch.from_numpy(np.stack([source[s:s + ctx].astype(np.int64) for s in starts]))
        y = torch.from_numpy(np.stack([source[s + 1:s + 1 + ctx].astype(np.int64) for s in starts]))
        return x.to(device), y.to(device)

    decay = [p for p in model.parameters() if p.ndim >= 2]
    no_decay = [p for p in model.parameters() if p.ndim < 2]
    optimizer = torch.optim.AdamW(
        [{"params": decay, "weight_decay": args.weight_decay}, {"params": no_decay, "weight_decay": 0.0}],
        lr=args.lr, betas=(0.9, 0.95), fused=device.type == "cuda")
    use_bf16 = device.type == "cuda" and torch.cuda.is_bf16_supported()
    dtype = torch.bfloat16 if use_bf16 else torch.float16
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda" and not use_bf16)

    def lr_at(step):
        if step < args.warmup_steps:
            return args.lr * (step + 1) / args.warmup_steps
        progress = (step - args.warmup_steps) / max(1, total_steps - args.warmup_steps)
        return args.lr * (args.min_lr_ratio + (1 - args.min_lr_ratio) * 0.5 * (1 + math.cos(math.pi * min(1, progress))))

    step, best_val = 0, float("inf")
    last = out / "last.pt"
    if last.exists():
        state = torch.load(last, map_location="cpu", weights_only=False)
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        step, best_val = state["step"], state["best_val"]
        print(f"retomando do passo {step}")
    generator = torch.Generator().manual_seed(args.seed + step)

    forward = torch.compile(model) if args.compile else model

    def autocast():
        return torch.autocast(device_type=device.type, dtype=dtype, enabled=device.type == "cuda")

    @torch.no_grad()
    def evaluate():
        model.eval()
        g = torch.Generator().manual_seed(0)
        losses = []
        for _ in range(args.eval_batches):
            x, y = batch("val", args.micro_batch, g)
            with autocast():
                logits = model(x)
            losses.append(F.cross_entropy(logits.float().view(-1, vocab_size), y.view(-1)).item())
        model.train()
        return sum(losses) / len(losses)

    def export(name):
        weights = {k: v.detach().float().cpu() for k, v in model.state_dict().items()}
        meta_out = {"config": config, "steps": step, "val_loss": best_val,
                    "tokenizer_sha256": hashlib.sha256(tokenizer_path.read_bytes()).hexdigest(),
                    "source": "pretrain-from-scratch"}
        save_checkpoint({"state_dict": weights, **meta_out}, out / f"{name}.safetensors")

    def save_state():
        torch.save({"model": model.state_dict(), "optimizer": optimizer.state_dict(),
                    "step": step, "best_val": best_val}, last.with_suffix(".tmp"))
        os.replace(last.with_suffix(".tmp"), last)

    started = time.time()
    model.train()
    stop = False
    while step < total_steps and not stop:
        for group in optimizer.param_groups:
            group["lr"] = lr_at(step)
        loss_sum = 0.0
        for _ in range(accum):
            x, y = batch("train", args.micro_batch, generator)
            with autocast():
                logits = forward(x)
            loss = F.cross_entropy(logits.float().view(-1, vocab_size), y.view(-1)) / accum
            scaler.scale(loss).backward()
            loss_sum += loss.item()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        scaler.step(optimizer)
        scaler.update()
        optimizer.zero_grad(set_to_none=True)
        step += 1
        if step % 10 == 0:
            print(f"passo {step}/{total_steps} loss {loss_sum:.3f} lr {lr_at(step):.2e}", flush=True)
        if step % args.eval_every == 0 or step == total_steps:
            value = evaluate()
            print(f"== passo {step} val_loss {value:.3f} (ppl {math.exp(value):.1f})", flush=True)
            if value < best_val:
                best_val = value
                export("best")
        if step % args.save_every == 0:
            save_state()
        if args.max_hours and time.time() - started > args.max_hours * 3600:
            print("limite de tempo atingido; salvando para retomar")
            stop = True
    save_state()
    export("final")
    print(f"fim no passo {step}; melhor val_loss {best_val:.3f}. Pesos em {out}/best.safetensors")


if __name__ == "__main__":
    main()
