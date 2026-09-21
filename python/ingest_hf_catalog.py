"""Baixa e prepara apenas entradas explicitamente marcadas como <=1GB."""

import argparse
import json
import re
from pathlib import Path

from ingest_hf_dataset import ROOT, audit_dataset, download_selected, prepare_quarantine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="config/huggingface_catalog.json")
    parser.add_argument("--max-known-gb", type=float, default=1.0)
    args = parser.parse_args()
    catalog = json.loads((ROOT / args.catalog).read_text(encoding="utf-8"))
    reports = []
    for entry in catalog["datasets"]:
        expected = str(entry.get("expected_size", "to-audit"))
        match = re.fullmatch(r"([0-9.]+)(KB|MB|GB)", expected)
        size_gb = None if not match else float(match.group(1)) / {"KB": 1024**2, "MB": 1024, "GB": 1}[match.group(2)]
        if size_gb is None or size_gb > args.max_known_gb:
            reports.append({"dataset": entry["id"], "status": "skipped_size_unknown", "training_eligible": False})
            continue
        try:
            report = prepare_quarantine(download_selected(audit_dataset(entry["id"])))
            reports.append(report)
        except Exception as error:
            reports.append({"dataset": entry["id"], "status": "failed", "error": str(error), "training_eligible": False})
    output = ROOT / "corpus/quarantine/huggingface/learning-batch-1.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"schema": "huggingface-learning-batch/v1", "max_known_gb": args.max_known_gb, "reports": reports, "training_eligible": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"processed": len(reports), "output": str(output.relative_to(ROOT)), "training_eligible": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
