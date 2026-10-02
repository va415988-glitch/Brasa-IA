#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERRO: ambiente virtual não encontrado em $PYTHON_BIN" >&2
  exit 1
fi
exec "$PYTHON_BIN" "$PROJECT_DIR/scripts/checkpoint_stabilization.py" "$@"
