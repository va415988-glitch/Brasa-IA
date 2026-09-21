"""Normaliza, filtra e deduplica documentos para o acervo local.

O script deliberadamente não baixa a internet. A aquisição é uma decisão
separada, com fonte e licença registradas no manifest. Isso evita transformar
um download grande e impossível de auditar em "conhecimento" do modelo.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import unicodedata
from pathlib import Path

EXTENSIONS = {".txt", ".md", ".html", ".htm", ".json", ".jsonl"}
DEFAULT_SOURCE = "user-local"
DEFAULT_CATEGORY = "general"
MIN_CHARS = 120
MAX_CHARS = 200_000
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SPACE_RE = re.compile(r"[ \t]+")
BLANK_RE = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", html.unescape(text))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = CONTROL_RE.sub("", text)
    # Preserve line structure, but remove layout noise from scraped pages.
    lines = [SPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    return BLANK_RE.sub("\n\n", "\n".join(lines)).strip()


def html_to_text(text: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return text


def chunks(text: str, target_chars: int) -> list[str]:
    """Divide por parágrafos, mantendo trechos curtos e recuperáveis."""
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    result = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 2 > target_chars:
            result.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}".strip()
    if current:
        result.append(current)
    return result or [text]


def read_document(path: Path) -> list[tuple[str, dict]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in {".html", ".htm"}:
        return [(html_to_text(raw), {})]
    if path.suffix.lower() == ".jsonl":
        result = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                text = value.get("text") or value.get("content") or value.get("body")
                if isinstance(text, str):
                    result.append((text, value))
            elif isinstance(value, str):
                result.append((value, {}))
        return result
    if path.suffix.lower() == ".json":
        value = json.loads(raw)
        if isinstance(value, dict):
            text = value.get("text") or value.get("content") or value.get("body")
            return [(text, value)] if isinstance(text, str) else []
        if isinstance(value, list):
            return [(str(item), {}) for item in value if isinstance(item, str)]
        return []
    return [(raw, {})]


def files_for(input_path: Path):
    if input_path.is_file():
        yield input_path
        return
    for path in sorted(input_path.rglob("*")):
        if path.is_file() and path.suffix.lower() in EXTENSIONS and not any(p.startswith(".") for p in path.parts):
            yield path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="arquivo ou diretório local")
    parser.add_argument("--output", default="corpus/clean/knowledge.jsonl")
    parser.add_argument("--manifest", default="corpus/manifest.jsonl")
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--category", default=DEFAULT_CATEGORY)
    parser.add_argument("--language", default="pt-BR")
    parser.add_argument("--min-chars", type=int, default=MIN_CHARS)
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS)
    parser.add_argument("--chunk-chars", type=int, default=2400)
    args = parser.parse_args()

    source_path = Path(args.input)
    output = Path(args.output)
    manifest = Path(args.manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    accepted = rejected = duplicate = 0
    with output.open("w", encoding="utf-8") as out, manifest.open("a", encoding="utf-8") as log:
        for path in files_for(source_path):
            try:
                documents = read_document(path)
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                rejected += 1
                print(f"rejeitado {path}: {error}")
                continue
            for raw_text, metadata in documents:
                normalized = normalize_text(raw_text)
                if len(normalized) > args.max_chars:
                    rejected += 1
                    continue
                for text in chunks(normalized, args.chunk_chars):
                    if len(text) < args.min_chars:
                        rejected += 1
                        continue
                    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    if digest in seen:
                        duplicate += 1
                        continue
                    seen.add(digest)
                    record = {
                        "id": f"doc-{digest[:16]}",
                        "text": text,
                        "source": metadata.get("source", args.source),
                        "license": metadata.get("license", "unknown-review-required"),
                        "language": metadata.get("language", args.language),
                        "category": metadata.get("category", args.category),
                        "path": str(path),
                        "sha256": digest,
                    }
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    log.write(json.dumps({k: record[k] for k in record if k != "text"}, ensure_ascii=False) + "\n")
                    accepted += 1
    print(f"documentos aceitos: {accepted}")
    print(f"duplicatas: {duplicate}")
    print(f"rejeitados: {rejected}")
    print(f"saída: {output}")


if __name__ == "__main__":
    main()
