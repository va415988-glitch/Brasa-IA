"""Cria um índice lexical compacto para recuperar conhecimento local."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

TOKEN_RE = re.compile(r"c\+\+|c#|f#|\.net|[\wÀ-ÖØ-öø-ÿ]+(?:\.[\wÀ-ÖØ-öø-ÿ]+)*", re.I)
ALIASES = {"c++": "cplusplus", "c#": "csharp", "f#": "fsharp", ".net": "dotnet"}
STOP = {"para", "com", "uma", "que", "dos", "das", "por", "como", "the", "and", "this", "that",
        "e", "de", "do", "da", "um", "o", "a", "em", "sobre", "aprenda", "explique", "pesquise",
        "documentacao", "documentation", "oficial", "official", "docs", "tutorial", "exemplos", "examples",
        "referencia", "reference", "linguagem", "framework", "programacao"}


def subject_tokens(text: str) -> list[str]:
    """Preserva nomes com símbolos sem manter uma lista de tecnologias."""
    found = []
    for match in TOKEN_RE.findall(text.lower()):
        term = ALIASES.get(match, match.replace(".", ""))
        if term not in STOP and (len(term) >= 2 or term in {"c", "r"}):
            found.append(term)
    return found


def topic_matches(topic: str, title: str, url: str = "", *, require_all: bool = True) -> bool:
    focus = set(subject_tokens(topic))
    identity = set(subject_tokens(f"{title} {url}"))
    return bool(focus) and (focus <= identity if require_all else bool(focus & identity))


def usable_row(row: dict) -> bool:
    """Não indexa pesquisa proativa cujo título/URL ignora o tema técnico."""
    category = str(row.get("category") or "")
    if category.startswith("learned/"):
        topic = str(row.get("topic") or category.removeprefix("learned/"))
        title = str(row.get("text") or "").split("\n", 1)[0]
        return topic_matches(topic, title, str(row.get("url") or ""))
    if category != "proactive-learning":
        return True
    topic = str(row.get("search_query") or "")
    if not topic:
        return False  # registros antigos sem tema verificável ficam no corpus bruto
    # Consultas genéricas (por exemplo, "explique") geram páginas de
    # dicionário que não são evidência sobre o assunto perguntado. Mantê-las
    # no índice cria exatamente o falso casamento lexical que polui respostas.
    generic = {"explique", "explica", "defina", "o que", "como", "sobre", "aprenda"}
    topic_terms = set(subject_tokens(topic))
    if not topic_terms or topic_terms <= generic:
        return False
    title = str(row.get("text") or "").split("\n", 1)[0]
    return topic_matches(topic, title, str(row.get("url") or ""), require_all=False)


def tokens(text: str) -> list[str]:
    return subject_tokens(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="corpus/clean/knowledge.jsonl")
    parser.add_argument("--output", default="corpus/index/knowledge.json")
    args = parser.parse_args()
    rows = [row for line in Path(args.input).read_text(encoding="utf-8").splitlines() if line.strip()
            if usable_row(row := json.loads(line))]
    postings: dict[str, list[int]] = defaultdict(list)
    doc_freq = Counter()
    for idx, row in enumerate(rows):
        terms = set(tokens(row["text"]))
        for term in terms:
            postings[term].append(idx)
        for term in terms:
            doc_freq[term] += 1
    total = max(1, len(rows))
    idf = {term: math.log((1 + total) / (1 + freq)) + 1 for term, freq in doc_freq.items()}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"documents": rows, "postings": postings, "idf": idf}, ensure_ascii=False), encoding="utf-8")
    print(f"documentos indexados: {len(rows)}")
    print(f"termos indexados: {len(postings)}")
    print(f"índice: {output}")


if __name__ == "__main__":
    main()
