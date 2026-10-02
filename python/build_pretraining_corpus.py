"""Build a local pretraining corpus from exact, licensed repository matches."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCUMENTS = ROOT / "corpus" / "raw" / "learned_topics.jsonl"
DEFAULT_REGISTRY = ROOT / "corpus" / "programming_sources.jsonl"
DEFAULT_OUTPUT = ROOT / "corpus" / "clean" / "pretraining_programming_permissive.jsonl"
DEFAULT_LICENSES = ("MIT", "BSD-3-Clause")
WORKSPACE_CHUNK_CHARS = 12_000


def repository_key(url: str) -> tuple[str, str, str] | None:
    parsed = urlsplit(url if "://" in url else f"https://{url}")
    parts = [unquote(part).lower() for part in parsed.path.split("/") if part]
    host = (parsed.hostname or "").lower()
    if host == "raw.githubusercontent.com" and len(parts) >= 2:
        return "github.com", parts[0], parts[1].removesuffix(".git")
    if host in {"github.com", "www.github.com"} and len(parts) >= 2:
        return "github.com", parts[0], parts[1].removesuffix(".git")
    return None


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def curate_records(
    documents: list[dict],
    registry: list[dict],
    allowed_licenses: set[str],
    *,
    documents_path: str,
    registry_path: str,
) -> tuple[list[dict], Counter]:
    registry_by_repo = {}
    for source in registry:
        key = repository_key(str(source.get("url") or ""))
        if key and source.get("license") in allowed_licenses:
            registry_by_repo[key] = source

    accepted = []
    rejected = Counter()
    seen_hashes = set()
    for document in documents:
        source_url = str(document.get("url") or document.get("source") or "").strip()
        key = repository_key(source_url)
        source = registry_by_repo.get(key) if key else None
        if source is None:
            rejected["unmatched_or_disallowed_source"] += 1
            continue
        text = str(document.get("text") or "").strip()
        if not text:
            rejected["empty_text"] += 1
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in seen_hashes:
            rejected["duplicate_text"] += 1
            continue
        seen_hashes.add(digest)
        accepted.append({
            "id": f"pretrain-{digest[:16]}",
            "text": text,
            "source": source_url,
            "source_url": source_url,
            "repository": source.get("title", ""),
            "registry_source_id": source.get("id", ""),
            "license": source["license"],
            "license_reference": source["url"],
            "license_registry": registry_path,
            "license_status": "declared-in-local-registry-not-legally-reviewed",
            "language": source.get("language") or document.get("language") or "und",
            "category": document.get("category") or source.get("category") or "programming",
            "path": documents_path,
            "sha256": digest,
        })
    return accepted, rejected


def _workspace_paths(root: Path) -> list[Path]:
    paths = [*root.glob("*.md")]
    for directory, pattern in (
        ("Documentacoes", "*.md"),
        ("python", "*.py"),
        ("runtime/src", "*.rs"),
        ("agent-core/src", "*.ts"),
    ):
        base = root / directory
        if base.is_dir():
            paths.extend(base.rglob(pattern) if directory in {"Documentacoes", "runtime/src", "agent-core/src"}
                         else base.glob(pattern))
    for name in ("README.md", "corpus/README.md", "model/README.md", "runtime/README.md",
                 "python/README.md", "agent-core/README.md"):
        path = root / name
        if path.is_file():
            paths.append(path)
    return sorted(set(path for path in paths if path.is_file()))


def _split_workspace_text(text: str, max_chars: int = WORKSPACE_CHUNK_CHARS) -> list[str]:
    chunks = []
    current = []
    current_chars = 0
    for line in text.splitlines(keepends=True):
        if len(line) > max_chars:
            if current:
                chunks.append("".join(current).strip())
                current, current_chars = [], 0
            chunks.extend(line[index:index + max_chars].strip() for index in range(0, len(line), max_chars))
        elif current_chars + len(line) > max_chars:
            chunks.append("".join(current).strip())
            current, current_chars = [line], len(line)
        else:
            current.append(line)
            current_chars += len(line)
    if current:
        chunks.append("".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def workspace_records(root: Path = ROOT) -> list[dict]:
    records = []
    seen_hashes = set()
    for path in _workspace_paths(Path(root)):
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        category = {
            ".md": "workspace-documentation",
            ".py": "programming/python",
            ".rs": "programming/rust",
            ".ts": "programming/typescript",
        }[suffix]
        text = path.read_text(encoding="utf-8", errors="replace")
        for part, chunk in enumerate(_split_workspace_text(text)):
            digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            records.append({
                "id": f"workspace-{digest[:16]}",
                "text": f"Workspace file: {relative}\n\n{chunk}",
                "source": "user-workspace",
                "source_url": "",
                "repository": "local-project",
                "license": "user-provided-local",
                "license_reference": relative,
                "license_registry": "user-workspace-consent",
                "license_status": "local-workspace-user-authorized-not-legal-review",
                "language": "und" if suffix == ".md" else "code",
                "category": category,
                "path": relative,
                "part": part,
                "sha256": digest,
            })
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--allow-license", action="append", dest="licenses")
    parser.add_argument("--include-workspace", action="store_true",
                        help="include selected first-party Markdown and source directories as user-provided data")
    args = parser.parse_args()

    licenses = set(args.licenses or DEFAULT_LICENSES)
    registry = read_jsonl(args.registry)
    documents = read_jsonl(args.documents)
    records, rejected = curate_records(
        documents, registry, licenses,
        documents_path=str(args.documents.relative_to(ROOT)) if args.documents.is_relative_to(ROOT) else str(args.documents),
        registry_path=str(args.registry.relative_to(ROOT)) if args.registry.is_relative_to(ROOT) else str(args.registry),
    )
    workspace_count = 0
    if args.include_workspace:
        existing_hashes = {record["sha256"] for record in records}
        local_records = [record for record in workspace_records(ROOT)
                         if record["sha256"] not in existing_hashes]
        workspace_count = len(local_records)
        records.extend(local_records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    print(json.dumps({
        "schema": "local-pretraining-corpus/v1",
        "input_records": len(documents),
        "accepted_records": len(records),
        "workspace_records": workspace_count,
        "accepted_chars": sum(len(record["text"]) for record in records),
        "allowed_licenses": sorted(licenses),
        "rejected": dict(rejected),
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()