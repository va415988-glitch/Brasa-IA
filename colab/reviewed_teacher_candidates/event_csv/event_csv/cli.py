"""Command line interface for JSONL-to-CSV conversion."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
import sys

from .validator import process_events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate JSONL events and write a timestamp-sorted CSV.")
    parser.add_argument("--input", required=True, help="Input JSONL path")
    parser.add_argument("--output", required=True, help="Output CSV path")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_path = Path(args.output)
    try:
        with input_path.open("r", encoding="utf-8") as stream:
            lines = stream.readlines()
        events, errors = process_events(lines)
        with output_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["id", "timestamp"], extrasaction="ignore")
            writer.writeheader()
            writer.writerows(events)
    except OSError as error:
        print(f"event-csv: {error}", file=sys.stderr)
        return 2

    print(f"Eventos gravados: {len(events)}")
    print(f"Linhas descartadas: {len(errors)}")
    for error_type, count in sorted(Counter(item["error"] for item in errors).items()):
        print(f"  {error_type}: {count}")
    for item in errors:
        print(f"  linha {item['line']}: {item['error']}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
