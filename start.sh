#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if ! command -v cargo >/dev/null 2>&1; then
  echo "Erro: cargo não foi encontrado. Instale o Rust antes de iniciar o projeto." >&2
  exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Erro: .venv/bin/python não foi encontrado. Crie o ambiente Python do projeto." >&2
  exit 1
fi

if ! command -v fuser >/dev/null 2>&1; then
  echo "Erro: fuser não foi encontrado; não posso limpar as portas com segurança." >&2
  exit 1
fi

free_port() {
  local port="$1"
  local pids
  pids="$(fuser -n tcp "$port" 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i ~ /^[0-9]+$/) print $i}' || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi
  echo "Encerrando processo(s) na porta ${port}: ${pids//$'\n'/ }"
  while read -r pid; do
    [[ -z "$pid" ]] || kill -TERM "$pid" 2>/dev/null || true
  done <<< "$pids"
  sleep 0.4
  pids="$(fuser -n tcp "$port" 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i ~ /^[0-9]+$/) print $i}' || true)"
  while read -r pid; do
    [[ -z "$pid" ]] || kill -KILL "$pid" 2>/dev/null || true
  done <<< "$pids"
}

echo "Compilando o runtime Rust..."
cargo build --manifest-path runtime/Cargo.toml --bin local_ai_runtime

# Uma falha de compilação preserva a instância que já estiver funcionando.
free_port 3000
free_port 3101

RUNTIME="${PROJECT_DIR}/runtime/target/debug/local_ai_runtime"
echo "Iniciando IA Local do Zero em http://127.0.0.1:3000"
echo "Checkpoint próprio: ${IA_LOCAL_CHECKPOINT:-model/checkpoints/compact-08-gate-focus.pt} · política de contexto: mínimo 8192 / alvo 16384 tokens"
echo "Pressione Ctrl+C para encerrar o runtime e o worker do modelo."

setsid "$RUNTIME" --web &
RUNTIME_PID=$!

cleanup() {
  trap - INT TERM EXIT
  kill -TERM -- "-${RUNTIME_PID}" 2>/dev/null || true
  wait "$RUNTIME_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

wait "$RUNTIME_PID"
