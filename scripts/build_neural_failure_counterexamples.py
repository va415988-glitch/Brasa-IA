#!/usr/bin/env python3
"""Build a compact synthetic dataset targeted at the observed raw checkpoint failure modes.

This dataset focuses on the core failure patterns uncovered by the benchmark:
- repeated-words loops
- too-short completion
- weak relevance signal
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "model" / "training" / "neural-failure-counterexamples-v1.jsonl"


def build_dataset() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    good_examples = [
        {
            "failure_mode": "repeated-words",
            "is_good_example": True,
            "prompt": "Explique em uma frase curta, mas completa, o que é uma variável em Python.",
            "answer": "Uma variável é um nome que guarda um valor em memória para que o programa possa reutilizá-lo depois, como um recipiente temporário para dados.",
        },
        {
            "failure_mode": "repeated-words",
            "is_good_example": True,
            "prompt": "Dê três motivos para separar logs de erro e logs de operação em um sistema de produção.",
            "answer": "Separar os logs ajuda a identificar falhas rapidamente, a diferenciar incidentes de atividade normal e a reduzir ruído ao diagnosticar a causa raiz de uma falha.",
        },
        {
            "failure_mode": "too-short",
            "is_good_example": True,
            "prompt": "Descreva como decidir se um problema de performance vem do banco de dados ou da aplicação.",
            "answer": "Primeiro mede-se o tempo de consulta e a carga do banco; depois compara-se com o tempo total da requisição e com o uso de CPU e memória da aplicação. Se o banco demora em consultas repetidas, o gargalo está no armazenamento; se a aplicação faz muitas consultas ou processa demais em memória, o problema está no código.",
        },
        {
            "failure_mode": "too-short",
            "is_good_example": True,
            "prompt": "Qual é a diferença entre uma fonte primária e uma fonte secundária em pesquisa?",
            "answer": "Uma fonte primária registra o fato diretamente, como um documento original, observação ou dado coletado no momento do evento. Uma fonte secundária interpreta, resume ou comenta esse material, como uma revisão acadêmica ou um artigo de análise.",
        },
    ]

    bad_examples = [
        {
            "failure_mode": "repeated-words",
            "is_good_example": False,
            "prompt": "Explique em uma frase curta, mas completa, o que é uma variável em Python.",
            "answer": "valor valor valor valor valor valor valor valor valor valor valor valor valor valor valor.",
        },
        {
            "failure_mode": "repeated-words",
            "is_good_example": False,
            "prompt": "Dê três motivos para separar logs de erro e logs de operação em um sistema de produção.",
            "answer": "erro erro erro erro erro erro erro erro erro erro erro erro erro erro erro.",
        },
        {
            "failure_mode": "too-short",
            "is_good_example": False,
            "prompt": "Descreva como decidir se um problema de performance vem do banco de dados ou da aplicação.",
            "answer": "Mede o tempo.",
        },
        {
            "failure_mode": "too-short",
            "is_good_example": False,
            "prompt": "Qual é a diferença entre uma fonte primária e uma fonte secundária em pesquisa?",
            "answer": "Fonte primária é direta.",
        },
    ]

    for index, row in enumerate(good_examples + bad_examples, start=1):
        rows.append({
            "id": f"neural-failure-{index:02d}",
            "failure_mode": row["failure_mode"],
            "is_good_example": bool(row["is_good_example"]),
            "prompt": row["prompt"],
            "answer": row["answer"],
        })

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
