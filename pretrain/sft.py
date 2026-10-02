"""Ajuste supervisionado (SFT): ensina o modelo pré-treinado a conversar.

Formato igual ao do servidor: ``<|user|>\\n...\\n<|assistant|>\\n`` + resposta + <eos>.
A perda só conta nos tokens da resposta (e do <eos>). Fontes: conversas do projeto
(python/data/*.jsonl, só turnos de texto) e, opcionalmente, conversas humanas abertas
(--hf oasst1,dolly). Nenhuma resposta vem de outro modelo de linguagem.
"""

import argparse
import glob
import hashlib
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
EOS = 2
IGNORE = -100


def local_conversations(pattern):
    for path in sorted(glob.glob(str(ROOT / pattern))):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                messages = json.loads(line).get("messages", [])
            except json.JSONDecodeError:
                continue
            turns = [(m["role"], m["content"]) for m in messages
                     if m.get("role") in {"user", "assistant"} and isinstance(m.get("content"), str)]
            # Traços de ferramenta ficam de fora: sem tool_call/tool_result o diálogo quebraria.
            if len(turns) == len(messages) and len(turns) >= 2 and turns[0][0] == "user" and turns[-1][0] == "assistant":
                yield turns


def hf_conversations(name):
    from datasets import load_dataset
    if name == "dolly":  # databricks-dolly-15k: escrito por humanos, CC-BY-SA
        for row in load_dataset("databricks/databricks-dolly-15k", split="train"):
            prompt = row["instruction"] + (f"\n\n{row['context']}" if row["context"] else "")
            yield [("user", prompt), ("assistant", row["response"])]
    elif name == "oasst1":  # OpenAssistant: conversas humanas, Apache-2.0; pt e en
        rows = {r["message_id"]: r for r in load_dataset("OpenAssistant/oasst1", split="train")
                if r["lang"] in {"pt-BR", "pt", "en"} and not r["deleted"]}
        for row in rows.values():
            if row["role"] != "assistant" or (row.get("rank") not in (0, None)):
                continue
            chain, node = [], row
            while node is not None:
                chain.append(("user" if node["role"] == "prompter" else "assistant", node["text"]))
                node = rows.get(node["parent_id"])
            chain.reverse()
            if chain and chain[0][0] == "user":
                yield chain
    else:
        raise SystemExit(f"fonte desconhecida: {name}")


def encode_conversation(turns, encode, context):
    tokens, labels = [], []
    for role, text in turns:
        if role == "user":
            ids = encode(f"<|user|>\n{text.strip()}\n<|assistant|>\n")
            tokens += ids
            labels += [IGNORE] * len(ids)
        else:
            ids = encode(text.strip()) + [EOS]
            tokens += ids
            labels += ids
    if len(tokens) > context or all(label == IGNORE for label in labels):
        return None
    return tokens, labels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="pretrain_data", help="pasta com tokenizer.json")
    parser.add_argument("--base", required=True, help="best.safetensors do pré-treino")
    parser.add_argument("--out", default="sft_out")
    parser.add_argument("--local", default="python/data/*.jsonl")
    parser.add_argument("--hf", default="", help="oasst1,dolly")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-tokens", type=int, default=32768)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--replay", type=float, default=0.1, help="fração de janelas de pré-treino misturadas")
    parser.add_argument("--holdout", type=float, default=0.03)
    parser.add_argument("--device", default="cuda" if __import__("torch").cuda.is_available() else "cpu")
    parser.add_argument("--seed", type=int, default=1337)
    args = parser.parse_args()

    import numpy as np
    import torch
    import torch.nn.functional as F
    from checkpoint_io import load_checkpoint, save_checkpoint
    from model import build_model
    from prepare_data import fast_tokenizer
    from tokenizer import ByteBPETokenizer

    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)
    data, out = Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tokenizer_path = data / "tokenizer.json"
    fast = fast_tokenizer(tokenizer_path)
    encode = lambda text: fast.encode(text).ids
    project_tokenizer = ByteBPETokenizer.load(tokenizer_path)  # decodifica como o servidor

    checkpoint = load_checkpoint(args.base)
    config = checkpoint["config"]
    context = int(config["context_length"])
    device = torch.device(args.device)
    model = build_model(config)
    model.load_state_dict(checkpoint["state_dict"])
    model.to(device).train()

    sources = {"local": list(local_conversations(args.local))}
    for name in filter(None, args.hf.split(",")):
        sources[name] = list(hf_conversations(name))
    train, held = [], []
    for name, conversations in sources.items():
        kept = 0
        for turns in conversations:
            example = encode_conversation(turns, encode, context)
            if example is None:
                continue
            kept += 1
            key = int(hashlib.sha256(turns[0][1].encode()).hexdigest(), 16) % 1000
            (held if key < args.holdout * 1000 else train).append(example)
        print(f"[sft] {name}: {kept}/{len(conversations)} conversas usadas")
    if not train or not held:
        raise SystemExit("poucos exemplos: ajuste --holdout ou as fontes")
    print(f"[sft] treino {len(train)} / validação {len(held)} conversas")

    pretrain_windows = None
    if args.replay > 0 and (data / "train.bin").exists():
        pretrain_windows = np.memmap(data / "train.bin", dtype=np.uint16, mode="r")

    def batches(examples, shuffle):
        order = list(range(len(examples)))
        if shuffle:
            rng.shuffle(order)
        current, longest = [], 0
        for index in order:
            size = len(examples[index][0])
            if current and max(longest, size) * (len(current) + 1) > args.batch_tokens:
                yield current
                current, longest = [], 0
            current.append(examples[index])
            longest = max(longest, size)
        if current:
            yield current

    def collate(batch):
        width = max(len(tokens) for tokens, _ in batch)
        x = torch.zeros(len(batch), width, dtype=torch.long)
        y = torch.full((len(batch), width), IGNORE, dtype=torch.long)
        for row, (tokens, labels) in enumerate(batch):
            x[row, :len(tokens)] = torch.tensor(tokens)
            y[row, :len(labels)] = torch.tensor(labels)
        return x[:, :-1].to(device), y[:, 1:].to(device)

    use_bf16 = device.type == "cuda" and torch.cuda.is_bf16_supported()
    dtype = torch.bfloat16 if use_bf16 else torch.float16
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda" and not use_bf16)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, betas=(0.9, 0.95), weight_decay=0.01)
    steps_per_epoch = sum(1 for _ in batches(train, False))
    total = max(1, steps_per_epoch * args.epochs)
    warmup = max(1, total // 20)

    def loss_of(x, y):
        with torch.autocast(device_type=device.type, dtype=dtype, enabled=device.type == "cuda"):
            logits = model(x)
        return F.cross_entropy(logits.float().reshape(-1, logits.shape[-1]), y.reshape(-1), ignore_index=IGNORE)

    @torch.no_grad()
    def validate():
        model.eval()
        losses = [loss_of(*collate(b)).item() for b in batches(held, False)]
        model.train()
        return sum(losses) / len(losses)

    print(f"[sft] validação inicial: {validate():.3f}")
    step, best = 0, float("inf")
    for epoch in range(args.epochs):
        train_batches = list(batches(train, True))
        if pretrain_windows is not None:
            extra = int(len(train_batches) * args.replay)
            for _ in range(extra):
                rows = max(1, args.batch_tokens // context)
                starts = [rng.randrange(0, len(pretrain_windows) - context - 1) for _ in range(rows)]
                windows = [pretrain_windows[s:s + context + 1].astype(np.int64).tolist() for s in starts]
                train_batches.append([(w, list(w)) for w in windows])
            rng.shuffle(train_batches)
        for batch in train_batches:
            rate = args.lr * min(1.0, (step + 1) / warmup) * (0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * min(1, step / total))))
            for group in optimizer.param_groups:
                group["lr"] = rate
            loss = loss_of(*collate(batch))
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad(set_to_none=True)
            step += 1
        value = validate()
        print(f"[sft] época {epoch + 1}/{args.epochs}: validação {value:.3f}")
        if value < best:
            best = value
            weights = {k: v.detach().float().cpu() for k, v in model.state_dict().items()}
            meta = {key: v for key, v in checkpoint.items() if key not in {"state_dict", "config"}}
            meta.update(sft={"epochs_done": epoch + 1, "val_loss": value, "sources": list(sources), "base": Path(args.base).name})
            save_checkpoint({"state_dict": weights, "config": {**config, "sft": True}, **meta}, out / "sft.safetensors")

    # Amostras: o primeiro sinal real de que o modelo conversa.
    model.eval()
    with torch.inference_mode():
        for prompt in ("Olá! Quem é você?", "Escreva uma função Python que soma dois números.", "Explique o que é um mutex."):
            ids = encode(f"<|user|>\n{prompt}\n<|assistant|>\n")
            logits, caches, length = model.prefill_with_cache(torch.tensor([ids], device=device), cache_capacity=min(context, len(ids) + 160))
            produced = []
            for _ in range(160):
                token = int(logits[0].argmax())
                if token == EOS:
                    break
                produced.append(token)
                if length >= min(context, len(ids) + 160) - 1:
                    break
                logits, caches = model.forward_next_with_cache(torch.tensor([token], device=device), length, caches, length)
                length += 1
            print(f"\n> {prompt}\n{project_tokenizer.decode(produced)}")
    print(f"\n[sft] melhor validação {best:.3f}; pesos em {out}/sft.safetensors")


if __name__ == "__main__":
    main()
