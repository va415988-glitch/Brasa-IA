"""Teste de margem da estratégia de contexto sem alocar o modelo."""

import argparse
import json
from context_strategy import choose_strategy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory-mb", type=int, default=None)
    args = parser.parse_args()
    memory = args.memory_mb
    if memory is None:
        try:
            memory = int(next(line.split()[1] for line in open('/proc/meminfo') if line.startswith('MemAvailable:'))) // 1024
        except (OSError, StopIteration, ValueError):
            memory = None
    rows = []
    for context in (8192, 16384, 32768, 65536):
        result = choose_strategy(memory, context)
        result["context"] = context
        result["headroom"] = None if memory is None else round(memory / max(result["estimated_peak_mb"], 1), 2)
        rows.append(result)
    print(json.dumps({"schema": "context-scaling-test/v1", "available_memory_mb": memory, "scenarios": rows}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
