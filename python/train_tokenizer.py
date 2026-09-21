"""Treina o primeiro tokenizer próprio usando os traces do projeto."""

import json
from pathlib import Path

from tokenizer import ByteBPETokenizer

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "data" / "combined.jsonl"
OUTPUT = ROOT.parent / "model" / "tokenizer.json"


def collect_texts():
    texts = []
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        trace = json.loads(line)
        texts.append(json.dumps(trace, ensure_ascii=False, separators=(",", ":")))
        for message in trace["messages"]:
            content = message.get("content", "")
            texts.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, separators=(",", ":")))
    return texts


def main():
    texts = collect_texts()
    tokenizer = ByteBPETokenizer.train(texts, vocab_size=1024, min_frequency=2)
    tokenizer.save(OUTPUT)
    sample = "Pesquise a versão atual do Rust e cite as fontes."
    encoded = tokenizer.encode(sample, add_bos=True, add_eos=True)
    print(f"textos usados: {len(texts)}")
    print(f"vocabulário: {len(tokenizer.vocab) + len(tokenizer.special_tokens)} tokens")
    print(f"amostra: {len(encoded)} tokens")
    print(f"round-trip: {tokenizer.decode(encoded)}")
    print(f"arquivo: {OUTPUT}")


if __name__ == "__main__":
    main()
