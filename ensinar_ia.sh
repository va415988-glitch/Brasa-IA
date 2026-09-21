#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "=============================================="
echo "      ENSINO E TREINO DO PLANEJADOR LOCAL"
echo "=============================================="

VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
TRAINER_SCRIPT="$PROJECT_DIR/python/train_planner.py"
CORPUS_DIR="$PROJECT_DIR/corpus/training"
MODEL_DIR="$PROJECT_DIR/model/planner"
OUTPUT_MODEL="$MODEL_DIR/planner_index.json"

mkdir -p "$MODEL_DIR"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "ERRO: ambiente virtual não encontrado em $VENV_PYTHON"
  echo "Antes de treinar, rode: python3 -m venv .venv && .venv/bin/pip install -r python/requirements-cpu.txt"
  exit 1
fi

if [[ ! -f "$TRAINER_SCRIPT" ]]; then
  echo "ERRO: script de treino não encontrado em $TRAINER_SCRIPT"
  exit 1
fi

if [[ ! -d "$CORPUS_DIR" ]]; then
  echo "ERRO: diretório de corpus não encontrado em $CORPUS_DIR"
  exit 1
fi

mapfile -t DATASETS < <(find "$CORPUS_DIR" -maxdepth 1 -type f -name '*.jsonl' -print | sort)

if [[ ${#DATASETS[@]} -eq 0 ]]; then
  echo "ERRO: nenhum dataset JSONL encontrado em $CORPUS_DIR"
  exit 1
fi

VALID_DATASETS=()
for dataset in "${DATASETS[@]}"; do
  name="$(basename "$dataset")"
  case "$name" in
    dataset_sintetico.jsonl|dataset_deepseek_code.jsonl|planner_sft.jsonl|teacher_plans_qwen3_30b.jsonl)
      VALID_DATASETS+=("$dataset")
      ;;
    *)
      # mantém compatibilidade para outros datasets relevantes, mas evita incluir os extras
      # caso o usuário queira controlar explicitamente a entrada por ambiente.
      if [[ -n "${TRAIN_DATASETS:-}" ]]; then
        VALID_DATASETS+=("$dataset")
      fi
      ;;
  esac
done

if [[ ${#VALID_DATASETS[@]} -eq 0 ]]; then
  echo "ERRO: nenhum dataset útil encontrado em $CORPUS_DIR"
  echo "Arquivos existentes:"
  for dataset in "${DATASETS[@]}"; do
    echo "  - $(basename "$dataset")"
  done
  exit 1
fi

ARGS=()
for dataset in "${VALID_DATASETS[@]}"; do
  ARGS+=(--input "$dataset")
done

printf -- "--> Preparando treino com %s dataset(s) de referência:\n" "${#VALID_DATASETS[@]}"
for dataset in "${VALID_DATASETS[@]}"; do
  printf -- "    - %s\n" "$(basename "$dataset")"
done

echo
printf -- "--> Treinando reranker do planejador em %s\n" "$OUTPUT_MODEL"
"$VENV_PYTHON" "$TRAINER_SCRIPT" "${ARGS[@]}" --output "$OUTPUT_MODEL"

STATUS=$?
if [[ $STATUS -ne 0 ]]; then
  echo
  echo "ERRO: treino falhou com código $STATUS"
  exit $STATUS
fi

echo
printf -- "--> Rodando benchmark de inteligência e robustez do modelo\n"
BENCHMARK_SCRIPT="$PROJECT_DIR/tests/benchmark_model_suite.py"
if [[ -f "$BENCHMARK_SCRIPT" ]]; then
  "$VENV_PYTHON" "$BENCHMARK_SCRIPT" --report "$PROJECT_DIR/model/eval_suite_report.json"
  BENCHMARK_STATUS=$?
  if [[ $BENCHMARK_STATUS -ne 0 ]]; then
    echo
    echo "AVISO: o benchmark reportou falhas; isso indica pontos a melhorar no modelo ou no pipeline."
    echo "Status do benchmark: $BENCHMARK_STATUS"
  fi
else
  echo "AVISO: benchmark não encontrado em $BENCHMARK_SCRIPT; pulando verificação pós-treino."
fi

WORKFLOW_BENCHMARK="$PROJECT_DIR/tests/benchmark_workflow_suite.py"
if [[ -f "$WORKFLOW_BENCHMARK" ]]; then
  printf -- "--> Validando o workflow real em conhecimento, ferramentas e latência\n"
  "$VENV_PYTHON" "$WORKFLOW_BENCHMARK" --report "$PROJECT_DIR/model/workflow_gate_report.json"
  WORKFLOW_STATUS=$?
  if [[ $WORKFLOW_STATUS -ne 0 ]]; then
    echo "AVISO: o workflow não passou em todos os casos; o runtime não deve promover respostas experimentais."
  fi
else
  echo "AVISO: gate de workflow não encontrado em $WORKFLOW_BENCHMARK; pulando verificação."
fi

echo
echo "=============================================="
echo "         TREINO DO PLANEJADOR CONCLUÍDO"
echo "=============================================="