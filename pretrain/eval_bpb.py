"""Compara checkpoints com tokenizers diferentes por bits por byte (BPB).

A perda por token não é comparável entre vocabulários distintos; bits por byte
é: soma da log-perda (em bits) de todos os tokens dividida pelos bytes UTF-8 do
texto. Menor é melhor. Cada checkpoint é carregado pelo mesmo caminho do
servidor (``ModelService._load_local_model``), com seu próprio tokenizer.

Os textos de avaliação são documentos reservados (separados por NUL) que não
entraram no treino; ``assemble_corpus.py`` grava esses arquivos em
``<dados>/heldout/``.

    .venv/bin/python pretrain/eval_bpb.py --heldout pretrain_data/heldout \\
        model/godmode/context-32768-v1/candidate.safetensors pretrain_out/.../best.safetensors
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))


def load(checkpoint_path, tokenizer_override=None):
    from checkpoint_io import load_checkpoint
    checkpoint = load_checkpoint(checkpoint_path)
    if checkpoint["config"].get("architecture") == "decoder_transformer_v2" and tokenizer_override:
        # Checkpoints recém-treinados ainda apontam para o tokenizer da pasta de dados.
        from model import build_model
        from tokenizer import ByteBPETokenizer
        model = build_model(checkpoint["config"])
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        return model, ByteBPETokenizer.load(tokenizer_override), checkpoint["config"]
    from model_server import ModelService
    service = ModelService.__new__(ModelService)
    service.checkpoint_path = str(checkpoint_path)
    service.local_model = service.local_tokenizer = service.local_config = service.local_model_error = None
    service._load_local_model()
    if service.local_model is None:
        raise SystemExit(f"{checkpoint_path}: {service.local_model_error}")
    return service.local_model, service.local_tokenizer, service.local_config


def documents(path, limit_docs, max_chars):
    raw = Path(path).read_text(encoding="utf-8").split("\x00")
    docs = [doc.strip()[:max_chars] for doc in raw if len(doc.strip()) >= 200]
    return docs[:limit_docs]


def bits_per_byte(model, tokenizer, config, docs, window):
    import torch
    import torch.nn.functional as F
    context = min(int(config.get("training_context_length") or config["context_length"]), window)
    bos = tokenizer.special_tokens.get("<bos>")
    total_bits, total_bytes = 0.0, 0
    with torch.inference_mode():
        for doc in docs:
            ids = tokenizer.encode_fast(doc)
            if bos is not None:
                ids = [bos] + ids
            # Janelas sem sobreposição: cada token é previsto uma vez, com o
            # contexto disponível dentro da sua janela.
            for start in range(0, len(ids) - 1, context):
                chunk = ids[start:start + context + 1]
                if len(chunk) < 2:
                    continue
                tokens = torch.tensor([chunk[:-1]])
                logits = model(tokens).float()[0]
                loss = F.cross_entropy(logits, torch.tensor(chunk[1:]), reduction="sum")
                total_bits += float(loss) / math.log(2)
            total_bytes += len(doc.encode("utf-8"))
    return total_bits / max(1, total_bytes)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("checkpoints", nargs="+")
    parser.add_argument("--heldout", required=True, help="pasta com <fonte>.txt reservados")
    parser.add_argument("--docs", type=int, default=40, help="documentos por fonte")
    parser.add_argument("--max-chars", type=int, default=4000)
    parser.add_argument("--window", type=int, default=512)
    parser.add_argument("--output")
    parser.add_argument("--tokenizer", help="tokenizer para checkpoints v2 recém-treinados (ex.: pretrain_data/tokenizer.json)")
    args = parser.parse_args()
    files = sorted(Path(args.heldout).glob("*.txt"))
    if not files:
        raise SystemExit(f"nenhum .txt em {args.heldout}")
    report = {"schema": "brasa-bpb-eval/v1", "heldout": str(args.heldout), "docs_per_source": args.docs,
              "max_chars": args.max_chars, "results": {}}
    for checkpoint in args.checkpoints:
        model, tokenizer, config = load(checkpoint, args.tokenizer)
        parameters = sum(p.numel() for p in model.parameters())
        row = {"parameters": parameters, "sources": {}}
        for path in files:
            docs = documents(path, args.docs, args.max_chars)
            if docs:
                row["sources"][path.stem] = round(bits_per_byte(model, tokenizer, config, docs, args.window), 4)
        values = list(row["sources"].values())
        row["mean_bpb"] = round(sum(values) / len(values), 4) if values else None
        report["results"][str(checkpoint)] = row
        print(f"{checkpoint}: {parameters / 1e6:.1f}M parâmetros, BPB médio {row['mean_bpb']} {row['sources']}", flush=True)
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
