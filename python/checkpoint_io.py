"""Leitura e conversão de checkpoints locais sem depender de pickle."""

from __future__ import annotations

import json
from pathlib import Path

import torch
from safetensors.torch import load_file, save_file


def load_checkpoint(path: str | Path) -> dict:
    path = Path(path)
    if path.suffix != ".safetensors":
        return torch.load(path, map_location="cpu", weights_only=False)
    tensors = load_file(str(path), device="cpu")
    metadata_path = path.with_suffix(path.suffix + ".json")
    if not metadata_path.exists():
        raise ValueError(f"metadados ausentes para o checkpoint: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return {"config": metadata["config"], "state_dict": tensors, **{key: value for key, value in metadata.items() if key != "config"}}


def save_checkpoint(checkpoint: dict, path: str | Path) -> None:
    path = Path(path)
    # O modelo usa weight tying entre embedding e lm_head; clone remove o
    # alias antes de serializar e mantém as duas chaves explícitas.
    tensors = {key: value.detach().cpu().contiguous().clone() for key, value in checkpoint["state_dict"].items()}
    save_file(tensors, str(path), metadata={"format": "safetensors", "source": str(checkpoint.get("source", "local"))})
    metadata = {key: value for key, value in checkpoint.items() if key != "state_dict"}
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
