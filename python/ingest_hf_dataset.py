"""Descoberta e ingestão controlada de datasets Hugging Face.

O modo padrão é auditoria. A ingestão grava apenas manifesto e metadados em
quarentena; nenhum conteúdo entra no treino sem revisão posterior.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from urllib.parse import urlencode
import csv
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUARANTINE = ROOT / "corpus" / "quarantine" / "huggingface"
MAX_METADATA_BYTES = 64 * 1024 * 1024
MAX_DATASET_BYTES = 50 * 1000 * 1000 * 1000
TEXT_EXTENSIONS = (".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".txt", ".md", ".rst", ".parquet", ".py", ".pyi", ".sh", ".bash", ".toml", ".yaml", ".yml", ".json5", ".xml", ".html", ".css", ".js", ".ts", ".sql", ".diff", ".patch", ".ini", ".cfg", ".ipynb")
ALLOWED_LICENSE_HINTS = ("mit", "apache", "bsd", "cc-by", "cc0", "odc")
MAX_EXAMPLES = 50000
MAX_EXAMPLE_CHARS = 32000
MAX_PARQUET_ROWS = 100000


def normalize_dataset_id(value: str) -> str:
    value = value.strip().removeprefix("https://huggingface.co/datasets/").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("use o identificador owner/dataset ou uma URL de dataset Hugging Face")
    return value


def huggingface_headers() -> dict:
    headers = {"User-Agent": "ia-local-do-zero-dataset-auditor/1.0"}
    token = os.environ.get("HF_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers=huggingface_headers())
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(MAX_METADATA_BYTES + 1)
    if len(data) > MAX_METADATA_BYTES:
        raise ValueError("metadados acima do limite")
    return json.loads(data.decode("utf-8"))


def audit_dataset(dataset_id: str) -> dict:
    dataset_id = normalize_dataset_id(dataset_id)
    params = urlencode([
        ("expand[]", "tags"),
        ("expand[]", "description"),
        ("expand[]", "downloads"),
        ("expand[]", "likes"),
        ("expand[]", "lastModified"),
        ("expand[]", "mainSize"),
    ])
    metadata = fetch_json(f"https://huggingface.co/api/datasets/{dataset_id}?{params}")
    tags = [str(tag) for tag in metadata.get("tags", [])]
    card = " ".join(tags + [str(metadata.get("description", ""))]).casefold()
    license_value = next((tag.removeprefix("license:") for tag in tags if tag.startswith("license:")), None)
    license_known = bool(license_value and any(hint in license_value.casefold() for hint in ALLOWED_LICENSE_HINTS))

    main_size = metadata.get("mainSize")
    if main_size is not None:
        main_size = int(main_size)

    return {
        "schema": "huggingface-dataset-audit/v1",
        "dataset": dataset_id,
        "source": f"https://huggingface.co/datasets/{dataset_id}",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "license": {"value": license_value, "known": license_known, "requires_review": not license_known},
        "metadata": {"downloads": metadata.get("downloads"), "likes": metadata.get("likes"), "last_modified": metadata.get("lastModified"), "tags": tags[:100], "candidate_files": [], "file_count": None, "total_bytes": main_size},
        "classification": {
            "code": any(term in card for term in ("code", "programming", "software", "repository")),
            "agent": any(term in card for term in ("agent", "tool", "trajectory", "trace", "instruction")),
            "evaluation": any(term in card for term in ("benchmark", "eval", "test")),
        },
        "policy": {"status": "quarantine", "training_eligible": False, "evaluation_eligible": True, "reason": "revisão de licença, duplicatas e contaminação ainda necessária"},
    }


def download_selected(report: dict) -> dict:
    """Baixa o snapshot completo em streaming, com retomada gerenciada pelo Hub."""
    dataset_id = report["dataset"]
    expected_total = int(report["metadata"].get("total_bytes") or 0)
    if expected_total > MAX_DATASET_BYTES:
        raise ValueError(
            f"dataset tem {expected_total / 1_000_000_000:.2f} GB; capacidade configurada: "
            f"{MAX_DATASET_BYTES / 1_000_000_000:.0f} GB"
        )

    try:
        from huggingface_hub import snapshot_download
    except ImportError as error:
        raise RuntimeError("instale huggingface_hub para baixar e retomar arquivos grandes: python -m pip install -r python/requirements-hf-import.txt") from error

    digest = hashlib.sha256(dataset_id.encode()).hexdigest()[:16]
    base = QUARANTINE / digest / "files"
    base.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN", "").strip() or False
    downloaded_root = Path(snapshot_download(
        repo_id=dataset_id,
        repo_type="dataset",
        revision="main",
        local_dir=str(base),
        token=token,
        max_workers=8,
    )).resolve()

    files = []
    actual_total = 0
    for current, directories, filenames in os.walk(downloaded_root):
        directories[:] = [name for name in directories if name != ".cache"]
        current_path = Path(current)
        for filename in filenames:
            target = (current_path / filename).resolve()
            if downloaded_root not in target.parents or not target.is_file():
                continue
            size = target.stat().st_size
            actual_total += size
            files.append({"name": target.relative_to(downloaded_root).as_posix(), "status": "downloaded", "bytes": size, "path": str(target.relative_to(ROOT))})
    if actual_total > MAX_DATASET_BYTES:
        raise ValueError(f"download final excedeu {MAX_DATASET_BYTES / 1_000_000_000:.0f} GB; revise o snapshot em {downloaded_root}")
    report["download"] = {
        "status": "quarantine",
        "total_bytes": actual_total,
        "expected_bytes": expected_total,
        "file_count": len(files),
        "files": files,
        "training_eligible": False,
        "complete_snapshot": True,
        "resume_supported": True,
    }
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
    if suffix not in TEXT_EXTENSIONS:
        return
    if suffix == ".parquet":
        try:
            import pyarrow.parquet as parquet
            source = parquet.ParquetFile(path)
            for batch in source.iter_batches(batch_size=1024):
                for row in batch.to_pylist():
                    text = _text_from_value(row)
                    if text:
                        yield text
        except Exception:
            return
        return
    if suffix in {".jsonl", ".ndjson"}:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                    text = _text_from_value(value) or (value if isinstance(value, str) else "")
                except json.JSONDecodeError:
                    text = line
                if text:
                    yield text
        return
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open(encoding="utf-8", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle, delimiter=delimiter):
                text = _text_from_value(row)
                if text:
                    yield text
        return
    if suffix == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            return
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = _text_from_value(item) or (item if isinstance(item, str) else "")
            if text:
                yield text
        return

    with path.open(encoding="utf-8", errors="replace") as handle:
        block = []
        for line in handle:
            if not line.strip():
                if block:
                    yield "".join(block)
                    block = []
            else:
                block.append(line)
        if block:
            yield "".join(block)


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
    parser.add_argument("--download", action="store_true", help="baixa o snapshot completo para quarentena (até 50 GB)")
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
