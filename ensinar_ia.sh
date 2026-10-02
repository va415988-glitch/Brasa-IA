#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
PIPELINE="$PROJECT_DIR/python/agent_learning_pipeline.py"
LOCK_DIR="${XDG_RUNTIME_DIR:-/tmp}"
if [[ ! -d "$LOCK_DIR" || ! -w "$LOCK_DIR" ]]; then
  LOCK_DIR="/tmp"
fi
LOCK_FILE="$LOCK_DIR/ia-local-learning-pipeline.lock"

usage() {
  cat <<'EOF'
Uso:
  ./ensinar_ia.sh                 Executa uma rodada segura do pipeline
  ./ensinar_ia.sh preflight       Verifica ambiente e entradas sem escrever
  ./ensinar_ia.sh report          Audita traces e datasets sem treinar
  ./ensinar_ia.sh diagnose        Executa a bateria neural e de requisitos
  ./ensinar_ia.sh godmode         Executa 100 verificações e controla God Mode
  ./ensinar_ia.sh run             Gera candidato e executa as baterias
  ./ensinar_ia.sh run --promote  Promove somente com todos os gates aprovados

Opções úteis do pipeline:
  --skip-batteries  Executa apenas para diagnóstico; nunca permite promoção
  --dry-run         Faz o pré-voo e não cria uma rodada
EOF
}

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERRO: ambiente virtual não encontrado em $PYTHON_BIN" >&2
  echo "Antes de executar, rode: python3 -m venv .venv && .venv/bin/pip install -r python/requirements-cpu.txt" >&2
  exit 1
fi

if [[ ! -f "$PIPELINE" ]]; then
  echo "ERRO: pipeline não encontrado em $PIPELINE" >&2
  exit 1
fi

cd "$PROJECT_DIR"

if [[ "${1:-}" == "godmode" ]]; then
  shift
  exec "$PYTHON_BIN" scripts/godmode.py "$@"
fi

run_diagnostics() {
  local checkpoint="${IA_LOCAL_CHECKPOINT:-model/checkpoints/compact-08-gate-focus.pt}"
  local report_dir="${IA_LOCAL_DIAGNOSTIC_DIR:-model/diagnostics}"
  local failure=0

  echo "[diagnose] checkpoint: ${checkpoint}"
  "$PYTHON_BIN" tests/benchmark_neural_generation.py \
    --checkpoint "$checkpoint" \
    --report "${report_dir}/neural_generation.json" || failure=1
  "$PYTHON_BIN" tests/benchmark_requirements.py \
    --checkpoint "$checkpoint" \
    --report "${report_dir}/requirements.json" || failure=1
  "$PYTHON_BIN" tests/benchmark_model_suite.py \
    --checkpoint "$checkpoint" \
    --report "${report_dir}/model_suite.json" || failure=1
  "$PYTHON_BIN" tests/benchmark_workflow_suite.py \
    --checkpoint "$checkpoint" \
    --report "${report_dir}/workflow_suite.json" || failure=1

  if [[ "$failure" -ne 0 ]]; then
    echo "[diagnose] uma ou mais baterias falharam; consulte ${report_dir}/" >&2
    return 1
  fi
  echo "[diagnose] todas as baterias passaram"
}

if [[ "${1:-}" == "diagnose" ]]; then
  shift
  if [[ $# -gt 0 ]]; then
    echo "ERRO: diagnose não aceita opções posicionais; use IA_LOCAL_CHECKPOINT e IA_LOCAL_DIAGNOSTIC_DIR." >&2
    exit 2
  fi
  run_diagnostics
  exit $?
fi

# Sem argumentos, executa uma rodada segura padrão.
if [[ $# -eq 0 ]]; then
  set -- run
elif [[ "$1" == "--promote" ]]; then
  shift
  set -- run --promote "$@"
fi

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  exit 0
fi

if command -v flock >/dev/null 2>&1; then
  if ! exec 9>"$LOCK_FILE"; then
    echo "AVISO: não foi possível criar o lock; continuando sem proteção de concorrência." >&2
  elif ! flock -n 9; then
    echo "ERRO: já existe outra execução do pipeline em andamento." >&2
    echo "A execução que detém o lock precisa terminar antes de outra rodada." >&2
    exit 75
  fi
fi

exec "$PYTHON_BIN" "$PIPELINE" "$@"
