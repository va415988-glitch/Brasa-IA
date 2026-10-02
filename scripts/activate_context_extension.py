#!/usr/bin/env python3
"""Ativa uma extensão de contexto somente após o teste real de 32K passar."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="model/godmode/context-32768-v1")
    parser.add_argument("--state", default="model/godmode/state.json")
    args = parser.parse_args()
    directory = Path(args.directory)
    if not directory.is_absolute():
        directory = ROOT / directory
    state_path = Path(args.state)
    if not state_path.is_absolute():
        state_path = ROOT / state_path
    checkpoint = directory / "candidate.safetensors"
    metadata_path = checkpoint.with_suffix(checkpoint.suffix + ".json")
    verification_path = directory / "context_verification.json"
    manifest_path = directory / "context_extension_manifest.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_tokens = int(verification.get("requested_context_tokens") or 0)
    if (verification.get("schema") != "context-window-runtime-verification/v1"
            or not verification.get("target_fully_exercised")
            or not verification.get("output_logits_finite")
            or expected_tokens != 32768
            or verification.get("checkpoint_sha256") != manifest.get("output_sha256")):
        raise SystemExit("verificação real de 32768 tokens ausente, incompleta ou incompatível")

    config = metadata.get("config") or {}
    if int(config.get("context_length") or 0) != expected_tokens:
        raise SystemExit("o tamanho dos pesos posicionais não corresponde ao teste")
    config["runtime_context_verified_tokens"] = expected_tokens
    metadata["config"] = config
    metadata["status"] = "context-extended-runtime-verified"
    metadata["context_verification"] = {
        "report": str(verification_path.relative_to(ROOT)),
        "runtime_context_tokens": expected_tokens,
        "elapsed_seconds": verification.get("elapsed_seconds"),
        "peak_rss_mb": verification.get("peak_rss_mb"),
        "semantic_quality_evaluated": False,
    }
    atomic_json(metadata_path, metadata)

    previous_state = json.loads(state_path.read_text(encoding="utf-8"))
    backup_path = directory / "state_before_activation.json"
    if not backup_path.exists():
        atomic_json(backup_path, previous_state)
    previous_checkpoint = previous_state.get("checkpoint")
    manifest["runtime_verification_pending"] = False
    manifest["runtime_verification"] = {
        "report": str(verification_path.relative_to(ROOT)),
        "target_fully_exercised": True,
        "semantic_quality_evaluated": False,
    }
    manifest["previous_active_checkpoint"] = previous_checkpoint
    atomic_json(manifest_path, manifest)

    new_state = dict(previous_state)
    new_state["status"] = "active"
    new_state["checkpoint"] = str(checkpoint.relative_to(ROOT))
    new_state["updated_at"] = datetime.now(timezone.utc).isoformat()
    new_state["reason"] = "janela de execução 32768 verificada; qualidade semântica longa permanece experimental"
    new_state["previous_checkpoint"] = previous_checkpoint
    atomic_json(state_path, new_state)
    print(json.dumps({
        "activated": new_state["checkpoint"],
        "runtime_context_tokens": expected_tokens,
        "trained_context_tokens": config.get("training_context_length"),
        "previous_checkpoint": previous_checkpoint,
        "semantic_quality_evaluated": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
