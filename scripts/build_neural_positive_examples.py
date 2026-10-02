#!/usr/bin/env python3
"""Build positive examples that teach the model to answer fully and relevantly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "model" / "training" / "neural-positive-examples-v1.jsonl"


def build_dataset() -> list[dict[str, Any]]:
    rows = [
        {
            "id": "positive-01",
            "prompt": "Explique em uma frase curta, mas completa, o que é uma variável em Python.",
            "answer": "Uma variável é um nome que guarda um valor em memória para que o programa possa reutilizá-lo depois, como um recipiente temporário para dados." ,
        },
        {
            "id": "positive-02",
            "prompt": "Dê três motivos para separar logs de erro e logs de operação em um sistema de produção.",
            "answer": "Separar os logs ajuda a identificar falhas rapidamente, a diferenciar incidentes de atividade normal e a reduzir ruído ao diagnosticar a causa raiz de uma falha.",
        },
        {
            "id": "positive-03",
            "prompt": "Descreva como decidir se um problema de performance vem do banco de dados ou da aplicação.",
            "answer": "Primeiro mede-se o tempo de consulta e a carga do banco; depois compara-se com o tempo total da requisição e com o uso de CPU e memória da aplicação. Se o banco demora em consultas repetidas, o gargalo está no armazenamento; se a aplicação faz muitas consultas ou processa demais em memória, o problema está no código.",
        },
        {
            "id": "positive-04",
            "prompt": "Qual é a diferença entre uma fonte primária e uma fonte secundária em pesquisa?",
            "answer": "Uma fonte primária registra o fato diretamente, como um documento original, observação ou dado coletado no momento do evento. Uma fonte secundária interpreta, resume ou comenta esse material, como uma revisão acadêmica ou um artigo de análise.",
        },
        {
            "id": "positive-05",
            "prompt": "O que deve ser verificado antes de declarar um incidente como resolvido?",
            "answer": "Antes de encerrar um incidente, é preciso confirmar que a causa foi identificada, que a correção foi testada em reprodução ou em ambiente controlado, e que a operação voltou a atender a demanda sem regressões visíveis ou métricas críticas comprometidas.",
        },
        {
            "id": "positive-06",
            "prompt": "Como você decidiria entre duas abordagens de arquitetura com custo semelhante?",
            "answer": "A decisão deve comparar a clareza operacional, a manutenção futura, o esforço de mudança e a confiabilidade sob carga. Se uma abordagem reduz etapas manuais e limita risco de erro sem aumentar complexidade de suporte, ela normalmente vence em produção.",
        },
    ]
    return rows


def write_dataset(path: Path = OUTPUT_PATH) -> list[dict[str, Any]]:
    rows = build_dataset()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows


def main() -> None:
    rows = write_dataset()
    print(json.dumps({"written": str(OUTPUT_PATH), "rows": len(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
