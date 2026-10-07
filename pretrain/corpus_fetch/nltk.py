#!/usr/bin/env python3
"""Corpus de pré-treino da família "nltk" (pacotes do repositório nltk_data).

Baixa, do espelho raw.githubusercontent.com do nltk_data, apenas os corpora com
licença aberta (domínio público / registros oficiais) e grava texto limpo:

    pretrain_data_raw/nltk/<fonte>.txt   documentos separados por "\\x00", UTF-8
    pretrain_data_raw/nltk/manifest.json fontes, licenças, contagens
    pretrain_data_raw/nltk/_zips/        cache dos .zip (verificados por sha256)

Fontes (português e inglês em arquivos separados):
    machado_pt      Machado de Assis, Obra Completa (domínio público)
    europarl_pt     Europarl (amostra NLTK), debates do Parlamento Europeu em português
    udhr_pt         Declaração Universal dos Direitos Humanos (pt-BR e pt-PT)
    gutenberg_en    Seleção do Project Gutenberg (domínio público)
    europarl_en     Europarl (amostra NLTK), inglês
    inaugural_en    Discursos de posse presidenciais dos EUA (obras do governo dos EUA)
    state_union_en  Discursos do Estado da União (obras do governo dos EUA)
    shakespeare_en  Peças de Shakespeare, texto Moby (domínio público), marcação XML removida
    genesis_web_en  Gênesis, World English Bible (domínio público)
    udhr_en         Declaração Universal dos Direitos Humanos (inglês)

Pacotes com licença não comercial, "research only", sem licença declarada ou de
procedência duvidosa são pulados e listados em manifest["skipped"].

Uso:  python -I pretrain/corpus_fetch/nltk.py [--only machado_pt,europarl_pt]
Só usa a biblioteca padrão. Pode ser rodado de novo: zips já baixados (e com
sha256 correto) não são baixados outra vez; os .txt são regenerados.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

BASE = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages"
INDEX_URL = f"{BASE}/index.xml"
REPO = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("BRASA_NLTK_OUT", REPO / "pretrain_data_raw" / "nltk"))
ZIPS = OUT / "_zips"

TIMEOUT = 20
SLEEP = 0.15
RETRIES = 3
MIN_DOC_CHARS = 200
MAX_SOURCE_BYTES = 150 * 1000 * 1000
CHUNK_TARGET = 12_000  # obras longas viram documentos de ~12-20 mil caracteres
CHUNK_HARD = 20_000
DOC_SEP = "\x00"
USER_AGENT = "Brasa-IA corpus fetch (stdlib urllib)"

_last_request = 0.0


# --------------------------------------------------------------------------- rede
def http_get(url: str) -> bytes:
    """GET com timeout, 3 tentativas e ~0,15 s entre requisições."""
    global _last_request
    error: Exception | None = None
    for attempt in range(RETRIES):
        wait = SLEEP - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            error = exc
            if exc.code in (403, 404, 405, 407, 410):
                break  # proxy recusou ou arquivo inexistente: não adianta repetir
        except Exception as exc:  # timeout, conexão resetada etc.
            error = exc
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"falha ao baixar {url}: {error}")


def load_index() -> dict[str, dict]:
    ZIPS.mkdir(parents=True, exist_ok=True)
    cached = ZIPS / "index.xml"
    if cached.exists() and cached.stat().st_size > 10_000:
        data = cached.read_bytes()
    else:
        data = http_get(INDEX_URL)
        cached.write_bytes(data)
    root = ET.fromstring(data)
    return {p.attrib["id"]: dict(p.attrib) for p in root.iter("package")}


def fetch_zip(package: dict) -> zipfile.ZipFile:
    """Baixa (se preciso) o zip do pacote e confere o sha256 publicado no index.xml."""
    target = ZIPS / f"{package['id']}.zip"
    expected = package.get("sha256_checksum")

    def ok(path: Path) -> bool:
        if not path.exists():
            return False
        if not expected:
            return path.stat().st_size > 0
        return hashlib.sha256(path.read_bytes()).hexdigest() == expected

    if not ok(target):
        data = http_get(package["url"])
        part = target.with_suffix(".zip.part")
        part.write_bytes(data)
        if expected and hashlib.sha256(data).hexdigest() != expected:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"sha256 não confere para {package['url']}")
        part.replace(target)
        print(f"[nltk] baixado {package['id']}.zip ({len(data) / 1e6:.1f} MB)")
    else:
        print(f"[nltk] {package['id']}.zip já existe, pulando download")
    return zipfile.ZipFile(target)


# ----------------------------------------------------------------- texto genérico
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‏  ﻿]")


def decode(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def clean_line(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\t", " ").replace("\xad", "")
    text = _CTRL.sub("", text)
    return re.sub(r" {2,}", " ", text).strip()


def normalize(text: str) -> str:
    """NFC, sem caracteres de controle, espaços normalizados, no máximo uma linha em branco."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [clean_line(line) for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def blocks(text: str) -> list[list[str]]:
    """Blocos separados por linha em branco, como listas de linhas (sem \\r)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for raw in re.split(r"\n[ \t\xa0]*\n", text):
        lines = [line.rstrip() for line in raw.split("\n")]
        lines = [line for line in lines if line.strip()]
        if lines:
            out.append(lines)
    return out


def unwrap(lines: list[str]) -> str:
    return " ".join(line.strip() for line in lines)


def chunk(units: list[str], sep: str = "\n\n") -> list[str]:
    """Quebra uma obra longa em documentos de ~12-20 mil caracteres, sempre entre unidades
    (parágrafos ou versos), para que nenhum documento fique gigante."""
    docs, current, size = [], [], 0
    expanded: list[str] = []
    for unit in units:  # unidade maior que o teto (ex.: um canto inteiro sem linha em branco)
        if len(unit) <= CHUNK_HARD:
            expanded.append(unit)
            continue
        pieces = unit.split("\n") if "\n" in unit else re.split(r"(?<=[.!?])\s+", unit)
        inner = "\n" if "\n" in unit else " "
        buf: list[str] = []
        for piece in pieces:
            buf.append(piece)
            if sum(len(b) + 1 for b in buf) >= CHUNK_TARGET // 2:
                expanded.append(inner.join(buf))
                buf = []
        if buf:
            expanded.append(inner.join(buf))
    for unit in expanded:
        if current and size + len(unit) > CHUNK_HARD:
            docs.append(current)
            current, size = [], 0
        current.append(unit)
        size += len(unit) + len(sep)
        if size >= CHUNK_TARGET:
            docs.append(current)
            current, size = [], 0
    if current:
        if docs and size < 2_000:
            docs[-1].extend(current)
        else:
            docs.append(current)
    return [sep.join(d) for d in docs]


# ------------------------------------------------------------------- machado_pt
_INDEX_HEADINGS = {"índice", "indice", "índice geral", "sumário"}
_SOURCE_NOTE = re.compile(r"(textos?|edição)[\s-]*(de\s+)?(fonte|refer[êe]ncia)\b", re.IGNORECASE)
_FOOTREF = re.compile(r"\s?\[(?:[ivxlc]+|\d{1,3})\]", re.IGNORECASE)


def _key(text: str) -> str:
    return re.sub(r"\W+", " ", text.casefold()).strip()


def drop_tables_of_contents(paras: list[str]) -> list[str]:
    """Remove listas "ÍNDICE": a lista termina quando o primeiro item reaparece como título."""
    out, i = [], 0
    while i < len(paras):
        if _key(paras[i]) in _INDEX_HEADINGS and i + 1 < len(paras):
            first = _key(paras[i + 1])
            end = None
            for j in range(i + 2, min(len(paras), i + 400)):
                if _key(paras[j]) == first:
                    end = j
                    break
            if end is not None:
                i = end
                continue
            # sem repetição: descarta apenas a sequência de linhas curtas logo após o título
            j = i + 1
            while j < len(paras) and len(paras[j]) < 80:
                j += 1
            i = j
            continue
        out.append(paras[i])
        i += 1
    return out


def machado_docs(zf: zipfile.ZipFile) -> list[str]:
    docs = []
    for name in sorted(zf.namelist()):
        if not name.endswith(".txt"):
            continue
        poetry = "/poesia/" in name
        text = decode(zf.read(name))
        paras = [clean_line(unwrap(b)) for b in blocks(text)]
        paras = [p for p in paras if p]
        if len(paras) < 3:
            continue
        # Cabeçalho: "Gênero, Título, Ano" / Título / Texto-fonte: ... / Publicado originalmente ...
        title = paras[1]
        body = paras[2:]
        src = next((k for k, p in enumerate(body[:8]) if _SOURCE_NOTE.match(p)), None)
        if src is not None:
            pub = next((k for k in range(src, min(len(body), src + 8))
                        if re.match(r"(publicad|encenad|representad)", body[k], re.I)), None)
            # "Texto-fonte:" pode vir sozinho, com a referência no parágrafo seguinte
            end = pub if pub is not None else (src if len(body[src]) > 25 else src + 1)
            rest = body[end + 1:]
            # continuação da referência bibliográfica (ex.: "Rio de Janeiro: Civilização Brasileira, 1956.")
            while rest and len(rest[0]) < 120 and re.search(
                    r"^(rio de janeiro|são paulo|lisboa|paris)\b.*\d{4}\.?$|^https?://|"
                    r"(aguilar|jackson|garnier|editora|org\.).*\d{4}", rest[0], re.I):
                rest = rest[1:]
            body = body[:src] + rest
        body = drop_tables_of_contents(body)
        # remove chamadas de nota "[iv]" e unifica o travessão ("--" em parte dos arquivos)
        body = [re.sub(r"(?<!-)--(?!-)", "—", _FOOTREF.sub("", p)).strip() for p in body]
        body = [p for p in body if p and not re.fullmatch(r"[\W_]+|https?://\S+", p)]
        if not body:
            continue
        if poetry:
            # Cada parágrafo do arquivo é um verso; títulos em CAIXA ALTA abrem um poema novo.
            units: list[str] = []
            for p in [title] + body:
                letters = [c for c in p if c.isalpha()]
                is_title = letters and all(c.isupper() for c in letters) and len(p) < 80
                if is_title and units:
                    units.append("")  # linha em branco antes do título
                units.append(p)
            joined = "\n".join(units)
            stanzas = [s for s in joined.split("\n\n") if s.strip()]
            docs.extend(chunk(stanzas))
        else:
            docs.extend(chunk([title] + body))
    return docs


# ------------------------------------------------------------------ europarl_*
_NO_SPACE_BEFORE = {",", ".", ";", ":", "?", "!", ")", "]", "}", "%", "»", "...", "…"}
_NO_SPACE_AFTER = {"(", "[", "{", "«", "¿", "¡"}
_CLITIC = re.compile(r"^'(s|re|ve|ll|d|m|t)$|^n't$", re.IGNORECASE)
_TURN = {
    "pt": re.compile(r"^(Senhor(a|es|as)?( e Senhor(a|es|as))? (Presidente|Comissári[oa]|Deputad[oa]s?|"
                     r"Ministr[oa]|Primeiro-Ministro)|Senhoras e Senhores|Caros colegas|Caras colegas|"
                     r"Minhas Senhoras e meus Senhores)\b"),
    "en": re.compile(r"^(Mr|Mrs|Madam|Madame)\.? (President|Commissioner|Minister|Chairman)\b|"
                     r"^(Ladies and gentlemen|Honourable Members|Commissioner ,|President-in-Office)\b"),
}


# "( FR )" = língua original do orador; ". - Muito obrigado" = troca de orador
_LANG_MARK = re.compile(r"\s*(?:-\s*)?\(\s*[A-Z]{2}\s*\)\s*|(?<=[.!?])\s+-\s+(?=[A-ZÀ-Ý])")


def detokenize(line: str) -> str:
    line = re.sub(r"\s*\xad\s*", "-", line)        # hífen gravado como soft hyphen
    line = re.sub(r" ' s\b", " 's", line)          # minute ' s -> minute's
    tokens = line.split()
    out: list[str] = []
    double_open = single_open = False
    glue_next = False
    for idx, tok in enumerate(tokens):
        space = bool(out) and not glue_next
        glue_next = False
        if tok == '"':
            if double_open:
                space = False
            else:
                glue_next = True
            double_open = not double_open
        elif tok == "'" and not single_open and out and out[-1].endswith("s") and \
                re.match(r"^\w", tokens[idx + 1] if idx + 1 < len(tokens) else ""):
            space = False  # genitivo plural: "the Quaestors ' meeting" -> "Quaestors' meeting"
        elif tok == "'":
            if single_open:
                space = False
            else:
                glue_next = True
            single_open = not single_open
        elif tok in _NO_SPACE_BEFORE or _CLITIC.match(tok):
            space = False
        elif tok == "/":
            space = False
            glue_next = True
        if tok in _NO_SPACE_AFTER:
            glue_next = True
        out.append((" " if space else "") + tok)
    text = "".join(out)
    text = re.sub(r"(\d) ([.,]) (\d{3})\b", r"\1\2\3", text)  # 1 . 000 -> 1.000 (raro)
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)  # "etc . ." -> "etc."
    text = re.sub(r"(\w) ([.,;:!?])(?=\s|$)", r"\1\2", text)
    return text.strip()


def europarl_docs(zf: zipfile.ZipFile, folder: str, lang: str) -> list[str]:
    turn = _TURN[lang]
    docs = []
    for name in sorted(zf.namelist()):
        if f"/{folder}/" not in name or name.endswith("/"):
            continue
        text = unicodedata.normalize("NFC", decode(zf.read(name)))
        paras: list[str] = []
        current: list[str] = []

        def flush():
            if current:
                paras.append(" ".join(current))
                current.clear()

        for raw in text.split("\n"):
            # soft hyphen (U+00AD) é o hífen real neste corpus: "quinta ­ feira" -> "quinta-feira"
            raw = re.sub(r"(?<=\w)\s*\xad\s*(?=\w)", "-", raw)
            line = clean_line(re.sub(r"\s*\xad\s*", " - ", raw))  # restante: travessão
            if not line:
                continue
            # "( FR )", "( SV )": língua original do orador = início de nova intervenção
            segments = [s for s in _LANG_MARK.split(line) if s.strip()]
            for k, segment in enumerate(segments):
                if k or _LANG_MARK.match(line):
                    flush()
                # "( O Parlamento , de pé , guarda um minuto de silêncio ) Senhora Presidente , ..."
                note = re.match(r"^(\([^()]{1,250}\))\s+(\S.*)$", segment)
                pieces = [note.group(1), note.group(2)] if note else [segment]
                for piece in pieces:
                    sentence = detokenize(piece)
                    if not sentence or sentence in {"-", "."}:
                        continue
                    new_turn = bool(turn.match(piece)) or sentence.startswith("(")
                    if new_turn or sum(len(s) for s in current) > 1_200:
                        flush()
                    current.append(sentence)
                    if sentence.startswith("(") and sentence.endswith(")"):
                        flush()
        flush()
        docs.extend(chunk(paras))
    return docs


# ------------------------------------------------------------------- gutenberg_en
_GUTENBERG_VERSE = {"blake-poems.txt", "milton-paradise.txt", "whitman-leaves.txt"}
# As três peças de Shakespeare desta seleção estão em grafia do First Folio ("ouer", "Scoena");
# as mesmas peças entram em grafia moderna (texto Moby) na fonte shakespeare_en.
_GUTENBERG_SKIP = {"shakespeare-caesar.txt", "shakespeare-hamlet.txt", "shakespeare-macbeth.txt"}


_GUTENBERG_FOOTER = re.compile(r"^[ \t*]*(end of (the )?project gutenberg|end of this project gutenberg|"
                               r"end of the project gutenberg|\*+ ?end of (the|this) project gutenberg)",
                               re.IGNORECASE | re.MULTILINE)


def gutenberg_docs(zf: zipfile.ZipFile) -> list[str]:
    docs = []
    for name in sorted(zf.namelist()):
        base = name.rsplit("/", 1)[-1]
        if not base.endswith(".txt") or base in _GUTENBERG_SKIP:
            continue
        text = decode(zf.read(name))
        footer = _GUTENBERG_FOOTER.search(text)  # licença/rodapé do Project Gutenberg
        if footer:
            text = text[:footer.start()]
        text = re.sub(r"_([^_\n][^_]{0,200}?)_", r"\1", text)  # _itálico_
        verse_file = base in _GUTENBERG_VERSE
        units = []
        for lines in blocks(text):
            stripped = [line.strip() for line in lines]
            if all(re.fullmatch(r"[*\s.]+", s) for s in stripped):
                continue  # separadores "*  *  *"
            if len(stripped) == 1 and re.fullmatch(r"\[(Illustration|Footnote)[^\]]*\]", stripped[0], re.I):
                continue
            indented = sum(1 for line in lines[1:] if re.match(r"^\s{2,}\S", line))
            short = len(lines) >= 3 and max(len(s) for s in stripped) < 50
            capitals = len(lines) >= 4 and sum(1 for s in stripped[1:] if s[:1].isupper()) >= 0.8 * (len(lines) - 1)
            if verse_file or (len(lines) > 1 and (indented >= max(1, (len(lines) - 1) // 2) or short or capitals)):
                unit = "\n".join(clean_line(s) for s in stripped)
            else:
                unit = clean_line(unwrap(stripped))
            unit = re.sub(r"^\}\s*", "", unit)  # resíduo de marcação em whitman-leaves
            if unit:
                units.append(unit)
        if units and re.fullmatch(r"\[.*\]", units[0]):
            units[0] = units[0][1:-1].strip()  # "[Emma by Jane Austen 1816]" -> título
        docs.extend(chunk(units))
    return docs


# --------------------------------------------------------- discursos (EUA)
def speeches_docs(zf: zipfile.ZipFile) -> list[str]:
    docs = []
    for name in sorted(zf.namelist()):
        if not name.endswith(".txt"):
            continue
        text = decode(zf.read(name))
        units = []
        for lines in blocks(text):
            # nestes arquivos cada linha já é um parágrafo
            units.extend(clean_line(line) for line in lines if clean_line(line))
        title = re.sub(r"\.txt$", "", name.rsplit("/", 1)[-1]).replace("-", " ")
        docs.extend(chunk([title] + units))
    return docs


# --------------------------------------------------------------- shakespeare_en
def shakespeare_docs(zf: zipfile.ZipFile) -> list[str]:
    def text_of(element) -> str:
        return clean_line(" ".join("".join(element.itertext()).split()))

    docs = []
    for name in sorted(zf.namelist()):
        if not name.endswith(".xml"):
            continue
        root = ET.fromstring(zf.read(name))
        units = [text_of(root.find("TITLE"))]
        personae = root.find("PERSONAE")
        if personae is not None:
            cast = [text_of(p) for p in personae.iter("PERSONA")]
            units.append("Dramatis Personae\n" + "\n".join(cast))
        for act in root.iter("ACT"):
            units.append(text_of(act.find("TITLE")))
            for scene in act.iter("SCENE"):
                for node in scene:
                    if node.tag == "TITLE":
                        units.append(text_of(node))
                    elif node.tag == "STAGEDIR":
                        units.append(text_of(node))
                    elif node.tag == "SPEECH":
                        speakers = " and ".join(text_of(s) for s in node.findall("SPEAKER"))
                        lines = []
                        for child in node:
                            if child.tag == "LINE":
                                lines.append(text_of(child))
                            elif child.tag == "STAGEDIR":
                                lines.append(f"[{text_of(child)}]")
                        lines = [line for line in lines if line]
                        if lines:
                            units.append(f"{speakers}:\n" + "\n".join(lines))
        docs.extend(chunk([u for u in units if u]))
    return docs


# -------------------------------------------------------------- genesis / udhr
def genesis_web_docs(zf: zipfile.ZipFile) -> list[str]:
    text = decode(zf.read("genesis/english-web.txt"))
    verses: list[str] = []
    for raw in text.splitlines():
        line = clean_line(raw)
        if not line:
            continue
        if verses and line[0].islower():
            verses[-1] += " " + line  # continuação de versículo quebrado
        else:
            verses.append(line)
    # agrupa versículos em parágrafos de ~8 linhas para um texto corrido legível
    paras = [" ".join(verses[i:i + 8]) for i in range(0, len(verses), 8)]
    return chunk(["Genesis (World English Bible)"] + paras)


def udhr_docs(zf: zipfile.ZipFile, files: list[str]) -> list[str]:
    docs = []
    for name in files:
        text = decode(zf.read(name))
        units = [clean_line(line) for line in text.splitlines() if clean_line(line)]
        docs.extend(chunk(units, sep="\n"))
    return docs


# ---------------------------------------------------------------------- fontes
EUROPARL_BASIS = (
    "Verbatim reports of European Parliament plenary debates (official public parliamentary records). "
    "Europarl corpus release (Koehn, statmt.org/europarl): 'We are not aware of any copyright restrictions "
    "of the material.' The European Parliament's legal notice authorises reuse of its documents with "
    "acknowledgement of the source (EP reuse policy, CC BY 4.0). NLTK index.xml declares no licence."
)

SOURCES = [
    {
        "name": "machado_pt", "language": "pt", "package": "machado",
        "license": "Public Domain",
        "license_basis": "Machado de Assis died in 1908 (all works and his translations are public domain).",
        "build": machado_docs,
    },
    {
        "name": "europarl_pt", "language": "pt", "package": "europarl_raw",
        "license": "CC-BY-4.0 (European Parliament reuse policy; official parliamentary records)",
        "license_basis": EUROPARL_BASIS,
        "build": lambda zf: europarl_docs(zf, "portuguese", "pt"),
    },
    {
        "name": "udhr_pt", "language": "pt", "package": "udhr2",
        "license": "Public Domain",
        "license_basis": "UN Universal Declaration of Human Rights (por_BR, por_PT).",
        "build": lambda zf: udhr_docs(zf, ["udhr2/por_BR.txt", "udhr2/por_PT.txt"]),
    },
    {
        "name": "gutenberg_en", "language": "en", "package": "gutenberg",
        "license": "Public Domain",
        "license_basis": "Project Gutenberg public-domain etexts. "
                         "First Folio Shakespeare files skipped (modern-spelling versions are in shakespeare_en).",
        "build": gutenberg_docs,
    },
    {
        "name": "europarl_en", "language": "en", "package": "europarl_raw",
        "license": "CC-BY-4.0 (European Parliament reuse policy; official parliamentary records)",
        "license_basis": EUROPARL_BASIS,
        "build": lambda zf: europarl_docs(zf, "english", "en"),
    },
    {
        "name": "inaugural_en", "language": "en", "package": "inaugural",
        "license": "Public Domain",
        "license_basis": "US presidential inaugural addresses (works of the US Government, 17 U.S.C. 105).",
        "build": speeches_docs,
    },
    {
        "name": "state_union_en", "language": "en", "package": "state_union",
        "license": "Public Domain",
        "license_basis": "US State of the Union addresses (works of the US Government, 17 U.S.C. 105).",
        "build": speeches_docs,
    },
    {
        "name": "shakespeare_en", "language": "en", "package": "shakespeare",
        "license": "Public Domain",
        "license_basis": "Files state 'Text placed in the public domain by Moby Lexical Tools, 1992 ... may be freely "
                         "copied and distributed worldwide'; only the play text is kept (Bosak XML markup dropped).",
        "build": shakespeare_docs,
    },
    {
        "name": "genesis_web_en", "language": "en", "package": "genesis",
        "license": "Public Domain",
        "license_basis": "World English Bible is dedicated to the public domain. "
                         "KJV Genesis skipped (already inside gutenberg_en bible-kjv); non-English files skipped.",
        "build": genesis_web_docs,
    },
    {
        "name": "udhr_en", "language": "en", "package": "udhr2",
        "license": "Public Domain",
        "license_basis": "UN Universal Declaration of Human Rights (eng).",
        "build": lambda zf: udhr_docs(zf, ["udhr2/eng.txt"]),
    },
]

# Pacotes avaliados e deixados de fora (registrados no manifest e no relatório).
SKIPPED = [
    {"package": "mac_morpho", "reason": "Only 'Distributed with permission of NILC' (index.xml and README); no open "
                                        "licence for redistribution/derivatives; text is Folha de S.Paulo 1994 news."},
    {"package": "floresta", "reason": "index.xml: 'Non-commercial use only' (NC)."},
    {"package": "genesis (portuguese.txt)", "reason": "Brazilian translation from bibliaonline.com.br in modern "
                                                     "orthography (apparently Almeida Revista e Corrigida, an "
                                                     "SBB-copyrighted edition) despite the package-wide 'public domain' label."},
    {"package": "universal_treebanks_v20", "reason": "CC BY-NC-SA 3.0 (NC); also contains pt-BR."},
    {"package": "brown / brown_tei", "reason": "index.xml: 'May be used for non-commercial purposes.'"},
    {"package": "semcor", "reason": "Princeton licence is permissive, but the text is the Brown Corpus, "
                                    "which NLTK distributes as non-commercial only."},
    {"package": "movie_reviews", "reason": "Index says CC BY 4.0 but README only 'with permission from the authors'; "
                                           "reviews are by third-party IMDb authors and the text is lower-cased/tokenized."},
    {"package": "masc_tagged", "reason": "Custom terms ('education, research, and development, including commercial "
                                         "development'), not a recognised open licence; tokenized and POS-tagged."},
    {"package": "switchboard", "reason": "Open Content License (not in the allowed list); 36 phone-call transcripts only."},
    {"package": "webtext / abc / ieer / conll2000 / conll2002 / comtrans / smultron / problem_reports / rte / qc",
     "reason": "No licence declared in index.xml (scraped web/news/script text)."},
    {"package": "reuters", "reason": "'for research purposes only'."},
    {"package": "nps_chat / treebank / dependency_treebank / timit / sinica_treebank / framenet_v15 / mte_teip5 / conll2007",
     "reason": "Non-commercial and/or no-derivatives licences."},
    {"package": "twitter_samples", "reason": "Twitter Developer Agreement, not an open licence."},
    {"package": "cess_cat / cess_esp / alpino / indian / pil / pe08 / senseval / propbank / nombank",
     "reason": "Research-citation or 'distributed with permission' terms only; not Portuguese/English prose."},
    {"package": "framenet_v17", "reason": "CC BY 3.0 annotations, but full-text documents come from mixed-provenance "
                                          "sources (WSJ/PropBank, NTI, ...) and LU examples are isolated BNC sentences."},
    {"package": "biocreative_ppi", "reason": "Public domain, but only POS/gene-tagged single MEDLINE sentences "
                                             "(no running documents)."},
    {"package": "wordnet / omw / extended_omw / words / names / stopwords / cmudict / swadesh / panlex_swadesh / crubadan",
     "reason": "Lexical resources (word lists, glosses, n-grams), not natural running text."},
]


# ------------------------------------------------------------------------ main
def finalize(docs: list[str]) -> tuple[list[str], int]:
    seen: set[str] = set()
    kept, total = [], 0
    for doc in docs:
        doc = normalize(doc).replace(DOC_SEP, "")
        if len(doc) < MIN_DOC_CHARS:
            continue
        digest = hashlib.sha1(doc.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        size = len(doc.encode("utf-8")) + 1
        if total + size > MAX_SOURCE_BYTES:
            break
        seen.add(digest)
        kept.append(doc)
        total += size
    return kept, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", help="lista de fontes separadas por vírgula")
    args = parser.parse_args()
    wanted = set(args.only.split(",")) if args.only else None

    OUT.mkdir(parents=True, exist_ok=True)
    failures: list[dict] = []
    try:
        index = load_index()
    except Exception as exc:
        print(f"[nltk] ERRO: index.xml inacessível: {exc}")
        return 1

    manifest_path = OUT / "manifest.json"
    previous = {}
    if manifest_path.exists():
        try:
            previous = {s["name"]: s for s in json.loads(manifest_path.read_text("utf-8")).get("sources", [])}
        except Exception:
            previous = {}

    results = []
    zips: dict[str, zipfile.ZipFile] = {}
    for source in SOURCES:
        if wanted and source["name"] not in wanted:
            if source["name"] in previous:
                results.append(previous[source["name"]])
            continue
        package = index.get(source["package"])
        if not package:
            failures.append({"what": source["name"], "reason": "pacote ausente do index.xml"})
            continue
        try:
            if source["package"] not in zips:
                zips[source["package"]] = fetch_zip(package)
            docs = source["build"](zips[source["package"]])
            docs, total = finalize(docs)
        except Exception as exc:
            print(f"[nltk] {source['name']}: ERRO {type(exc).__name__}: {exc}")
            failures.append({"what": source["name"], "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if not docs:
            failures.append({"what": source["name"], "reason": "nenhum documento após a limpeza"})
            continue
        target = OUT / f"{source['name']}.txt"
        part = target.with_suffix(".txt.part")
        with part.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(DOC_SEP.join(docs) + DOC_SEP)
        part.replace(target)
        entry = {
            "name": source["name"],
            "language": source["language"],
            "license": source["license"],
            "license_basis": source["license_basis"]
            + (f" [index.xml license attribute: {package['license']!r}]" if package.get("license") else ""),
            "origin_urls": [INDEX_URL, package["url"]],
            "documents": len(docs),
            "bytes": target.stat().st_size,
            "text_path": str(target),
        }
        results.append(entry)
        print(f"[nltk] {source['name']}: {len(docs)} docs, {entry['bytes'] / 1e6:.2f} MB -> {target}")

    manifest = {
        "family": "nltk",
        "sources": results,
        "skipped": SKIPPED,
        "failures": failures,
        "generated_by": "pretrain/corpus_fetch/nltk.py",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[nltk] manifest: {manifest_path}")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
