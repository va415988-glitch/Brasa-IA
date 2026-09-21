"""Descoberta e ingestão controlada de datasets Hugging Face.

O modo padrão é auditoria. A ingestão grava apenas manifesto e metadados em
quarentena; nenhum conteúdo entra no treino sem revisão posterior.
"""

import argparse
import hashlib
import json
import re
import urllib.request
import csv
import io
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUARANTINE = ROOT / "corpus" / "quarantine" / "huggingface"
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_DATASET_BYTES = 1024 * 1024 * 1024
ALLOWED_EXTENSIONS = (".json", ".jsonl", ".csv", ".tsv", ".txt", ".md", ".parquet")
ALLOWED_LICENSE_HINTS = ("mit", "apache", "bsd", "cc-by", "cc0", "odc")
MAX_EXAMPLES = 50000
MAX_EXAMPLE_CHARS = 32000
MAX_PARQUET_ROWS = 100000


def normalize_dataset_id(value: str) -> str:
    value = value.strip().removeprefix("https://huggingface.co/datasets/").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("use o identificador owner/dataset ou uma URL de dataset Hugging Face")
    return value


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "ia-local-do-zero-dataset-auditor/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(MAX_METADATA_BYTES + 1)
    if len(data) > MAX_METADATA_BYTES:
        raise ValueError("metadados acima do limite")
    return json.loads(data.decode("utf-8"))


def audit_dataset(dataset_id: str) -> dict:
    dataset_id = normalize_dataset_id(dataset_id)
    metadata = fetch_json(f"https://huggingface.co/api/datasets/{dataset_id}")
    tags = [str(tag) for tag in metadata.get("tags", [])]
    card = " ".join(tags + [str(metadata.get("description", ""))]).casefold()
    license_value = next((tag.removeprefix("license:") for tag in tags if tag.startswith("license:")), None)
    license_known = bool(license_value and any(hint in license_value.casefold() for hint in ALLOWED_LICENSE_HINTS))
    files = []
    for sibling in metadata.get("siblings", []):
        name = str(sibling.get("rfilename", ""))
        if name.casefold().endswith(ALLOWED_EXTENSIONS):
            files.append({"name": name, "size": sibling.get("size"), "selected": True})
    return {
        "schema": "huggingface-dataset-audit/v1",
        "dataset": dataset_id,
        "source": f"https://huggingface.co/datasets/{dataset_id}",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "license": {"value": license_value, "known": license_known, "requires_review": not license_known},
        "metadata": {"downloads": metadata.get("downloads"), "likes": metadata.get("likes"), "last_modified": metadata.get("lastModified"), "tags": tags[:100], "candidate_files": files[:200]},
        "classification": {
            "code": any(term in card for term in ("code", "programming", "software", "repository")),
            "agent": any(term in card for term in ("agent", "tool", "trajectory", "trace", "instruction")),
            "evaluation": any(term in card for term in ("benchmark", "eval", "test")),
        },
        "policy": {"status": "quarantine", "training_eligible": False, "evaluation_eligible": True, "reason": "revisão de licença, duplicatas e contaminação ainda necessária"},
    }


def download_selected(report: dict) -> dict:
    dataset_id = report["dataset"]
    selected = report["metadata"]["candidate_files"]
    total = 0
    downloaded = []
    base = QUARANTINE / hashlib.sha256(dataset_id.encode()).hexdigest()[:16] / "files"
    for item in selected:
        if len(downloaded) >= 50:
            break
        name = item["name"]
        url = f"https://huggingface.co/datasets/{dataset_id}/resolve/main/{name}"
        request = urllib.request.Request(url, headers={"User-Agent": "ia-local-do-zero-dataset-auditor/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read(MAX_FILE_BYTES + 1)
        except Exception as error:
            downloaded.append({"name": name, "status": "failed", "error": str(error)})
            continue
        if len(data) > MAX_FILE_BYTES or total + len(data) > MAX_DATASET_BYTES:
            downloaded.append({"name": name, "status": "skipped", "reason": "limite de tamanho"})
            continue
        destination = (base / Path(name)).resolve()
        if base.resolve() not in destination.parents:
            downloaded.append({"name": name, "status": "skipped", "reason": "caminho fora da quarentena"})
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        total += len(data)
        downloaded.append({"name": name, "status": "downloaded", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "path": str(destination.relative_to(ROOT))})
    report["download"] = {"status": "quarantine", "total_bytes": total, "files": downloaded, "training_eligible": False}
    return report


def _text_from_value(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            text = _text_from_value(item)
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        preferred = ("text", "content", "prompt", "instruction", "input", "output", "completion", "messages")
        parts = [_text_from_value(value[key]) for key in preferred if key in value]
        return "\n".join(part for part in parts if part)
    return ""


def normalize_example(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:MAX_EXAMPLE_CHARS]


def extract_examples(path: Path):
    suffix = path.suffix.casefold()
    if suffix == ".parquet":
        try:
            import pyarrow.parquet as parquet
            table = parquet.read_table(path)
            rows = table.slice(0, MAX_PARQUET_ROWS).to_pylist()
            for row in rows:
                text = _text_from_value(row)
                if text:
                    yield text
        except Exception:
            return
        return
    raw = path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".jsonl":
        for line in raw.splitlines():
            try:
                value = json.loads(line)
                text = _text_from_value(value) or (value if isinstance(value, str) else "")
            except json.JSONDecodeError:
                text = line
            if text:
                yield text
    elif suffix == ".json":
        try:
            value = json.loads(raw)
            values = value if isinstance(value, list) else [value]
            for item in values:
                text = _text_from_value(item) or (item if isinstance(item, str) else "")
                if text:
                    yield text
        except json.JSONDecodeError:
            return
    elif suffix in (".csv", ".tsv"):
        delimiter = "\t" if suffix == ".tsv" else ","
        for row in csv.DictReader(io.StringIO(raw), delimiter=delimiter):
            text = _text_from_value(row)
            if text:
                yield text
    else:
        for block in re.split(r"\n\s*\n", raw):
            if block.strip():
                yield block


def prepare_quarantine(report: dict) -> dict:
    downloaded = [item for item in report.get("download", {}).get("files", []) if item.get("status") == "downloaded"]
    seen = set()
    examples = []
    candidates = 0
    for item in downloaded:
        path = ROOT / item["path"]
        for raw in extract_examples(path):
            text = normalize_example(raw)
            candidates += 1
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if len(text) < 20 or digest in seen:
                continue
            seen.add(digest)
            examples.append({"dataset": report["dataset"], "source_file": item["name"], "sha256": digest, "text": text, "split": "quarantine", "training_eligible": False, "license_review_required": True})
            if len(examples) >= MAX_EXAMPLES:
                break
        if len(examples) >= MAX_EXAMPLES:
            break
    digest = hashlib.sha256(report["dataset"].encode()).hexdigest()[:16]
    output = QUARANTINE / digest / "prepared.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example, ensure_ascii=False) + "\n")
    report["preparation"] = {"status": "quarantine", "examples": len(examples), "candidates": candidates, "duplicates_removed": max(0, candidates - len(examples)), "path": str(output.relative_to(ROOT)), "training_eligible": False, "review_required": True}
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset")
    parser.add_argument("--ingest", action="store_true", help="salva o manifesto auditado em quarentena")
    parser.add_argument("--download", action="store_true", help="baixa somente arquivos textuais selecionados para quarentena")
    parser.add_argument("--prepare", action="store_true", help="converte e deduplica os arquivos baixados, ainda em quarentena")
    args = parser.parse_args()
    report = audit_dataset(args.dataset)
    if args.download:
        report = download_selected(report)
    if args.prepare:
        if "download" not in report:
            report = download_selected(report)
        report = prepare_quarantine(report)
    if args.ingest or args.download or args.prepare:
        QUARANTINE.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(report["dataset"].encode()).hexdigest()[:16]
        path = QUARANTINE / f"{digest}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["quarantine_manifest"] = str(path.relative_to(ROOT))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
