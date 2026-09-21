"""Coleta um lote pequeno de documentação oficial de programação.

O script usa uma lista explícita de URLs, limite de bytes e deduplicação por
hash. Ele não rastreia links recursivamente e não baixa sites inteiros.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from html import unescape
from pathlib import Path
from urllib.request import Request, urlopen


SOURCES = [
    ("python", "PSF License", "https://docs.python.org/3/tutorial/introduction.html"),
    ("python", "PSF License", "https://docs.python.org/3/tutorial/controlflow.html"),
    ("python", "PSF License", "https://docs.python.org/3/tutorial/errors.html"),
    ("python", "PSF License", "https://docs.python.org/3/tutorial/classes.html"),
    ("rust", "MIT/Apache-2.0", "https://doc.rust-lang.org/book/ch04-00-understanding-ownership.html"),
    ("rust", "MIT/Apache-2.0", "https://doc.rust-lang.org/book/ch06-00-enums.html"),
    ("rust", "MIT/Apache-2.0", "https://doc.rust-lang.org/book/ch09-00-error-handling.html"),
    ("rust", "MIT/Apache-2.0", "https://doc.rust-lang.org/book/ch16-00-concurrency.html"),
    ("web", "CC-BY-SA-2.5", "https://developer.mozilla.org/en-US/docs/Learn_web_development/Core/Scripting/What_is_JavaScript"),
    ("web", "CC-BY-SA-2.5", "https://developer.mozilla.org/en-US/docs/Learn_web_development/Core/Structuring_content/HTML_basics"),
    ("web", "CC-BY-SA-2.5", "https://developer.mozilla.org/en-US/docs/Learn_web_development/Core/Styling_basics/Getting_started"),
]


def html_text(raw: str) -> str:
    raw = re.sub(r"<(script|style|nav|header|footer|aside)\b[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"</(p|h[1-6]|li|pre|section|article)>", "\n\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    text = unescape(raw).replace("\r", "")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def fetch(url: str) -> str:
    request = Request(url, headers={"User-Agent": "IA-Local-do-Zero/0.1 (programming corpus; contact local)"})
    with urlopen(request, timeout=15) as response:
        data = response.read(1_500_000)
        return data.decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="corpus/raw/programming_docs.jsonl")
    parser.add_argument("--max-pages", type=int, default=len(SOURCES))
    parser.add_argument("--max-bytes", type=int, default=12 * 1024 * 1024)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    written = 0
    accepted = 0
    with output.open("w", encoding="utf-8") as file:
        for category, license_name, url in SOURCES[:args.max_pages]:
            try:
                text = html_text(fetch(url))
                if len(text) < 500:
                    print(f"rejeitado: pouco conteúdo: {url}")
                    continue
                digest = hashlib.sha256(text.encode()).hexdigest()
                if digest in seen:
                    continue
                record = {"text": text, "source": url.split('/')[2], "license": license_name,
                          "language": "en", "category": f"programming/{category}", "url": url,
                          "sha256": digest}
                encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode()
                if written + len(encoded) > args.max_bytes:
                    break
                file.write(encoded.decode()); file.flush()
                seen.add(digest); written += len(encoded); accepted += 1
                print(f"aceito: {category} {url}")
            except Exception as error:
                print(f"falha: {url}: {error}")
            time.sleep(0.5)
    print(f"páginas aceitas: {accepted}; bytes: {written}; saída: {output}")


if __name__ == "__main__":
    main()
