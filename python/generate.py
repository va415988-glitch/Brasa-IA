"""Executa inferência local a partir de um checkpoint próprio."""

import argparse
import json

import torch

from model import build_model
from tokenizer import ByteBPETokenizer
from checkpoint_io import load_checkpoint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", default="model/checkpoints/compact-01.pt")
    parser.add_argument("--tokenizer", default="model/tokenizer.json")
    parser.add_argument("--prompt", default="<|user|>\nOlá, quem é você?\n<|assistant|>\n")
    parser.add_argument("--tokens", type=int, default=32)
    args = parser.parse_args()

    checkpoint = load_checkpoint(args.checkpoint)
    config = checkpoint["config"]
    model = build_model(config)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    tokenizer = ByteBPETokenizer.load(args.tokenizer)
    generated = tokenizer.encode(args.prompt)

    with torch.no_grad():
        for _ in range(args.tokens):
            context = generated[-config["context_length"] :]
            logits = model(torch.tensor([context]))[0, -1]
            next_token = int(torch.argmax(logits).item())
            generated.append(next_token)
            if next_token == tokenizer.special_tokens["<eos>"]:
                break

    print(tokenizer.decode(generated))
    print(json.dumps({"input_tokens": len(tokenizer.encode(args.prompt)), "output_tokens": len(generated), "checkpoint_steps": checkpoint["steps"]}))


if __name__ == "__main__":
    main()
