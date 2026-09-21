"""Converte traces JSONL em um fluxo de tokens para treinamento causal."""

import argparse
import json
import struct
from pathlib import Path

from tokenizer import ByteBPETokenizer


def format_trace(trace):
    messages = trace.get("messages")
    if not isinstance(messages, list):
        text = trace.get("text")
        if isinstance(text, str) and text.strip():
            category = str(trace.get("category") or "conhecimento")
            messages = [
                {"role": "user", "content": f"Explique este conteúdo de {category}."},
                {"role": "assistant", "content": text},
            ]
        else:
            raise ValueError("registro sem messages ou text utilizável")
    parts = []
    for message in messages:
        role = message["role"]
        parts.append(f"<|{role}|>\n")
        if "tool_call" in message:
            parts.append(json.dumps(message["tool_call"], ensure_ascii=False, separators=(",", ":")))
        else:
            content = message.get("content", "")
            parts.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, separators=(",", ":")))
        parts.append("\n")
    return "".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", action="append", default=None, help="dataset JSONL; pode ser repetido")
    parser.add_argument("--tokenizer", default="model/tokenizer.json")
    parser.add_argument("--output", default="model/train_tokens.bin")
    parser.add_argument("--repeat", type=int, default=16, help="repete o corpus apenas para smoke test")
    parser.add_argument("--max-records", type=int, default=0, help="limita registros por dataset para experimentos controlados")
    args = parser.parse_args()

    tokenizer = ByteBPETokenizer.load(args.tokenizer)
    tokens = []
    datasets = [Path(item) for item in args.dataset] if args.dataset else [
        Path("python/data/combined.jsonl"),
        Path("python/data/curriculum_apex_v1.jsonl"),
    ]
    traces = []
    for dataset in datasets:
        loaded = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
        traces.extend(loaded[:args.max_records] if args.max_records else loaded)
    for _ in range(max(1, args.repeat)):
        for trace in traces:
            tokens.extend(tokenizer.encode(format_trace(trace), add_bos=True, add_eos=True))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as file:
        file.write(struct.pack(f"<{len(tokens)}I", *tokens))
    print(f"tokens gravados: {len(tokens)}")
    print(f"arquivo: {output}")


if __name__ == "__main__":
    main()
