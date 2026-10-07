"""Monta o corpus de pré-treino local a partir de pretrain_data_raw/.

Lê os manifestos das coletas (pretrain/corpus_fetch/*) e as saídas do motor de
dados verificados (pretrain/data_engine/*), aplica um teto em MB por fonte,
reserva ~1% dos documentos de cada fonte para avaliação (escolha por hash, sem
sobreposição com o treino) e grava:

  <out>/text/<fonte>.txt      documentos de treino (NUL entre documentos)
  <out>/heldout/<fonte>.txt   documentos reservados para eval_bpb.py
  <out>/sources.json          fontes no formato de prepare_data.py (kind "nul")
  <out>/corpus_manifest.json  licença, origem, documentos e bytes por fonte

Depois rode prepare_data.py com --out <out> --sources <out>/sources.json: os
textos já existem, então ele só treina o tokenizer e tokeniza.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "pretrain_data_raw"
DOC_SEP = "\x00"

# Tetos padrão em MB por fonte (correspondência por prefixo do nome; o primeiro
# prefixo que casar vence). Português não tem teto: é a prioridade do modelo.
DEFAULT_CAPS = [
    ("pypi_code", 30), ("rust_crates", 12), ("python_stdlib", 8), ("brasa_code", 4),
    ("en_", 40), ("gutenberg", 12), ("english", 12),
]


def read_docs(path):
    with Path(path).open(encoding="utf-8") as handle:
        buffer = ""
        while chunk := handle.read(1 << 22):
            buffer += chunk
            *docs, buffer = buffer.split(DOC_SEP)
            yield from (doc for doc in docs if doc.strip())
        if buffer.strip():
            yield buffer


def discover(raw):
    """Fontes declaradas nos manifestos e arquivos do motor de dados."""
    sources = []
    for manifest_path in sorted(raw.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for source in manifest.get("sources", []):
            text_path = Path(source["text_path"])
            text_path = text_path if text_path.is_absolute() else ROOT / text_path
            if text_path.exists():
                sources.append({"name": f"{manifest_path.parent.name}__{source['name']}", "path": text_path,
                                "language": source.get("language"), "license": source.get("license"),
                                "origin": source.get("origin_urls") or source.get("origin")})
    for text_path in sorted((raw / "engine").glob("*.txt")):
        sources.append({"name": f"engine__{text_path.stem}", "path": text_path, "language": "pt",
                        "license": "gerado pelo projeto (verificado por ferramentas)",
                        "origin": f"pretrain/data_engine/{text_path.stem}.py"})
    return sources


def cap_for(name, caps):
    short = name.split("__", 1)[-1]
    for prefix, limit in caps:
        if short.startswith(prefix) or name.startswith(prefix):
            return limit
    return None


def held_out(document, fraction):
    digest = hashlib.sha256(document[:2000].encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 2**32 < fraction


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", default=str(RAW))
    parser.add_argument("--out", default="pretrain_data")
    parser.add_argument("--heldout-fraction", type=float, default=0.01)
    parser.add_argument("--max-heldout-docs", type=int, default=120)
    parser.add_argument("--caps-json", help='lista JSON [["prefixo", mb], ...] que substitui os tetos padrão')
    parser.add_argument("--no-chat", action="store_true",
                        help="não inclui as conversas do projeto (python/data/*.jsonl)")
    args = parser.parse_args()
    out = ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
    (out / "text").mkdir(parents=True, exist_ok=True)
    (out / "heldout").mkdir(parents=True, exist_ok=True)
    caps = json.loads(Path(args.caps_json).read_text()) if args.caps_json else DEFAULT_CAPS
    manifest, prepare_sources = [], []
    seen = set()
    for source in discover(Path(args.raw)):
        limit = cap_for(source["name"], caps)
        budget = None if limit is None else int(limit * 1_000_000)
        train_bytes = heldout_docs = train_docs = duplicates = 0
        with (out / "text" / f"{source['name']}.txt").open("w", encoding="utf-8") as train, \
                (out / "heldout" / f"{source['name']}.txt").open("w", encoding="utf-8") as heldout:
            for document in read_docs(source["path"]):
                document = document.strip()
                digest = hashlib.sha1(document.encode("utf-8")).digest()
                if len(document) < 200 or digest in seen:
                    duplicates += digest in seen
                    continue
                seen.add(digest)
                if heldout_docs < args.max_heldout_docs and held_out(document, args.heldout_fraction):
                    heldout.write(document + DOC_SEP)
                    heldout_docs += 1
                    continue
                if budget is not None and train_bytes >= budget:
                    continue
                train.write(document + DOC_SEP)
                train_bytes += len(document.encode("utf-8"))
                train_docs += 1
        manifest.append({**{k: v for k, v in source.items() if k != "path"}, "path": str(source["path"].relative_to(ROOT)),
                         "cap_mb": limit, "train_docs": train_docs, "train_bytes": train_bytes,
                         "heldout_docs": heldout_docs, "cross_source_duplicates": duplicates})
        prepare_sources.append({"name": source["name"], "kind": "nul", "glob": str((out / "text" / f"{source['name']}.txt").relative_to(ROOT))
                                if (out / "text").is_relative_to(ROOT) else str(out / "text" / f"{source['name']}.txt"),
                                "mb": 10_000, "license": source.get("license")})
        print(f"{source['name']}: {train_docs} docs de treino ({train_bytes / 1e6:.1f} MB), {heldout_docs} reservados", flush=True)
    if not args.no_chat:
        prepare_sources.append({"name": "projeto-chat", "kind": "chat", "glob": "python/data/*.jsonl", "mb": 50, "license": "autoral"})
    (out / "sources.json").write_text(json.dumps({"sources": prepare_sources}, ensure_ascii=False, indent=2) + "\n")
    (out / "corpus_manifest.json").write_text(json.dumps({"schema": "brasa-corpus/v1", "sources": manifest},
                                                         ensure_ascii=False, indent=2) + "\n")
    total = sum(row["train_bytes"] for row in manifest)
    print(f"total de treino: {total / 1e6:.1f} MB em {len(manifest)} fontes")


if __name__ == "__main__":
    main()
