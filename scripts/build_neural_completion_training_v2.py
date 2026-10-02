#!/usr/bin/env python3
"""Dataset de correção mais robusto para os padrões que ainda quebram o benchmark local."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "model" / "training" / "neural-completion-training-v2.jsonl"
MESSAGES_PATH = ROOT / "model" / "training" / "neural-completion-training-v2.messages.jsonl"


POSITIVE_ROWS = [
    {
        "id": "positive-v2-01",
        "failure_mode": "good-response",
        "label": "positive",
        "prompt": "Qual é a diferença entre uma variável local e global em Python?",
        "answer": "Uma variável local vive dentro do escopo em que foi criada e normalmente desaparece ao sair da função ou do bloco. Uma variável global fica acessível em mais de um escopo, mas seu uso exige cuidado porque pode ser modificada em vários pontos e tornar o código menos previsível.",
    },
    {
        "id": "positive-v2-02",
        "failure_mode": "good-response",
        "label": "positive",
        "prompt": "Como a regra de ownership evita vazamentos em Rust?",
        "answer": "Ownership define um único dono para cada valor e libera o recurso quando esse dono sai de escopo. Assim, o compilador garante que não haja uso após liberação nem acesso simultâneo inválido, reduzindo bugs comuns de memória sem depender de um coletor de lixo.",
    },
    {
        "id": "positive-v2-03",
        "failure_mode": "good-response",
        "label": "positive",
        "prompt": "Qual é a melhor maneira de validar entradas em uma API REST?",
        "answer": "Valide tipo, tamanho, formato e limites antes de processar a requisição. Em seguida, retorne erros explícitos em status adequados, registre o problema e mantenha o processamento isolado de dados externos para evitar efeitos colaterais ou falhas silenciosas.",
    },
    {
        "id": "positive-v2-04",
        "failure_mode": "good-response",
        "label": "positive",
        "prompt": "Como criar um plano de trabalho para uma campanha criativa?",
        "answer": "Defina a meta, separe as entregas em blocos simples e escolha uma sequência de trabalho com checkpoints curtos. Isso reduz o risco de bloqueio porque cada etapa fica acionável e você pode testar uma ideia menor, revisar e ajustar rapidamente.",
    },
    {
        "id": "positive-v2-05",
        "failure_mode": "good-response",
        "label": "positive",
        "prompt": "Quais são três maneiras de tornar um jogo mais tático sem torná-lo confuso?",
        "answer": "1) Use regras simples e visuais claras para cada ação. 2) Dê ao jogador informação relevante sobre vantagens e riscos em cada turno. 3) Acrescente pequenos choques de estado para criar decisão sem aumentar a complexidade arbitrária do sistema.",
    },
    {
        "id": "positive-v2-06",
        "failure_mode": "good-response",
        "label": "positive",
        "prompt": "Como decidir se um argumento científico é confiável?",
        "answer": "Examine a metodologia, o tamanho e a representatividade da amostra, a transparência da coleta e se há revisão por pares ou reprodução independente. Um argumento forte precisa de evidência observável, limites bem definidos e clareza sobre o que foi realmente demonstrado.",
    },
    {
        "id": "positive-v2-07",
        "failure_mode": "good-response",
        "label": "positive",
        "prompt": "Qual é a diferença entre correlação e causalidade em análise de dados?",
        "answer": "Correlação mostra que duas variáveis se movem juntas; causalidade exige evidência de que uma causa diretamente a mudança na outra. Sem isso, a relação pode ser coincidência, confusão por variável oculta ou efeito de um terceiro fator.",
    },
    {
        "id": "positive-v2-08",
        "failure_mode": "good-response",
        "label": "positive",
        "prompt": "Como priorizar opções quando uma decisão depende de dados incompletos?",
        "answer": "Liste os riscos, compare o custo de cada erro e escolha a opção com maior reversibilidade. Quando os dados faltam, vale validar hipóteses em etapas, acompanhar indicadores e reavaliar cedo em vez de decidir com excesso de confiança.",
    },
    {
        "id": "positive-v2-09",
        "failure_mode": "good-response",
        "label": "positive",
        "prompt": "Como distinguir uma fonte original de uma revisão crítica?",
        "answer": "Uma fonte original apresenta o relato, dado ou material primário diretamente; uma revisão crítica interpreta esse material e o contextualiza. Se a fonte explicita que está resumindo, analisando ou comparando outras fontes, ela é normalmente secundária ou analítica.",
    },
    {
        "id": "positive-v2-10",
        "failure_mode": "good-response",
        "label": "positive",
        "prompt": "Como escrever uma função Python com poucos efeitos colaterais?",
        "answer": "Mantenha a entrada e a saída explícitas, evite mutar estado global e deixe claro o que a função recebe e retorna. Quando a lógica é separada do restante do sistema, a função fica mais previsível, mais fácil de testar e menos sujeita a erros por estado compartilhado.",
    },
]

NEGATIVE_ROWS = [
    {
        "id": "negative-v2-01",
        "failure_mode": "too-short",
        "label": "negative",
        "prompt": "Quando um endpoint deve usar erro 500 em vez de 400?",
        "answer": "500 resolve.",
    },
    {
        "id": "negative-v2-02",
        "failure_mode": "repeated-fragment",
        "label": "negative",
        "prompt": "Por que o status 429 aparece em integrações com fila?",
        "answer": "429 429 429 429 429 429 429 429 429 429 429 429.",
    },
    {
        "id": "negative-v2-03",
        "failure_mode": "prompt-leak",
        "label": "negative",
        "prompt": "Como manter a criatividade sem perder clareza de escopo?",
        "answer": "Como manter a criatividade sem perder clareza de escopo? Como manter a criatividade sem perder clareza de escopo? Como manter a criatividade sem perder clareza de escopo?",
    },
    {
        "id": "negative-v2-04",
        "failure_mode": "prompt-leak",
        "label": "negative",
        "prompt": "Quando usar clone em Rust em vez de mover um valor?",
        "answer": "<|user|>\nQuando usar clone em Rust em vez de mover um valor?\n<|assistant|>\nUse clone quando precisar duplicar um valor sem transferir a posse.",
    },
    {
        "id": "negative-v2-05",
        "failure_mode": "repeated-character",
        "label": "negative",
        "prompt": "Quais padrões funcionam para um jogo de furtividade?",
        "answer": "aaaaaa aaaaaa aaaaaa aaaaaa aaaaaa aaaaaa",
    },
    {
        "id": "negative-v2-06",
        "failure_mode": "not-relevant",
        "label": "negative",
        "prompt": "Como verificar se um estudo usa amostra representativa?",
        "answer": "Mesa azul, cachorro, telefone, lua, vento, sonho, café, capacitor, borda, espelho.",
    },
    {
        "id": "negative-v2-07",
        "failure_mode": "not-relevant",
        "label": "negative",
        "prompt": "Qual estratégia ajuda em decisões com risco moderado?",
        "answer": "Papel, giz, relógio, pedra, futuro, pão, música, barco, dente, sombra.",
    },
    {
        "id": "negative-v2-08",
        "failure_mode": "repeated-words",
        "label": "negative",
        "prompt": "Como separar uma fonte primária de um resumo bem escrito?",
        "answer": "Fonte fonte fonte fonte fonte fonte fonte primária primária primária primária.",
    },
]


def build_dataset() -> list[dict[str, Any]]:
    return POSITIVE_ROWS + NEGATIVE_ROWS


def write_dataset() -> tuple[Path, list[dict[str, Any]]]:
    rows = build_dataset()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    MESSAGES_PATH.write_text(
        "".join(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": row["prompt"]},
                        {"role": "assistant", "content": row["answer"]},
                    ],
                    "source": str(OUTPUT_PATH.relative_to(ROOT)),
                    "label": row["label"],
                    "failure_mode": row["failure_mode"],
                },
                ensure_ascii=False,
            ) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    return OUTPUT_PATH, rows


def main() -> int:
    path, rows = write_dataset()
    print(json.dumps({"written": str(path), "rows": len(rows), "positive": sum(1 for row in rows if row["label"] == "positive"), "negative": sum(1 for row in rows if row["label"] == "negative")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
