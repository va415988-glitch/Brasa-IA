"""Exporta um checkpoint v2 treinado como candidato carregável pelo servidor local.

Copia o tokenizer para a pasta do modelo, aponta ``tokenizer_path`` para ele,
grava os pesos em bf16 (metade do tamanho; o servidor converte ao carregar) e
anexa a proveniência: corpus usado, linhagem de crescimento e avaliações.

    .venv/bin/python pretrain/export_candidate.py sft_out/sft.safetensors model/brasa-cpu-v1 \\
        --data pretrain_data --report pretrain_out/avaliacao.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("checkpoint")
    parser.add_argument("target_dir", help="pasta dentro do projeto, ex.: model/brasa-cpu-v1")
    parser.add_argument("--data", default="pretrain_data", help="pasta com tokenizer.json e corpus_manifest.json")
    parser.add_argument("--report", action="append", default=[], help="relatórios JSON para anexar (repetível)")
    parser.add_argument("--dtype", choices=("bfloat16", "float32"), default="bfloat16")
    args = parser.parse_args()

    import torch
    from checkpoint_io import load_checkpoint, save_checkpoint
    from model import build_model

    checkpoint = load_checkpoint(args.checkpoint)
    config = dict(checkpoint["config"])
    if config.get("architecture") != "decoder_transformer_v2":
        raise SystemExit("o exportador espera um checkpoint decoder_transformer_v2")
    target = (ROOT / args.target_dir).resolve()
    if not target.is_relative_to(ROOT):
        raise SystemExit("a pasta de destino precisa ficar dentro do projeto")
    target.mkdir(parents=True, exist_ok=True)
    data = Path(args.data)
    tokenizer_source = data / "tokenizer.json"
    tokenizer_sha = hashlib.sha256(tokenizer_source.read_bytes()).hexdigest()
    if checkpoint.get("tokenizer_sha256") and checkpoint["tokenizer_sha256"] != tokenizer_sha:
        raise SystemExit("o tokenizer da pasta de dados não é o do checkpoint")
    shutil.copyfile(tokenizer_source, target / "tokenizer.json")
    config["tokenizer_path"] = str((target / "tokenizer.json").relative_to(ROOT))
    dtype = getattr(torch, args.dtype)
    weights = {key: value.to(dtype) for key, value in checkpoint["state_dict"].items()}

    # Prova de carga: o modelo reconstruído aceita os pesos e produz logits finitos.
    model = build_model(config)
    model.load_state_dict(weights)
    with torch.inference_mode():
        logits = model(torch.tensor([[1, 5, 9, 13]]))
    if not torch.isfinite(logits).all():
        raise SystemExit("logits não finitos após a exportação")

    corpus = {}
    manifest_path = data / "corpus_manifest.json"
    if manifest_path.exists():
        rows = json.loads(manifest_path.read_text(encoding="utf-8"))["sources"]
        corpus = {"sources": [{key: row.get(key) for key in ("name", "language", "license", "train_bytes", "train_docs")}
                              for row in rows],
                  "train_bytes": sum(row.get("train_bytes", 0) for row in rows)}
    meta = {key: value for key, value in checkpoint.items() if key not in {"state_dict", "config"}}
    meta.update({
        "tokenizer_sha256": tokenizer_sha,
        "source": "brasa-cpu-progressive-pretrain",
        "corpus": corpus,
        "reports": [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.report],
        "exported_dtype": args.dtype,
        "parameters": sum(p.numel() for p in model.parameters()),
    })
    config["training_policy"] = {**config.get("training_policy", {}), "method": "progressive-growth-pretrain+sft",
                                 "external_llm": False, "ollama": False}
    output = target / "candidate.safetensors"
    save_checkpoint({**meta, "config": config, "state_dict": weights}, output)
    print(json.dumps({"checkpoint": str(output.relative_to(ROOT)), "parameters": meta["parameters"],
                      "bytes": output.stat().st_size, "tokenizer": config["tokenizer_path"]}, indent=2))


if __name__ == "__main__":
    main()
