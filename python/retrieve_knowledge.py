"""Busca lexical no acervo local, sem modelo externo."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

TOKEN_RE = re.compile(r"[\wÀ-ÖØ-öø-ÿ]{2,}", re.UNICODE)


def tokens(text: str) -> list[str]:
    return [item.lower() for item in TOKEN_RE.findall(text)]


def search(index: dict, query: str, limit: int = 5) -> list[dict]:
    terms = tokens(query)
    scores: dict[int, float] = {}
    for term in terms:
        weight = index["idf"].get(term, 0.0)
        for doc_id in index["postings"].get(term, []):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [{"score": round(score, 3), **index["documents"][doc_id]} for doc_id, score in ranked]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--index", default="corpus/index/knowledge.json")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    for row in search(index, args.query, args.limit):
        print(f"[{row['score']}] {row['id']} ({row['category']})")
        print(row["text"][:500].replace("\n", " "))
        print()


if __name__ == "__main__":
    main()
