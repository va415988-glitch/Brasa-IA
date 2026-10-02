"""Resolve the checkpoint used by start.sh and evaluation commands."""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FALLBACK = Path("model/checkpoints/compact-08-gate-focus.pt")


def selected_checkpoint(root: Path = PROJECT_ROOT, environment: dict[str, str] | None = None) -> Path:
    root = Path(root)
    environment = os.environ if environment is None else environment
    override = environment.get("IA_LOCAL_CHECKPOINT", "").strip()
    if override:
        path = Path(override)
        return path if path.is_absolute() else root / path
    try:
        state = json.loads((root / "model/godmode/state.json").read_text(encoding="utf-8"))
        candidate = Path(str(state.get("checkpoint") or ""))
        if state.get("status") == "active" and str(candidate) != ".":
            resolved = candidate if candidate.is_absolute() else root / candidate
            if resolved.is_file():
                return resolved
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return root / FALLBACK
