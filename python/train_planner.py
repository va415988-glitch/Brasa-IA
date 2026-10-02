"""Treina um índice leve de seleção de ferramentas a partir de traces.

É um componente especializado, deliberadamente pequeno: aprende vocabulário
por ferramenta e funciona como reranker do planejador contratual. Não tenta
substituir o modelo conversacional nem executa ferramentas.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from dialogue import normalize


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "model" / "planner" / "planner_index.json"


def _features(text: str) -> list[str]:
    normalized = normalize(text)
    words = re.findall(r"[\wÀ-ÿ]{2,}", normalized)
    compact = re.sub(r"\s+", " ", normalized)
    grams = [compact[index:index + 4] for index in range(max(0, len(compact) - 3))]
    return words + [f"#4:{gram}" for gram in grams if " " not in gram]


def _rows(path: Path):
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue

        metadata = row.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        review_status = metadata.get("review_status", row.get("review_status"))
        requires_review = bool(metadata.get("requires_human_review", row.get("requires_human_review", False)))
        # Raw workflow candidates are never training data until a person has
        # reviewed the trajectory and explicitly marked it approved.
        if review_status not in {None, "approved"} or (requires_review and review_status != "approved"):
            continue
        
        # 1. Suporte a Datasets de Instrução/Conhecimento Sintético (ex: dataset_sintetico.jsonl)
        question = row.get("instruction") or row.get("prompt") or row.get("user")
        if question and (row.get("output") or row.get("response")):
            tool_name = row.get("tool") or "knowledge_base"
            yield str(question), str(tool_name), row.get("source", path.name)
            continue

        # 2. Suporte a Traces Estruturados de Ferramentas
        messages = row.get("messages") or []
        question = next((item.get("content", "") for item in messages if item.get("role") == "user"), "")
        call = next((item.get("tool_call") for item in messages if item.get("role") == "assistant" and item.get("tool_call")), None)
        if call and call.get("name") and question:
            yield question, str(call["name"]), metadata.get("source", path.name)
            continue
        
        # 3. Aceita os traces legados usados no corpus inicial
        for item in messages:
            legacy = item.get("tool_call") if item.get("role") == "assistant" else None
            if legacy and legacy.get("name") and question:
                yield question, str(legacy["name"]), row.get("derived_from", path.name)
                break


def train(inputs: list[Path], output: Path) -> dict[str, int]:
    examples = []
    by_tool: dict[str, Counter[str]] = defaultdict(Counter)
    document_frequency: Counter[str] = Counter()
    for path in inputs:
        for question, tool, source in _rows(path) or ():
            features = Counter(_features(question))
            examples.append({"tool": tool, "source": source, "question": question[:400]})
            by_tool[tool].update(features)
            document_frequency.update(features.keys())

    artifact = {
        "schema": "planner-index/v1",
        "method": "supervised-feature-reranker",
        "examples": examples,
        "tools": {
            tool: {"features": dict(counts), "examples": sum(1 for item in examples if item["tool"] == tool)}
            for tool, counts in sorted(by_tool.items())
        },
        "document_frequency": dict(document_frequency),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"examples": len(examples), "tools": len(by_tool), "features": len(document_frequency)}


def main():
    parser = argparse.ArgumentParser(description="Treina o reranker local de ferramentas")
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    inputs = [Path(item) for item in args.input] or [
        ROOT / "corpus" / "training" / "planner_sft.jsonl",
        ROOT / "python" / "data" / "tool_traces.jsonl",
        ROOT / "python" / "data" / "behavior_augmented_v2.jsonl",
    ]
    print(json.dumps(train(inputs, Path(args.output)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
