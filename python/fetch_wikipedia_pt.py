"""Baixa um lote pequeno e rastreável de artigos da Wikipédia em português.

É uma coleta inicial, não um dump completo. Cada registro leva origem, idioma,
categoria e licença para o pipeline de corpus.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API = "https://pt.wikipedia.org/w/api.php"
TOPICS = [
    "programação Python Rust algoritmos",
    "matemática ciência computação",
    "história geografia cultura mundo",
    "saúde educação economia sociedade",
]


def fetch(topic: str, limit: int) -> list[dict]:
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": topic,
        "gsrnamespace": 0,
        "gsrlimit": limit,
        "prop": "extracts|info",
        "inprop": "url",
        "explaintext": 1,
        "exlimit": limit,
        "format": "json",
        "formatversion": 2,
    }
    request = Request(API + "?" + urlencode(params), headers={"User-Agent": "IA-Local-do-Zero/0.1 (local corpus builder)"})
    with urlopen(request, timeout=10) as response:
        payload = json.load(response)
    return payload.get("query", {}).get("pages", [])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="corpus/raw/wikipedia_pt.jsonl")
    parser.add_argument("--limit-per-topic", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=80)
    parser.add_argument("--max-bytes", type=int, default=25 * 1024 * 1024)
    parser.add_argument("--append", action="store_true", help="preserva o lote existente e adiciona apenas páginas novas")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if args.append and output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
                if record.get("pageid"):
                    seen.add(record["pageid"])
                elif record.get("sha256"):
                    seen.add(record["sha256"])
            except json.JSONDecodeError:
                continue
    mode = "a" if args.append else "w"
    written_bytes = output.stat().st_size if args.append and output.exists() else 0
    accepted = 0
    with output.open(mode, encoding="utf-8") as file:
        for topic in TOPICS:
            if accepted >= args.max_pages or written_bytes >= args.max_bytes:
                break
            try:
                pages = fetch(topic, args.limit_per_topic)
            except Exception as error:
                print(f"falha em {topic!r}: {error}")
                continue
            for page in pages:
                text = (page.get("extract") or "").strip()
                title = page.get("title", "").strip()
                if not title or len(text) < 400 or page.get("pageid") in seen:
                    continue
                record = {
                    "text": f"{title}\n\n{text}",
                    "source": "wikipedia-pt",
                    "license": "CC-BY-SA/GFDL (verify current terms)",
                    "language": "pt-BR",
                    "category": "encyclopedic",
                    "url": page.get("fullurl", f"https://pt.wikipedia.org/?curid={page.get('pageid')}"),
                    "pageid": page.get("pageid"),
                }
                record["sha256"] = hashlib.sha256(record["text"].encode("utf-8")).hexdigest()
                encoded = json.dumps(record, ensure_ascii=False) + "\n"
                if written_bytes + len(encoded.encode("utf-8")) > args.max_bytes:
                    break
                seen.add(page.get("pageid")); seen.add(record["sha256"])
                file.write(encoded); file.flush()
                written_bytes += len(encoded.encode("utf-8"))
                accepted += 1
                if accepted >= args.max_pages:
                    break
            print(f"{topic}: {len(pages)} resultados")
            time.sleep(0.5)
    print(f"artigos gravados: {accepted}")
    print(f"arquivo: {output}")


if __name__ == "__main__":
    main()
