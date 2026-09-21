"""Audita em lote o catálogo Hugging Face, sem baixar conteúdo."""

import argparse
import json
from pathlib import Path

from ingest_hf_dataset import audit_dataset, ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="config/huggingface_catalog.json")
    parser.add_argument("--output", default="corpus/quarantine/huggingface/catalog-audit.json")
    args = parser.parse_args()
    catalog = json.loads((ROOT / args.catalog).read_text(encoding="utf-8"))
    reports = []
    for entry in catalog["datasets"]:
        try:
            reports.append(audit_dataset(entry["id"]))
        except Exception as error:
            reports.append({"dataset": entry["id"], "status": "audit_failed", "error": str(error), "training_eligible": False})
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"schema": "huggingface-catalog-audit/v1", "reports": reports}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"datasets": len(reports), "output": str(output.relative_to(ROOT)), "training_eligible": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
