#!/usr/bin/env python3
"""Prepara tokenizer e configuração do candidato neural profissional local.

O artefato é criado a partir dos corpora locais, sem modelo externo. A janela
de produção é 16.384 tokens; o treino usa janelas menores explicitamente
registradas para caber no computador e não confunde esse atalho com a janela
de inferência.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from tokenizer import ByteBPETokenizer


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "python" / "data"
OUTPUT = ROOT / "model" / "godmode"


def rows() -> list[dict]:
    names = [
        "combined.jsonl",
        "behavior_expanded.jsonl",
        "behavior_phase1.jsonl",
        "curriculum_apex_v1.jsonl",
        "senior_creative_v1.jsonl",
        "agentic_curriculum_v1.jsonl",
        "open_programming_curriculum_v1.jsonl",
        "agent_harness_curriculum_v1.jsonl",
        "hf_datasets_curriculum_v1.jsonl",
        "deep_learning_book_curriculum_v1.jsonl",
        "databricks_genai_curriculum_v1.jsonl",
        "little_book_deep_learning_curriculum_v1.jsonl",
        "godmode_knowledge_v1.jsonl",
        "godmode_procedures_v1.jsonl",
        "neural_professional_v1.jsonl",
    ]
    result = []
    for name in names:
        path = DATA / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                result.append(json.loads(line))
    return result


def expand_tokenizer(target_size: int) -> ByteBPETokenizer:
    """Preserva os IDs atuais e reserva IDs novos sem BPE custoso.

    O corpus atual foi treinado com estes merges. Reaprendê-los do zero para
    preencher cada slot mudaria a segmentação de todo o projeto; os slots
    reservados podem ser aprendidos em uma rodada posterior sem invalidar o
    checkpoint atual.
    """
    source = ROOT / "model" / "tokenizer.json"
    tokenizer = ByteBPETokenizer.load(source)
    next_id = max(tokenizer.vocab.values(), default=-1) + 1
    while next_id < target_size:
        raw = ("<unused-godmode-" + str(next_id) + ">").encode("ascii")
        tokenizer.vocab[raw.hex()] = next_id
        next_id += 1
    return tokenizer


def main() -> None:
    records = rows()
    tokenizer = expand_tokenizer(8192)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    tokenizer_path = OUTPUT / "godmode-tokenizer-v1.json"
    tokenizer.save(tokenizer_path)
    config = {
        "name": "ia-local-zero-godmode-v1",
        "architecture": "decoder_transformer",
        "vocab_size": 8192,
        "context_length": 16384,
        "generation_length": 4096,
        "training_context_length": 512,
        "layers": 2,
        "hidden_size": 128,
        "attention_heads": 4,
        "feed_forward_multiplier": 4,
        "language_priority": ["pt-BR", "code", "en"],
        "tool_call_format": "json",
        "max_request_seconds": 60,
        "runtime": "python-local",
        "tokenizer_path": str(tokenizer_path),
        "training_policy": {
            "method": "local-sft-from-scratch",
            "external_llm": False,
            "ollama": False,
            "gpu_required": False,
            "training_context": 512,
            "production_context": 16384,
        },
    }
    config_path = OUTPUT / "godmode-config-v1.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "records": len(records),
        "texts": "preserved-base-tokenizer-plus-reserved-slots",
        "tokenizer": str(tokenizer_path),
        "tokenizer_tokens": len(tokenizer.vocab) + len(tokenizer.special_tokens),
        "max_token_id": max(tokenizer.vocab.values()),
        "config": str(config_path),
        "context_length": config["context_length"],
        "training_context_length": config["training_context_length"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
