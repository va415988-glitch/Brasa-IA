"""Corpus de código e documentação já presentes na máquina, sem rede.

Fontes (somente licenças permissivas):
  python_stdlib  biblioteca padrão do Python instalada (PSF-2.0)
  rust_crates    crates no cache do cargo cuja licença declarada é MIT/Apache/BSD
  brasa_code     código, contratos e documentação do próprio projeto (autoral)

Grava pretrain_data_raw/local_code/<fonte>.txt com documentos separados por NUL,
no formato lido por pretrain/prepare_data.py.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import sysconfig
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "pretrain_data_raw" / "local_code"
DOC_SEP = "\x00"
MAX_DOC = 48_000
PERMISSIVE = re.compile(r"\b(?:MIT|Apache-2\.0|BSD-[23]-Clause|ISC|Zlib|Unlicense|CC0-1\.0|0BSD)\b")
SKIP_DIRS = {"__pycache__", "node_modules", "target", ".git", "test_data", "tests/data", "benches", "fixtures"}


def clean(text):
    text = text.replace(DOC_SEP, " ").replace("\r\n", "\n")
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def write_source(name, documents, license_name, origin):
    OUT.mkdir(parents=True, exist_ok=True)
    seen, kept, size = set(), 0, 0
    with (OUT / f"{name}.txt").open("w", encoding="utf-8") as handle:
        for header, body in documents:
            body = clean(body)
            if len(body) < 200:
                continue
            digest = hashlib.sha1(body.encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            document = f"{header}\n{body[:MAX_DOC]}"
            handle.write(document + DOC_SEP)
            kept += 1
            size += len(document.encode())
    return {"name": name, "language": "code", "license": license_name, "origin": origin,
            "documents": kept, "bytes": size, "text_path": str((OUT / f"{name}.txt").relative_to(ROOT))}


def python_stdlib():
    base = Path(sysconfig.get_paths()["stdlib"])
    for path in sorted(base.rglob("*.py")):
        relative = path.relative_to(base)
        if any(part in {"test", "tests", "idlelib", "site-packages", "__pycache__", "lib2to3"} for part in relative.parts):
            continue
        try:
            yield f"# arquivo: python/{relative.as_posix()}", path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue


def crate_license(crate_dir):
    try:
        manifest = tomllib.loads((crate_dir / "Cargo.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return None
    license_name = str((manifest.get("package") or {}).get("license") or "")
    return license_name if PERMISSIVE.search(license_name) and "GPL" not in license_name else None


def rust_crates():
    registry = Path.home() / ".cargo" / "registry" / "src"
    for crate_dir in sorted(registry.glob("*/*")):
        if not crate_dir.is_dir() or not crate_license(crate_dir):
            continue
        for path in sorted(crate_dir.rglob("*")):
            if path.suffix not in {".rs", ".md"} or any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.stat().st_size > 200_000:
                continue
            try:
                yield f"# arquivo: {crate_dir.name}/{path.relative_to(crate_dir).as_posix()}", path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue


def brasa_code():
    patterns = ["python/*.py", "agent-core/src/*.ts", "runtime/src/*.rs", "runtime/src/bin/*.rs", "pretrain/*.py",
                "scripts/*.py", "contracts/*.json", "Documentacoes/*.md", "*.md", "agent-core/README.md",
                "python/templates/**/*.py", "runtime/static/*.js"]
    for pattern in patterns:
        for path in sorted(ROOT.glob(pattern)):
            if path.stat().st_size > 400_000:
                continue
            try:
                yield f"# arquivo: {path.relative_to(ROOT).as_posix()}", path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue


def main():
    sources = [
        write_source("python_stdlib", python_stdlib(), "PSF-2.0", f"Python {sys.version.split()[0]} stdlib"),
        write_source("rust_crates", rust_crates(), "MIT/Apache-2.0/BSD (por crate)", "~/.cargo/registry/src"),
        write_source("brasa_code", brasa_code(), "autoral (projeto Brasa)", "repositório local"),
    ]
    (OUT / "manifest.json").write_text(json.dumps({"family": "local_code", "sources": sources}, ensure_ascii=False, indent=2) + "\n")
    for source in sources:
        print(f"{source['name']}: {source['documents']} docs, {source['bytes'] / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
