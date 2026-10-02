#!/usr/bin/env python3
"""Importa um dataset do Hugging Face para quarentena e separa pares de avaliação."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from ingest_hf_dataset import (  # noqa: E402
    QUARANTINE,
    ROOT as INGEST_ROOT,
    audit_dataset,
    download_selected,
    normalize_dataset_id,
    prepare_quarantine,
)

MAX_EVALUATION_ROWS = 500
MAX_FIELD_CHARS = 16000
QUESTION_KEYS = ("question", "prompt", "instruction", "query", "problem", "user", "input", "task")
ANSWER_KEYS = ("answer", "response", "output", "completion", "solution", "target", "assistant", "chosen")
USER_ROLES = {"user", "human", "prompter"}
ASSISTANT_ROLES = {"assistant", "gpt", "bot", "model"}


def dataset_id_from_link(value: str) -> str:
    raw = value.strip()
    if "://" not in raw:
        return normalize_dataset_id(raw)
    parsed = urlsplit(raw)
    if parsed.scheme != "https" or parsed.hostname not in {"huggingface.co", "www.huggingface.co"}:
        raise ValueError("use uma URL HTTPS de huggingface.co/datasets/... ou owner/dataset")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 3 or parts[0] != "datasets":
        raise ValueError("a URL deve apontar para /datasets/owner/nome-do-dataset")
    return normalize_dataset_id("/".join(parts[1:3]))


def ask_yes_no(question: str, default: bool = False) -> bool:
    suffix = "[S/n]" if default else "[s/N]"
    answer = input(f"{question} {suffix} ").strip().casefold()
    if not answer:
        return default
    return answer in {"s", "sim", "y", "yes"}


def as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(part for part in (as_text(item) for item in value) if part)
    if isinstance(value, dict):
        for key in ("text", "content", "value", "answer"):
            if key in value:
                rendered = as_text(value[key])
                if rendered:
                    return rendered
        return json.dumps(value, ensure_ascii=False)
    return ""


def row_to_qa(row: dict) -> tuple[str, str] | None:
    lowered = {str(key).casefold(): value for key, value in row.items()}
    question = next((as_text(lowered[key]) for key in QUESTION_KEYS if key in lowered and as_text(lowered[key])), "")
    answer = next((as_text(lowered[key]) for key in ANSWER_KEYS if key in lowered and as_text(lowered[key])), "")

    instruction = as_text(lowered.get("instruction"))
    extra_input = as_text(lowered.get("input"))
    if instruction and extra_input and question == instruction:
        question = f"{instruction}\n\nContexto/entrada:\n{extra_input}"

    if not question or not answer:
        conversation = lowered.get("messages", lowered.get("conversations"))
        if isinstance(conversation, list):
            user_turns = []
            assistant_turns = []
            for message in conversation:
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role", message.get("from", ""))).casefold()
                content = as_text(message.get("content", message.get("value", message.get("text"))))
                if role in USER_ROLES and content:
                    user_turns.append(content)
                elif role in ASSISTANT_ROLES and content:
                    assistant_turns.append(content)
            if user_turns and assistant_turns:
                question, answer = user_turns[-1], assistant_turns[-1]

    question = re.sub(r"\s+", " ", question).strip()[:MAX_FIELD_CHARS]
    answer = re.sub(r"\s+", " ", answer).strip()[:MAX_FIELD_CHARS]
    if len(question) < 4 or len(answer) < 1:
        return None
    return question, answer


def iter_rows(path: Path):
    suffix = path.suffix.casefold()
    if suffix in {".jsonl", ".ndjson"}:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    yield value
        return
    if suffix == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            return
        if isinstance(value, list):
            yield from (item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            for candidate in value.values():
                if isinstance(candidate, list):
                    yield from (item for item in candidate if isinstance(item, dict))
            if not any(isinstance(candidate, list) for candidate in value.values()):
                yield value
        return
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open(encoding="utf-8", errors="replace", newline="") as handle:
            yield from csv.DictReader(handle, delimiter=delimiter)
        return
    if suffix == ".parquet":
        try:
            import pyarrow.parquet as parquet
            table = parquet.read_table(path)
            yield from table.slice(0, 100000).to_pylist()
        except ImportError:
            return
        except Exception:
            return


def write_evaluation_pairs(report: dict) -> dict:
    dataset_id = report["dataset"]
    digest = hashlib.sha256(dataset_id.encode()).hexdigest()[:16]
    output = QUARANTINE / digest / "evaluation.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    examples = []
    for item in report.get("download", {}).get("files", []):
        if item.get("status") != "downloaded":
            continue
        relative = Path(str(item.get("path", "")))
        source = (INGEST_ROOT / relative).resolve()
        if INGEST_ROOT.resolve() not in source.parents or not source.is_file():
            continue
        for row in iter_rows(source):
            pair = row_to_qa(row)
            if not pair:
                continue
            question, answer = pair
            key = hashlib.sha256(question.casefold().encode("utf-8")).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            examples.append({
                "id": f"hf-{len(examples) + 1:04d}",
                "dataset": dataset_id,
                "source_file": item["name"],
                "prompt": question,
                "reference": answer,
                "metric": "token_f1",
                "split": "evaluation",
                "evaluation_only": True,
                "training_eligible": False,
                "license_review_required": True,
            })
            if len(examples) >= MAX_EVALUATION_ROWS:
                break
        if len(examples) >= MAX_EVALUATION_ROWS:
            break
    with output.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example, ensure_ascii=False) + "\n")
    return {
        "path": str(output.relative_to(ROOT)),
        "examples": len(examples),
        "limit": MAX_EVALUATION_ROWS,
        "status": "evaluation_only_quarantine",
        "training_eligible": False,
        "license_review_required": True,
    }


def persist_manifest(report: dict) -> Path:
    digest = hashlib.sha256(report["dataset"].encode()).hexdigest()[:16]
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    destination = QUARANTINE / f"{digest}.json"
    report["quarantine_manifest"] = str(destination.relative_to(ROOT))
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Audita e importa um dataset Hugging Face para quarentena.")
    parser.add_argument("--url", help="URL de dataset HF; se omitida, o script solicitará o link")
    parser.add_argument("--download", action="store_true", help="baixa os arquivos selecionados sem perguntar novamente")
    parser.add_argument("--audit-only", action="store_true", help="salva somente os metadados, sem baixar arquivos")
    args = parser.parse_args()

    if args.download and args.audit_only:
        parser.error("--download e --audit-only são incompatíveis")

    raw_link = args.url or input("Cole a URL do dataset no Hugging Face (ou owner/dataset): ")
    try:
        dataset_id = dataset_id_from_link(raw_link)
        report = audit_dataset(dataset_id)
        metadata = report["metadata"]
        license_info = report["license"]
        print(f"Dataset: {dataset_id}")
        print(f"Licença identificada: {license_info.get('value') or 'não identificada'}")
        print(f"Revisão de licença necessária: {'sim' if license_info.get('requires_review') else 'não'}")
        size_bytes = int(metadata.get("total_bytes", 0))
        print(f"Tamanho informado pelo Hugging Face: {size_bytes / 1_000_000_000:.2f} GB" if size_bytes else "Tamanho total informado: indisponível")
        print("Destino: corpus/quarantine/huggingface/ (snapshot completo; não entra no treino automaticamente)")

        should_download = args.download
        if not args.audit_only and not args.download:
            should_download = ask_yes_no(
                f"Baixar o snapshot completo de {size_bytes / 1_000_000_000:.2f} GB e preparar exemplos para avaliação?",
                default=False,
            )
        if should_download:
            report = prepare_quarantine(download_selected(report))
            report["evaluation"] = write_evaluation_pairs(report)
            print(f"Exemplos pergunta/resposta para avaliação: {report['evaluation']['examples']}")
            print(f"Arquivo de avaliação: {report['evaluation']['path']}")
            print("As referências ficam em quarentena; isso não autoriza treino nem promoção do modelo.")
        manifest = persist_manifest(report)
        print(f"Manifesto: {manifest.relative_to(ROOT)}")
        print(json.dumps({
            "dataset": dataset_id,
            "status": "imported_to_quarantine" if should_download else "audited_only",
            "manifest": str(manifest.relative_to(ROOT)),
            "evaluation": report.get("evaluation"),
            "training_eligible": False,
        }, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TimeoutError, RuntimeError) as error:
        print(f"Falha na importação: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
