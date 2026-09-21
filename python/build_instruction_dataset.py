"""Gera exemplos supervisionados de conhecimento a partir do acervo local.

Os exemplos são derivados apenas de documentos já presentes no corpus; não
buscam dados externos nem inventam fontes.
"""

import argparse
import json
import re
from urllib.parse import unquote, urlparse
from pathlib import Path


def clean_title(text, url=""):
    title = next((line.strip() for line in text.splitlines() if line.strip()), "")
    title = re.sub(r"^#+\s*", "", title)
    title = re.sub(r"\s+", " ", title).strip(" .:")
    if len(title) > 120 or title.startswith(("==", "##")) or title.count("?") > 1:
        path_title = unquote(urlparse(url).path.rsplit("/", 1)[-1]).replace("_", " ")
        title = re.sub(r"\s+", " ", path_title).strip(" .:")
    if len(title) > 120 or title.startswith(("==", "##")) or title.count("?") > 1:
        return ""
    return title


def answer_paragraphs(text, limit=1200):
    paragraphs = [re.sub(r"\s+", " ", part).strip() for part in text.split("\n\n") if part.strip()]
    body = [part for part in paragraphs[1:] if not part.startswith(("==", "##"))]
    return " ".join(body[:2])[:limit].strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge", default="corpus/clean/knowledge.jsonl")
    parser.add_argument("--behavior", default="python/data/combined.jsonl")
    parser.add_argument("--output", default="python/data/behavior_augmented.jsonl")
    args = parser.parse_args()

    rows = []
    seen = set()
    for raw in Path(args.behavior).read_text(encoding="utf-8").splitlines():
        if raw.strip():
            rows.append(json.loads(raw))
    original_count = len(rows)

    for raw in Path(args.knowledge).read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        doc = json.loads(raw)
        title = clean_title(doc.get("text", ""), doc.get("url", ""))
        answer = answer_paragraphs(doc.get("text", ""))
        if not title or len(answer) < 80:
            continue
        question = f"O que é {title}?"
        key = question.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "messages": [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ],
            "tools_used": [],
            "expected_sources": [],
            "derived_from": doc.get("id", "local-corpus"),
        })

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"comportamento original: {original_count}")
    print(f"exemplos totais: {len(rows)}")
    print(f"saída: {output}")


if __name__ == "__main__":
    main()
