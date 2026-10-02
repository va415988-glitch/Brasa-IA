#!/usr/bin/env python3
"""Cria um checkpoint com janela posicional estendida sem reescrever o treino.

As posições aprendidas são interpoladas para o novo tamanho. O manifesto
preserva o contexto realmente usado no treino e deixa explícito que esta
operação amplia a execução, mas não substitui fine-tuning longo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from checkpoint_io import load_checkpoint, save_checkpoint
from model import extend_position_embeddings


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="model/godmode/training-neural-v1/candidate.safetensors",
    )
    parser.add_argument("--output-dir", default="model/godmode/context-32768-v1")
    parser.add_argument("--context", type=int, default=32768)
    args = parser.parse_args()

    source = Path(args.source)
    output_dir = Path(args.output_dir)
    if not source.is_absolute():
        source = ROOT / source
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    if args.context < 8192:
        parser.error("--context precisa ser pelo menos 8192 tokens")
    if output_dir.exists():
        parser.error(f"diretório de saída já existe: {output_dir}")

    checkpoint = load_checkpoint(source)
    config = dict(checkpoint["config"])
    position = checkpoint["state_dict"].get("position_embedding.weight")
    if position is None:
        raise ValueError("checkpoint não contém position_embedding.weight")
    source_native = int(config["context_length"])
    trained_context = int(config.get("training_context_length") or source_native)
    target = int(args.context)
    if target < source_native:
        raise ValueError("a extensão não reduz o contexto nativo")
    if trained_context > position.shape[0]:
        raise ValueError("training_context_length excede a tabela posicional do checkpoint")

    extension = {
        "method": "trained-prefix-preserved-v1",
        "source_checkpoint": str(source.relative_to(ROOT) if source.is_relative_to(ROOT) else source),
        "source_sha256": sha256(source),
        "source_native_context_tokens": source_native,
        "source_trained_context_tokens": trained_context,
        "target_context_tokens": target,
        "fine_tuned_at_target": False,
        "claim": "execução ampliada; não é evidência de treino semântico no contexto alvo",
    }

    state_dict = dict(checkpoint["state_dict"])
    state_dict["position_embedding.weight"] = extend_position_embeddings(
        position, target_context=target, source_context=trained_context,
    )
    config["context_length"] = target
    config["runtime_context_tokens"] = target
    config["training_context_length"] = trained_context
    config["context_extension"] = extension
    updated = {
        **checkpoint,
        "config": config,
        "state_dict": state_dict,
        "source": str(source.relative_to(ROOT) if source.is_relative_to(ROOT) else source),
        "status": "context-extended-experimental",
        "context_extension": extension,
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    candidate = output_dir / "candidate.safetensors"
    save_checkpoint(updated, candidate)
    manifest = {
        "schema": "context-extension-manifest/v1",
        "source": extension,
        "output_checkpoint": str(candidate.relative_to(ROOT)),
        "output_sha256": sha256(candidate),
        "output_position_rows": int(state_dict["position_embedding.weight"].shape[0]),
        "training_provenance_preserved": True,
        "runtime_verification_pending": True,
    }
    (output_dir / "context_extension_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
