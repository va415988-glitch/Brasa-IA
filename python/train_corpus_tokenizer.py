"""Treina um tokenizer candidato usando conhecimento e comportamento misturados."""

import argparse
import json
from pathlib import Path

from tokenizer import ByteBPETokenizer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="model/train_corpus.jsonl")
    parser.add_argument("--output", default="model/tokenizer-mixed.json")
    parser.add_argument("--vocab-size", type=int, default=1024)
    parser.add_argument("--min-frequency", type=int, default=2)
    args = parser.parse_args()
    records = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    texts = [record.get("text", "") for record in records if record.get("text")]
    tokenizer = ByteBPETokenizer.train(texts, vocab_size=args.vocab_size, min_frequency=args.min_frequency)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tokenizer.save(output)
    print(f"textos usados: {len(texts)}")
    print(f"vocabulário: {len(tokenizer.vocab) + len(tokenizer.special_tokens)} tokens")
    print(f"arquivo: {output}")


if __name__ == "__main__":
    main()
