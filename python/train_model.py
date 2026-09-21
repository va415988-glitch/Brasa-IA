"""Treina a primeira rede própria, do zero, sem carregar pesos externos."""

import argparse
import copy
import json
import os
import struct
import time
from pathlib import Path


def load_tokens(path):
    data = Path(path).read_bytes()
    if len(data) % 4:
        raise ValueError("arquivo de tokens corrompido")
    return list(struct.unpack(f"<{len(data) // 4}I", data))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="model/config.json")
    parser.add_argument("--tokens", default="model/train_tokens.bin")
    parser.add_argument("--val-tokens", default="")
    parser.add_argument("--output", default="model/checkpoints/compact-01.pt")
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--val-fraction", type=float, default=0.1)
    parser.add_argument("--eval-every", type=int, default=100)
    parser.add_argument("--save-every", type=int, default=0, help="salva um checkpoint parcial a cada N passos; 0 usa --eval-every")
    parser.add_argument("--allow-experimental-context", action="store_true", help="permite treinar uma janela abaixo de 8192 sem elegibilidade de produção")
    parser.add_argument("--allow-memory-risk", action="store_true", help="experimento explícito; ignora o bloqueio conservador de memória")
    args = parser.parse_args()

    import torch
    import torch.nn.functional as functional

    from model import build_model

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    # Aplica a inicialização corrigida em novos treinos e registra a escolha
    # nos metadados; checkpoints existentes nunca têm seus pesos reiniciados.
    config.setdefault('initialization', 'scaled-normal-v1')
    tokens = load_tokens(args.tokens)
    context = config["context_length"]
    minimum_context = 8192
    if context < minimum_context and not args.allow_experimental_context and not args.config.endswith("config.json"):
        raise ValueError(f"checkpoint de produção exige pelo menos {minimum_context} tokens de contexto; recebido {context}")
    if context >= 8192 and args.batch_size > 1:
        print("aviso: contexto longo com batch > 1 pode exceder a memória; use batch-size 1 para o primeiro experimento")
    if context >= 8192:
        available_mb = None
        try:
            available_mb = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) // 1024
        except (OSError, StopIteration, ValueError):
            pass
        estimated_mb = args.batch_size * config.get('layers', 1) * 2 * context * context * 4 * 8 / (1024 * 1024)
        if available_mb is not None and estimated_mb >= available_mb * 0.45 and not args.allow_memory_risk:
            raise RuntimeError(f"treino bloqueado por segurança: pico conservador estimado em {estimated_mb:.0f} MiB, memória disponível {available_mb} MiB")
    if len(tokens) < context + 2:
        raise ValueError(f"são necessários pelo menos {context + 2} tokens; encontrados {len(tokens)}")

    if args.val_tokens:
        train_tokens = tokens
        val_tokens = load_tokens(args.val_tokens)
    else:
        split = int(len(tokens) * (1.0 - max(0.0, min(args.val_fraction, 0.4))))
        train_tokens = tokens[:split]
        val_tokens = tokens[split:]
    if len(train_tokens) < context + 2 or len(val_tokens) < context + 2:
        raise ValueError("o corpus precisa ter dados suficientes para treino e validação")
    torch.manual_seed(42)
    device = torch.device(args.device)
    train_tensor = torch.tensor(train_tokens, dtype=torch.long, device=device)
    val_tensor = torch.tensor(val_tokens, dtype=torch.long, device=device)
    train_windows = train_tensor.unfold(0, context + 1, 1)
    val_windows = val_tensor.unfold(0, context + 1, 1)
    model = build_model(config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.01)
    model.train()
    started = time.monotonic()
    last_loss = None
    best_val_loss = float("inf")
    best_state = None
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    save_every = args.save_every or args.eval_every

    for step in range(1, args.steps + 1):
        starts = torch.randint(0, train_windows.shape[0], (args.batch_size,), device=device)
        batch = train_windows.index_select(0, starts)
        inputs = batch[:, :-1]
        targets = batch[:, 1:]
        logits = model(inputs)
        loss = functional.cross_entropy(logits.reshape(-1, config["vocab_size"]), targets.reshape(-1))
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        last_loss = float(loss.detach().cpu())

        if step == 1 or step % max(1, args.eval_every) == 0 or step == args.steps:
            elapsed = time.monotonic() - started
            model.eval()
            with torch.no_grad():
                val_count = min(16, val_windows.shape[0])
                val_starts = torch.linspace(0, val_windows.shape[0] - 1, steps=val_count).long()
                val_batch = val_windows.index_select(0, val_starts)
                val_inputs = val_batch[:, :-1]
                val_targets = val_batch[:, 1:]
                val_logits = model(val_inputs)
                val_loss = float(functional.cross_entropy(val_logits.reshape(-1, config["vocab_size"]), val_targets.reshape(-1)).detach().cpu())
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = copy.deepcopy(model.state_dict())
            if step % max(1, save_every) == 0 and best_state is not None:
                partial = output.with_suffix(output.suffix + ".partial")
                torch.save({
                    "config": config,
                    "state_dict": best_state,
                    "steps": step,
                    "loss": last_loss,
                    "best_val_loss": best_val_loss,
                    "val_fraction": args.val_fraction,
                    "status": "partial",
                }, partial)
            model.train()
            print(f"step={step} loss={last_loss:.4f} val_loss={val_loss:.4f} elapsed={elapsed:.1f}s")

    torch.save({"config": config, "state_dict": best_state or model.state_dict(), "steps": args.steps, "loss": last_loss, "best_val_loss": best_val_loss, "val_fraction": args.val_fraction}, output)
    print(f"checkpoint salvo: {output}")


if __name__ == "__main__":
    main()
