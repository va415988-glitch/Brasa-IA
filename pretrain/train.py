"""Pré-treino do zero (sem pesos externos) com a arquitetura do projeto.

Usa python/model.py e grava safetensors + metadados no formato que o servidor
local já lê (checkpoint_io.save_checkpoint). Retomável: guarda o estado
completo em --out/last.pt e continua dele automaticamente.

Padrões: Muon nas matrizes dos blocos (AdamW em embeddings e normas), agenda WSD
(aquecimento, platô, decaimento 1-sqrt no fim), QK-norm e z-loss. Na agenda WSD,
retomar com um --tokens maior antes do decaimento estende o treino sem recomeçar.
Com --init, o treino continua a partir dos pesos de um .safetensors (arquitetura
herdada dele) com otimizador e agenda novos: serve para seguir um treino que já
decaiu, sobre os dados que ele ainda não viu.
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
    parser.add_argument("--lr", type=float, default=0, help="0 = 2e-3 com Muon, 6e-4 com AdamW")
    parser.add_argument("--optimizer", choices=("muon", "adamw"), default="muon")
    parser.add_argument("--schedule", choices=("wsd", "cosine"), default="wsd")
    parser.add_argument("--decay-frac", type=float, default=0.2, help="fração final dos passos em decaimento (WSD)")
    parser.add_argument("--min-lr-ratio", type=float, default=None, help="padrão: 0 na WSD, 0,1 no cosseno")
    parser.add_argument("--warmup-steps", type=int, default=200)
    parser.add_argument("--weight-decay", type=float, default=0.1)
    parser.add_argument("--z-loss", type=float, default=1e-4, help="peso da penalidade logsumexp² (0 desliga)")
    parser.add_argument("--no-qk-norm", action="store_true", help="arquitetura sem QK-norm (linha de base)")
    parser.add_argument("--init", default="", help="continua dos pesos deste .safetensors (ignora --preset e --no-qk-norm)")
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
    from checkpoint_io import load_checkpoint, save_checkpoint
    from model import build_model

    torch.manual_seed(args.seed)
    if args.optimizer == "muon" and not hasattr(torch.optim, "Muon"):
        raise SystemExit(f"torch {torch.__version__} não tem torch.optim.Muon (exige 2.9+): "
                         "atualize o torch ou use --optimizer adamw")
    args.lr =args.lr or (2e-3 if args.optimizer == "muon" else 6e-4)
    if args.min_lr_ratio is None:
        args.min_lr_ratio = 0.0 if args.schedule == "wsd" else 0.1
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
        **PRESETS[args.preset], "qk_norm": not args.no_qk_norm,
    }
    initial = None
    if args.init:
        initial = load_checkpoint(args.init)
        if initial.get("tokenizer_sha256") != hashlib.sha256(tokenizer_path.read_bytes()).hexdigest():
            raise SystemExit(f"{args.init} foi treinado com outro tokenizer que {tokenizer_path}")
        config = {**initial["config"], "qk_norm": bool(initial["config"].get("qk_norm", False))}
        config.pop("sft", None)
        if config["vocab_size"] != vocab_size:
            raise SystemExit("vocabulário do checkpoint difere do tokenizer")
    ctx = config["context_length"]

    device = torch.device(args.device)
    model = build_model(config)
    if initial is not None:
        model.load_state_dict(initial["state_dict"])
        print(f"pesos iniciais de {args.init} ({initial.get('steps', '?')} passos, val_loss {initial.get('val_loss', float('nan')):.3f})")
    model = model.to(device)
    params = sum(p.numel() for p in model.parameters())
    budget = int(args.tokens) or 20 * params
    budget = min(budget, int(meta["train_tokens"] * 4))  # no máximo ~4 épocas
    accum = max(1, args.batch_tokens // (args.micro_batch * ctx))
    tokens_per_step = accum * args.micro_batch * ctx
    total_steps = max(1, budget // tokens_per_step)
    print(f"modelo {config['layers']}x{config['hidden_size']}: {params / 1e6:.1f}M parâmetros, vocab {vocab_size}, ctx {ctx}")
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

    # Muon ortogonaliza a atualização das matrizes internas; embeddings (ligados à
    # cabeça de saída) e vetores de norma ficam no AdamW, como recomendado.
    matrices = [p for n, p in model.named_parameters() if p.ndim >= 2 and n.startswith("blocks.")]
    embeddings = [p for n, p in model.named_parameters() if p.ndim >= 2 and not n.startswith("blocks.")]
    vectors = [p for p in model.parameters() if p.ndim < 2]
    optimizers = []
    if args.optimizer == "muon":
        optimizers.append(torch.optim.Muon(matrices, lr=args.lr, weight_decay=args.weight_decay,
                                           adjust_lr_fn="match_rms_adamw"))
        adam_groups = [{"params": vectors, "weight_decay": 0.0},
                       {"params": embeddings, "weight_decay": args.weight_decay}]
    else:  # mesma ordem de grupos dos estados antigos, para retomá-los
        adam_groups = [{"params": [p for p in model.parameters() if p.ndim >= 2], "weight_decay": args.weight_decay},
                       {"params": vectors, "weight_decay": 0.0}]
    optimizers.append(torch.optim.AdamW(adam_groups, lr=args.lr, betas=(0.9, 0.95), fused=device.type == "cuda"))
    use_bf16 = device.type == "cuda" and torch.cuda.is_bf16_supported()
    dtype = torch.bfloat16 if use_bf16 else torch.float16
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda" and not use_bf16)

    def lr_at(step):
        floor = args.min_lr_ratio
        if step < args.warmup_steps:
            return args.lr * (step + 1) / args.warmup_steps
        if args.schedule == "wsd":
            decay_start = max(args.warmup_steps, int(total_steps * (1 - args.decay_frac)))
            if step < decay_start:
                return args.lr
            progress = min(1, (step - decay_start) / max(1, total_steps - decay_start))
            return args.lr * (floor + (1 - floor) * (1 - math.sqrt(progress)))
        progress = (step - args.warmup_steps) / max(1, total_steps - args.warmup_steps)
        return args.lr * (floor + (1 - floor) * 0.5 * (1 + math.cos(math.pi * min(1, progress))))

    step, best_val = 0, float("inf")
    last = out / "last.pt"
    if last.exists():
        state = torch.load(last, map_location="cpu", weights_only=False)
        saved = state.get("config") or {}  # estados antigos não guardavam config nem QK-norm
        if saved and {k: saved.get(k) for k in PRESETS["tiny"]} != {k: config[k] for k in PRESETS["tiny"]}:
            raise SystemExit(f"{last} é de outra arquitetura; use outro --out")
        if saved.get("qk_norm", False) != config["qk_norm"]:
            raise SystemExit(f"{last} difere em qk_norm (use --no-qk-norm para estados antigos) ou outro --out")
        if state.get("optimizer_kind", "adamw") != args.optimizer:
            raise SystemExit(f"{last} usa outro otimizador; use outro --out")
        model.load_state_dict(state["model"])
        for optimizer, saved_state in zip(optimizers, state.get("optimizers") or [state["optimizer"]]):
            optimizer.load_state_dict(saved_state)
        if state.get("scaler"):
            scaler.load_state_dict(state["scaler"])
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
                    "source": "pretrain-from-scratch", "init_from": Path(args.init).name or None}
        save_checkpoint({"state_dict": weights, **meta_out}, out / f"{name}.safetensors")

    def save_state():
        torch.save({"model": model.state_dict(), "optimizers": [o.state_dict() for o in optimizers],
                    "optimizer_kind": args.optimizer, "scaler": scaler.state_dict(), "config": config,
                    "step": step, "best_val": best_val}, last.with_suffix(".tmp"))
        os.replace(last.with_suffix(".tmp"), last)

    def train_loss(logits, y):
        logits = logits.float().view(-1, vocab_size)
        loss = F.cross_entropy(logits, y.view(-1))
        if args.z_loss:  # mantém os logits de saída numa escala estável
            loss = loss + args.z_loss * torch.logsumexp(logits, dim=-1).pow(2).mean()
        return loss

    print(f"otimizador {args.optimizer}, agenda {args.schedule}, lr {args.lr:.1e}, "
          f"qk_norm {config['qk_norm']}, z-loss {args.z_loss}")
    started = tick = time.time()
    model.train()
    stop = False
    while step < total_steps and not stop:
        for optimizer in optimizers:
            for group in optimizer.param_groups:
                group["lr"] = lr_at(step)
        loss_sum = 0.0
        for _ in range(accum):
            x, y = batch("train", args.micro_batch, generator)
            with autocast():
                logits = forward(x)
            loss = train_loss(logits, y) / accum
            scaler.scale(loss).backward()
            loss_sum += loss.item()
        for optimizer in optimizers:
            scaler.unscale_(optimizer)
        grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        for optimizer in optimizers:
            scaler.step(optimizer)
        scaler.update()
        for optimizer in optimizers:
            optimizer.zero_grad(set_to_none=True)
        step += 1
        if step % 10 == 0:
            rate = 10 * tokens_per_step / (time.time() - tick)
            tick = time.time()
            print(f"passo {step}/{total_steps} loss {loss_sum:.3f} lr {lr_at(step):.2e} "
                  f"grad {float(grad_norm):.2f} {rate / 1e3:.1f}k tok/s", flush=True)
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
