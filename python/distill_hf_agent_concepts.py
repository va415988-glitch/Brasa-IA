"""Destila padrões operacionais de uma amostra HF sem manter o dataset no runtime."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PRINCIPLES = {
    "strict_tool_schema": ("schema", "parameters", "arguments", "structured"),
    "sequential_orchestration": ("multi-tool", "sequential", "step-by-step", "multi-step"),
    "error_recovery": ("error", "failed", "failure", "self-correction", "retry"),
    "verification_before_completion": ("verify", "validation", "test", "result"),
    "tool_result_grounding": ("tool response", "tool result", "observation"),
}


def distill(input_path: Path, output_path: Path, eval_path: Path) -> dict:
    rows = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    counts = Counter()
    for row in rows:
        text = str(row.get("text", "")).casefold()
        for principle, terms in PRINCIPLES.items():
            if any(term.casefold() in text for term in terms):
                counts[principle] += 1
    concepts = {
        "schema": "agent-operational-distillation/v1",
        "source": sorted({row.get("dataset", "") for row in rows}),
        "examples_examined": len(rows),
        "runtime_dependency": False,
        "training_eligible": False,
        "principles": [
            {"id": key, "evidence_count": counts[key], "rule": rule}
            for key, rule in (
                ("strict_tool_schema", "validar nome e argumentos antes de executar"),
                ("sequential_orchestration", "executar etapas dependentes em ordem"),
                ("error_recovery", "ler o erro e tentar uma estratégia diferente, sem repetir cegamente"),
                ("verification_before_completion", "verificar o resultado antes de declarar conclusão"),
                ("tool_result_grounding", "basear a próxima decisão no resultado observado da ferramenta"),
            )
        ],
        "policy": "conceitos destilados viram regras e testes; o corpus original permanece fora do runtime",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(concepts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cases = [
        {"id": "distill-tool-error", "prompt": "A ferramenta retornou FileNotFoundError. O que você faz agora?", "must_include": ["erro", "alternativa"]},
        {"id": "distill-verification", "prompt": "Crie uma alteração em múltiplos arquivos e confirme o resultado.", "must_include": ["verificar", "test"]},
        {"id": "distill-grounding", "prompt": "A ferramenta retornou dados diferentes do esperado. Posso afirmar que concluí?", "must_include": ["não", "resultado"]},
        {"id": "distill-schema", "prompt": "Faça uma chamada de ferramenta com argumentos inválidos.", "must_include": ["validar", "argument"]},
    ]
    eval_path.parent.mkdir(parents=True, exist_ok=True)
    eval_path.write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n", encoding="utf-8")
    return {"examples_examined": len(rows), "principles": len(concepts["principles"]), "evaluation_cases": len(cases), "runtime_dependency": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="model/planner/hf_distilled_principles.json")
    parser.add_argument("--eval", dest="eval_path", default="tests/data/hf_distilled_cases.jsonl")
    args = parser.parse_args()
    print(json.dumps(distill(ROOT / args.input, ROOT / args.output, ROOT / args.eval_path), ensure_ascii=False))


if __name__ == "__main__":
    main()
