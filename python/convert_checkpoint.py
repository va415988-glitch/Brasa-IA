"""Converte um checkpoint próprio .pt para Safetensors."""

import argparse
from pathlib import Path

from checkpoint_io import load_checkpoint, save_checkpoint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    checkpoint = load_checkpoint(Path(args.input))
    checkpoint["source"] = str(args.input)
    save_checkpoint(checkpoint, Path(args.output))
    print(f"checkpoint convertido: {args.output}")


if __name__ == "__main__":
    main()
