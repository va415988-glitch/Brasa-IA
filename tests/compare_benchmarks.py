#!/usr/bin/env python3
"""Compara dois ou mais relatórios de benchmark para ranking de modelos."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.model_assessment import compare_reports


def main():
    parser = argparse.ArgumentParser(description="Compara benchmarks de checkpoints")
    parser.add_argument("--report", nargs="+", required=True, help="Lista de arquivos JSON de relatório")
    parser.add_argument("--label", nargs="*", default=[], help="Etiquetas opcionais para cada relatório")
    parser.add_argument("--output", type=Path, default=Path("model/benchmark_ranking.json"), help="Arquivo de saída em JSON")
    args = parser.parse_args()

    labels = args.label or None
    ranking = compare_reports(args.report, labels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"ranking": ranking}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ranking": ranking}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
