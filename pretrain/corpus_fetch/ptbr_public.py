#!/usr/bin/env python3
"""Corpus de pré-treino da família "ptbr_public": texto geral, literário e jurídico em
português, de domínio público ou com licença aberta, hospedado como arquivos em
repositórios do GitHub (raw.githubusercontent.com / git smart-HTTP) e em pacotes do PyPI.

Saída:
    pretrain_data_raw/ptbr_public/<fonte>.txt     documentos separados por "\\x00", UTF-8
    pretrain_data_raw/ptbr_public/manifest.json   fontes, licenças, contagens, falhas
    pretrain_data_raw/ptbr_public/_cache/         downloads brutos (reaproveitados ao rodar de novo)

Descoberta de arquivos (sem api.github.com): índices "raw" (listas de arquivos,
catálogos, JSON de índice) e, quando não há índice, a listagem da árvore do
repositório pelo protocolo git smart-HTTP (github.com/<dono>/<repo>.git, a mesma
rota usada por "git clone"), implementada aqui só com a biblioteca padrão. Os
arquivos em si vêm de raw.githubusercontent.com fixados no commit listado.

Uso:  python -I pretrain/corpus_fetch/ptbr_public.py [--only fonte1,fonte2] [--list]
Só usa a biblioteca padrão. Pode ser rodado de novo: tudo o que já foi baixado
fica em _cache/ e não é baixado outra vez; os .txt são regenerados.
"""
from __future__ import annotations

import argparse
import bz2
import gzip
import hashlib
import html
import io
import json
import lzma
import os
import re
import struct
import sys
import tarfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("BRASA_PTBR_PUBLIC_OUT", REPO / "pretrain_data_raw" / "ptbr_public"))
CACHE = OUT / "_cache"

TIMEOUT = 20
SLEEP = 0.15
RETRIES = 3
MIN_DOC_CHARS = 200
MAX_SOURCE_BYTES = 150 * 1000 * 1000
CHUNK_TARGET = 12_000  # obras longas viram documentos de ~12-20 mil caracteres
CHUNK_HARD = 20_000
DOC_SEP = "\x00"
USER_AGENT = "Brasa-IA corpus fetch (python urllib)"
RAW = "https://raw.githubusercontent.com"

_last_request = 0.0
LOG_PREFIX = "[ptbr_public]"


def log(*parts) -> None:
    print(LOG_PREFIX, *parts, flush=True)


# =========================================================================== rede
class NotFound(Exception):
    pass


def http(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = TIMEOUT) -> bytes:
    """GET/POST com timeout de 20 s, 3 tentativas e ~0,15 s entre requisições."""
    global _last_request
    error: Exception | None = None
    for attempt in range(RETRIES):
        wait = SLEEP - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                raise NotFound(url) from exc
            if exc.code in (401, 403, 407, 451):  # política do proxy ou acesso negado: não insistir
                raise
            error = exc
        except Exception as exc:  # timeouts, conexões cortadas
            error = exc
        time.sleep(1.5 * (attempt + 1))
    assert error is not None
    raise error


def _cache_path(key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._/-]", "_", key)
    return CACHE / safe


def cached(url: str, key: str) -> bytes:
    """Baixa uma URL uma única vez (cache em _cache/<key>); 404 também fica registrado."""
    path = _cache_path(key)
    if path.exists():
        return path.read_bytes()
    missing = path.with_name(path.name + ".404")
    if missing.exists():
        raise NotFound(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = http(url)
    except NotFound:
        missing.write_bytes(b"")
        raise
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data


def raw_url(owner: str, repo: str, ref: str, path: str) -> str:
    return f"{RAW}/{owner}/{repo}/{ref}/{urllib.parse.quote(path)}"


def raw_cached(owner: str, repo: str, ref: str, path: str) -> bytes:
    return cached(raw_url(owner, repo, ref, path), f"raw/{owner}/{repo}/{ref}/{path}")


# ------------------------------------------------------- git smart-HTTP (só stdlib)
_GIT_TYPES = {1: b"commit", 2: b"tree", 3: b"blob", 4: b"tag"}


def _pkt(line: bytes) -> bytes:
    return b"%04x" % (len(line) + 4) + line


def _iter_pkts(data: bytes):
    pos = 0
    while pos + 4 <= len(data):
        size = int(data[pos:pos + 4], 16)
        if size == 0:
            pos += 4
            yield None
            continue
        yield data[pos + 4:pos + size]
        pos += size


def _git_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}.git"


def git_refs(owner: str, repo: str) -> tuple[dict, str | None]:
    data = http(_git_url(owner, repo) + "/info/refs?service=git-upload-pack")
    refs, head = {}, None
    for line in _iter_pkts(data):
        if line is None or line.startswith(b"#"):
            continue
        line = line.rstrip(b"\n")
        if b"\0" in line:
            line, caps = line.split(b"\0", 1)
            for cap in caps.split():
                if cap.startswith(b"symref=HEAD:"):
                    head = cap.split(b":", 1)[1].decode()
        sha, name = line.split(b" ", 1)
        refs[name.decode()] = sha.decode()
    return refs, head


def _upload_pack(owner: str, repo: str, wants: list[str], deepen: int | None = None,
                 blob_filter: str | None = None) -> bytes:
    caps = b" side-band-64k ofs-delta no-progress" + (b" shallow" if deepen else b"") + (b" filter" if blob_filter else b"")
    body = b"".join(_pkt(b"want " + w.encode() + (caps if i == 0 else b"") + b"\n") for i, w in enumerate(wants))
    if deepen:
        body += _pkt(b"deepen %d\n" % deepen)
    if blob_filter:
        body += _pkt(b"filter " + blob_filter.encode() + b"\n")
    body += b"0000" + _pkt(b"done\n")
    response = http(_git_url(owner, repo) + "/git-upload-pack", data=body,
                    headers={"Content-Type": "application/x-git-upload-pack-request",
                             "Accept": "application/x-git-upload-pack-result"})
    pack = bytearray()
    for line in _iter_pkts(response):
        if line is None:
            continue
        if line[:1] == b"\x01":
            pack += line[1:]
        elif line[:1] == b"\x03":
            raise RuntimeError("git: " + line[1:].decode("utf-8", "replace"))
    return bytes(pack)


def _apply_delta(base: bytes, delta: bytes) -> bytes:
    pos = 0

    def varint() -> int:
        nonlocal pos
        shift = value = 0
        while True:
            byte = delta[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                return value

    varint()
    varint()
    out = bytearray()
    while pos < len(delta):
        op = delta[pos]
        pos += 1
        if op & 0x80:
            offset = size = 0
            for i in range(4):
                if op & (1 << i):
                    offset |= delta[pos] << (8 * i)
                    pos += 1
            for i in range(3):
                if op & (1 << (4 + i)):
                    size |= delta[pos] << (8 * i)
                    pos += 1
            out += base[offset:offset + (size or 0x10000)]
        elif op:
            out += delta[pos:pos + op]
            pos += op
        else:
            raise ValueError("delta inválido")
    return bytes(out)


def _parse_pack(pack: bytes) -> dict[str, tuple[int, bytes]]:
    if pack[:4] != b"PACK":
        raise ValueError("resposta git sem packfile")
    count = int.from_bytes(pack[8:12], "big")
    pos, by_offset, objects = 12, {}, {}
    view = memoryview(pack)
    for _ in range(count):
        start = pos
        byte = pack[pos]
        pos += 1
        kind = (byte >> 4) & 7
        while byte & 0x80:
            byte = pack[pos]
            pos += 1
        base = None
        if kind == 6:  # OFS_DELTA
            byte = pack[pos]
            pos += 1
            offset = byte & 0x7F
            while byte & 0x80:
                byte = pack[pos]
                pos += 1
                offset = ((offset + 1) << 7) | (byte & 0x7F)
            base = by_offset[start - offset]
        elif kind == 7:  # REF_DELTA
            base = objects[pack[pos:pos + 20].hex()]
            pos += 20
        inflater = zlib.decompressobj()
        parts = []
        while not inflater.eof:
            piece = view[pos:pos + 65536]
            if not piece:
                raise ValueError("packfile truncado")
            parts.append(inflater.decompress(piece))
            pos += len(piece)
        pos -= len(inflater.unused_data)
        data = b"".join(parts)
        if base is not None:
            kind, data = base[0], _apply_delta(base[1], data)
        by_offset[start] = (kind, data)
        sha = hashlib.sha1(_GIT_TYPES[kind] + b" %d\0" % len(data) + data).hexdigest()
        objects[sha] = (kind, data)
    return objects


def _parse_tree(data: bytes):
    pos = 0
    while pos < len(data):
        space = data.index(b" ", pos)
        nul = data.index(b"\0", space)
        yield data[pos:space].decode(), data[space + 1:nul].decode("utf-8", "replace"), data[nul + 1:nul + 21].hex()
        pos = nul + 21


def git_list(owner: str, repo: str, branch: str | None = None) -> tuple[str, dict[str, str]]:
    """Lista (commit, {caminho: sha do blob}) de um repositório do GitHub sem baixar conteúdo
    (fetch raso, profundidade 1, filtro blob:none). Resultado fica em cache por branch."""
    key = _cache_path(f"git/{owner}__{repo}__{branch or 'HEAD'}.json")
    if key.exists():
        saved = json.loads(key.read_text("utf-8"))
        return saved["commit"], saved["files"]
    refs, head = git_refs(owner, repo)
    ref = f"refs/heads/{branch}" if branch else (head or "HEAD")
    commit = refs.get(ref) or refs["HEAD"]
    objects = _parse_pack(_upload_pack(owner, repo, [commit], deepen=1, blob_filter="blob:none"))
    tree = objects[commit][1].split(b"\n", 1)[0].split()[1].decode()
    files: dict[str, str] = {}

    def walk(sha: str, prefix: str) -> None:
        for mode, name, child in _parse_tree(objects[sha][1]):
            if mode.startswith("40"):
                walk(child, prefix + name + "/")
            elif mode.startswith("100"):
                files[prefix + name] = child

    walk(tree, "")
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps({"commit": commit, "files": files}), "utf-8")
    return commit, files


def git_blobs(owner: str, repo: str, shas: list[str], batch: int = 400) -> dict[str, bytes]:
    """Baixa blobs por sha em lotes (um packfile por lote); cache por sha em _cache/blobs/."""
    out: dict[str, bytes] = {}
    todo = []
    for sha in dict.fromkeys(shas):
        path = _cache_path(f"blobs/{sha[:2]}/{sha}")
        if path.exists():
            out[sha] = path.read_bytes()
        else:
            todo.append(sha)
    for i in range(0, len(todo), batch):
        group = todo[i:i + batch]
        try:
            objects = _parse_pack(_upload_pack(owner, repo, group))
        except Exception as exc:
            log(f"  git blobs {owner}/{repo}: lote {i // batch} falhou ({type(exc).__name__}: {exc})")
            continue
        for sha, (kind, data) in objects.items():
            if kind != 3:
                continue
            path = _cache_path(f"blobs/{sha[:2]}/{sha}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            out[sha] = data
    return out


# ================================================================ limpeza de texto
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​‎‏﻿]")


def decode(data: bytes, prefer: str = "utf-8") -> str:
    for encoding in (prefer, "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1")


def clean_line(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\t", " ").replace("\xad", "")
    text = _CTRL.sub("", text)
    return re.sub(r" {2,}", " ", text).strip()


def normalize(text: str) -> str:
    """NFC, sem caracteres de controle, espaços normalizados, no máximo uma linha em branco."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = "\n".join(clean_line(line) for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_blocks(text: str) -> list[list[str]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for raw in re.split(r"\n[ \t\xa0]*\n", text):
        lines = [line.rstrip() for line in raw.split("\n") if line.strip()]
        if lines:
            out.append(lines)
    return out


def join_wrapped(lines: list[str]) -> str:
    """Junta linhas quebradas de um parágrafo; hífen no fim da linha é mantido sem espaço."""
    text = lines[0].strip()
    for line in lines[1:]:
        line = line.strip()
        if text.endswith("-") and not text.endswith("--") and line[:1].islower():
            text += line
        else:
            text += " " + line
    return text


def looks_like_prose(lines: list[str]) -> bool:
    if len(lines) <= 1:
        return True
    lengths = sorted(len(line.strip()) for line in lines[:-1])
    return lengths[len(lengths) // 2] >= 55


def chunk(units: list[str], sep: str = "\n\n") -> list[str]:
    """Quebra uma obra longa em documentos de ~12-20 mil caracteres, sempre entre unidades."""
    expanded: list[str] = []
    for unit in units:
        if len(unit) <= CHUNK_HARD:
            expanded.append(unit)
            continue
        pieces = unit.split("\n") if "\n" in unit else re.split(r"(?<=[.!?;])\s+", unit)
        inner = "\n" if "\n" in unit else " "
        buf: list[str] = []
        size = 0
        for piece in pieces:
            buf.append(piece)
            size += len(piece) + 1
            if size >= CHUNK_TARGET // 2:
                expanded.append(inner.join(buf))
                buf, size = [], 0
        if buf:
            expanded.append(inner.join(buf))
    docs, current, size = [], [], 0
    for unit in expanded:
        if current and size + len(unit) > CHUNK_HARD:
            docs.append(sep.join(current))
            current, size = [], 0
        current.append(unit)
        size += len(unit) + len(sep)
        if size >= CHUNK_TARGET:
            docs.append(sep.join(current))
            current, size = [], 0
    if current:
        if docs and size < 2_000:
            docs[-1] = docs[-1] + sep + sep.join(current)
        else:
            docs.append(sep.join(current))
    return docs


def strip_markdown(text: str) -> str:
    """Remove a sintaxe de markdown (imagens, links, ênfase, tabelas, citações, cabeçalhos)."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>\n]{1,200}>", " ", text)
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"^\s*(?:>\s*)+", "", line)  # citações "> >"
        line = re.sub(r"^\s*#{1,6}\s+", "", line)
        if re.fullmatch(r"[\s|:\-=_*~]*", line) and line.strip():  # separadores de tabela / réguas
            continue
        line = line.strip().strip("|").strip()
        line = re.sub(r"\s*\|\s*", " ", line)
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\*{1,3}|~~|(?<!\w)__(?!\w)", "", text)
    return text


# ===================================================================== escrita
class Collector:
    """Acumula documentos de uma fonte: normaliza, descarta < 200 caracteres,
    remove duplicatas exatas e respeita o teto de 150 MB."""

    def __init__(self, name: str):
        self.name = name
        self.docs: list[str] = []
        self.seen: set[str] = set()
        self.bytes = 0
        self.full = False
        self.dropped_short = 0
        self.dropped_dup = 0

    def add(self, doc: str) -> bool:
        if self.full:
            return False
        doc = normalize(doc).replace(DOC_SEP, "")
        if len(doc) < MIN_DOC_CHARS:
            self.dropped_short += 1
            return True
        digest = hashlib.sha1(doc.encode("utf-8")).hexdigest()
        if digest in self.seen:
            self.dropped_dup += 1
            return True
        size = len(doc.encode("utf-8")) + 1
        if self.bytes + size > MAX_SOURCE_BYTES:
            self.full = True
            log(f"  {self.name}: teto de {MAX_SOURCE_BYTES // 10**6} MB atingido")
            return False
        self.seen.add(digest)
        self.docs.append(doc)
        self.bytes += size
        return True

    def add_many(self, docs) -> None:
        for doc in docs:
            if not self.add(doc):
                break

    def write(self) -> Path:
        target = OUT / f"{self.name}.txt"
        part = target.with_name(target.name + ".part")
        with part.open("w", encoding="utf-8", newline="\n") as handle:
            for doc in self.docs:
                handle.write(doc)
                handle.write(DOC_SEP)
        part.replace(target)
        return target


# ======================================================== fonte: Project Gutenberg
# Catálogo: metadados do pacote R "gutenbergr" (rOpenSci, GPL-2) em formato .rda,
# lidos por um desserializador mínimo; nomes dos repositórios GITenberg: lista que
# acompanha o pacote "gitberg" no PyPI.
GUTENBERGR = ("ropensci", "gutenbergr", "main")
PG_DEATH_CUTOFF = 1955  # autor morto até 1955: domínio público também no Brasil e em Portugal (vida + 70)
PG_EXCLUDE_AUTHORS = {"Assis, Machado de"}  # já coberto pela família "nltk" (machado_pt, obra completa)


def _decompress(raw: bytes) -> bytes:
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    if raw[:3] == b"BZh":
        return bz2.decompress(raw)
    if raw[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(raw)
    return raw


class RData:
    """Desserializador mínimo do formato XDR do R (save/.rda), o suficiente para data.frames."""

    def __init__(self, data: bytes):
        self.data, self.pos, self.refs = data, 0, []

    def i32(self) -> int:
        value = struct.unpack_from(">i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def length(self) -> int:
        n = self.i32()
        if n == -1:
            n = (self.i32() << 32) + self.i32()
        return n

    def attrs(self, has_attr: bool):
        return self.item() if has_attr else None

    def item(self):
        flags = self.i32()
        kind, has_attr, has_tag = flags & 0xFF, bool(flags & 0x200), bool(flags & 0x400)
        if kind in (254, 253, 252, 251, 250, 242, 241):
            return None
        if kind == 255:
            index = flags >> 8 or self.i32()
            return self.refs[index - 1]
        if kind == 1:  # SYMSXP
            symbol = ("sym", self.item())
            self.refs.append(symbol)
            return symbol
        if kind == 9:  # CHARSXP
            n = self.i32()
            if n == -1:
                return None
            text = self.data[self.pos:self.pos + n].decode("utf-8", "replace")
            self.pos += n
            return text
        if kind in (2, 3, 5, 6, 239, 240):  # pairlist e afins
            items = []
            while True:
                if has_attr:
                    self.item()
                tag = self.item() if has_tag else None
                items.append((tag[1] if isinstance(tag, tuple) else tag, self.item()))
                flags = self.i32()
                if flags & 0xFF == 254:
                    break
                if flags & 0xFF not in (2, 239):
                    self.pos -= 4
                    items.append((None, self.item()))
                    break
                has_attr, has_tag = bool(flags & 0x200), bool(flags & 0x400)
            return ("pairlist", items)
        if kind == 4:  # ENVSXP
            env: dict = {}
            self.refs.append(env)
            self.i32()
            for _ in range(4):
                self.item()
            return env
        if kind in (10, 13):
            n = self.length()
            values = list(struct.unpack_from(">%di" % n, self.data, self.pos))
            self.pos += 4 * n
            return ("vec", [None if v == -2147483648 else v for v in values], self.attrs(has_attr))
        if kind == 14:
            n = self.length()
            values = list(struct.unpack_from(">%dd" % n, self.data, self.pos))
            self.pos += 8 * n
            return ("vec", values, self.attrs(has_attr))
        if kind == 16:
            n = self.length()
            return ("vec", [self.item() for _ in range(n)], self.attrs(has_attr))
        if kind in (19, 20):
            n = self.length()
            return ("list", [self.item() for _ in range(n)], self.attrs(has_attr))
        if kind == 238:  # ALTREP
            info, state, attributes = self.item(), self.item(), self.item()
            cls = info[1][0][1][1] if info and info[0] == "pairlist" else None
            if cls in ("compact_intseq", "compact_realseq"):
                n, start, step = state[1][:3]
                return ("vec", [start + i * step for i in range(int(n))], attributes)
            if cls == "deferred_string":
                return ("vec", [None if v is None else str(v) for v in state[1][0][1][1]], attributes)
            if cls and cls.startswith("wrap_"):
                return ("vec", state[1][0][1], attributes)
            raise ValueError(f"ALTREP não suportado: {cls}")
        raise ValueError(f"tipo R não suportado: {kind}")


def read_rda_frame(raw: bytes) -> dict[str, list]:
    data = _decompress(raw)
    if data[:5] not in (b"RDX2\n", b"RDX3\n") or data[5:7] != b"X\n":
        raise ValueError("arquivo .rda inesperado")
    reader = RData(data)
    reader.pos = 7
    version = reader.i32()
    reader.i32()
    reader.i32()
    if version == 3:
        reader.pos += reader.i32()
    frame = reader.item()[1][0][1]
    attributes = dict(frame[2][1])
    columns = {}
    for name, column in zip(attributes["names"][1], frame[1]):
        values = column[1]
        col_attrs = dict(column[2][1]) if column[2] else {}
        if "levels" in col_attrs:
            levels = col_attrs["levels"][1]
            values = [None if v is None else levels[v - 1] for v in values]
        columns[name] = values
    return columns


def gitenberg_repo_list() -> dict[int, str]:
    meta = json.loads(cached("https://pypi.org/pypi/gitberg/0.8.8/json", "pypi/gitberg-0.8.8.json"))
    sdist = next(u for u in meta["urls"] if u["filename"].endswith(".tar.gz"))
    raw = cached(sdist["url"], "pypi/" + sdist["filename"])
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        member = next(m for m in tar.getmembers() if m.name.endswith("data/GITenberg_repo_list.tsv"))
        text = tar.extractfile(member).read().decode("utf-8")
    repos = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[0].isdigit():
            repos[int(parts[0])] = parts[1]
    return repos


_PG_START = re.compile(r"^.*\*{3}\s*START OF (?:THE|THIS) PROJECT GUTENBERG.*$|^.*\*END\*THE SMALL PRINT.*$", re.I | re.M)
_PG_END = re.compile(r"^.*\*{3}\s*END OF (?:THE|THIS) PROJECT GUTENBERG.*$|^\s*End of (?:the |this )?Project Gutenberg.*$"
                     r"|^\s*\*{3}\s*END OF THE PROJECT.*$|^\s*Fim do (?:Projecto|Projeto) Gutenberg.*$", re.I | re.M)
_PG_CREDIT = re.compile(r"produced by|prepared by|e-?text|transcri|proofread|pgdp|gutenberg|digitaliza|"
                        r"distributed proofreaders|biblioteca nacional|http|www\.|archive\.org|google|"
                        r"this file|este (?:ficheiro|arquivo|livro electr)|images? (?:generously|courtesy)", re.I)


def clean_gutenberg(text: str) -> list[str]:
    start = _PG_START.search(text)
    if start:
        text = text[start.end():]
    end = _PG_END.search(text)
    if end:
        text = text[:end.start()]
    paragraphs = []
    for lines in split_blocks(text):
        para = join_wrapped(lines) if looks_like_prose(lines) else "\n".join(line.strip() for line in lines)
        para = re.sub(r"\[\s*(?:Pg|pg|Pag|pag|Página|p)\.?\s*[\divxlc]+\s*\]", "", para)
        para = re.sub(r"\[(?:Ilustra[çc][ãa]o|Illustration|Imagem|Gravura|Retrato)[^\]]*\]", "", para, flags=re.I)
        para = re.sub(r"(?<![\w_])_([^_\n]+?)_(?![\w_])", r"\1", para)  # _itálico_
        para = re.sub(r"(?<![\w=])=([^=\n]+?)=(?![\w=])", r"\1", para)  # =negrito=
        para = para.strip()
        if para:
            paragraphs.append(para)
    # créditos de digitalização no começo (e no fim) do texto
    head = 0
    while head < min(len(paragraphs), 6) and _PG_CREDIT.search(paragraphs[head]) and len(paragraphs[head]) < 600:
        head += 1
    paragraphs = paragraphs[head:]
    while paragraphs and _PG_CREDIT.search(paragraphs[-1]) and len(paragraphs[-1]) < 400:
        paragraphs.pop()
    return paragraphs


def source_gutenberg(col: Collector, ctx: dict) -> dict:
    owner, repo, ref = GUTENBERGR
    tables = {}
    for name in ("gutenberg_metadata", "gutenberg_authors"):
        tables[name] = read_rda_frame(raw_cached(owner, repo, ref, f"data/{name}.rda"))
    meta, authors = tables["gutenberg_metadata"], tables["gutenberg_authors"]
    death = dict(zip(authors["gutenberg_author_id"], authors["deathdate"]))
    repos = gitenberg_repo_list()
    books = []
    skipped = {"autor falecido após %d" % PG_DEATH_CUTOFF: [], "fora do GITenberg": [], "autor excluído": []}
    for i, book_id in enumerate(meta["gutenberg_id"]):
        if meta["language"][i] != "pt" or not meta["has_text"][i]:
            continue
        if "public domain" not in (meta["rights"][i] or "").lower():
            continue
        author = meta["author"][i] or ""
        died = death.get(meta["gutenberg_author_id"][i])
        if author in PG_EXCLUDE_AUTHORS:
            skipped["autor excluído"].append(book_id)
            continue
        if died is not None and died > PG_DEATH_CUTOFF:
            skipped["autor falecido após %d" % PG_DEATH_CUTOFF].append(book_id)
            continue
        if book_id not in repos:
            skipped["fora do GITenberg"].append(book_id)
            continue
        books.append((book_id, repos[book_id], author, meta["title"][i]))
    log(f"  gutenberg: {len(books)} livros em português elegíveis; pulados: "
        + ", ".join(f"{k}={len(v)}" for k, v in skipped.items()))
    missing = []
    for n, (book_id, repo_name, author, title) in enumerate(sorted(books)):
        names = [f"{book_id}-0.txt", f"{book_id}-8.txt", f"{book_id}.txt"]
        if book_id < 60000:
            names = [f"{book_id}-8.txt", f"{book_id}-0.txt", f"{book_id}.txt"]
        data, used = None, None
        for name in names:
            try:
                data = raw_cached("GITenberg", repo_name, "master", name)
                used = name
                break
            except NotFound:
                continue
            except Exception as exc:
                log(f"  gutenberg {book_id}: {type(exc).__name__}: {exc}")
                break
        if data is None:
            missing.append(book_id)
            continue
        text = decode(data, "utf-8" if used.endswith("-0.txt") else "utf-8")
        paragraphs = clean_gutenberg(text)
        col.add_many(chunk(paragraphs))
        if col.full:
            break
        if (n + 1) % 50 == 0:
            log(f"  gutenberg: {n + 1}/{len(books)} livros, {len(col.docs)} docs, {col.bytes / 1e6:.1f} MB")
    if missing:
        ctx["failures"].append({"what": f"literatura_gutenberg_pt: {len(missing)} livros sem .txt no GITenberg",
                                "reason": "nenhum de <id>-0.txt/<id>-8.txt/<id>.txt existe no repositório "
                                          f"(ids: {missing[:40]}{'...' if len(missing) > 40 else ''})"})
    for reason, ids in skipped.items():
        if ids and reason != "autor excluído":
            ctx["failures"].append({"what": f"literatura_gutenberg_pt: {len(ids)} livros ({reason})",
                                    "reason": f"pulados por cautela de licença/cobertura: ids {ids[:40]}"})
    return {"books": len(books) - len(missing)}


# ====================================================== fonte: atos oficiais (BR)
VADE_MECUM = ("andreramon", "vade-mecum")
LOCKBOT = ("lockbot", "sd_internet_benevolencia")
LENER = ("peluz", "lener-br")
# leis já presentes (texto compilado e limpo) no vade-mecum: não repetir a versão bruta
_VADE_LAWS = {"l10406", "del2848", "del3689", "l13105", "del5452", "l8078", "l5172", "l8069"}


def _render_dispositivo(item: dict) -> str:
    rotulo = (item.get("rotulo") or "").strip()
    texto = (item.get("texto") or "").strip()
    line = f"{rotulo} {texto}".strip() if rotulo and not texto.startswith(rotulo) else texto
    nota = (item.get("nota") or "").strip()
    if nota and nota not in line:
        line += " " + (nota if nota.startswith("(") else f"({nota})")
    return line


def render_vade_mecum(law: dict) -> list[str]:
    units: list[str] = [law.get("titulo") or law.get("nomeCurto") or ""]
    is_sumula = law.get("tipo") == "sumulas" or (law.get("id", "").startswith("sum"))
    for block in law.get("blocos", []):
        if block.get("revogado"):
            continue
        if block.get("tipo") == "titulo":
            heading = " ".join(x for x in (block.get("rotulo"), block.get("nome")) if x)
            if heading:
                units.append(heading)
            continue
        numero = (block.get("numero") or "").strip()
        caput = (block.get("caput") or "").strip()
        prefix = f"Súmula {numero}." if is_sumula else f"Art. {numero}"
        lines = [f"{prefix} {caput}".strip()]
        if block.get("nota"):
            lines[0] += f" {block['nota']}" if str(block["nota"]).startswith("(") else f" ({block['nota']})"
        for item in block.get("dispositivos") or []:
            if item.get("revogado"):
                continue
            rendered = _render_dispositivo(item)
            if rendered:
                lines.append(rendered)
        units.append("\n".join(lines))
    return [u for u in units if u]


_PLANALTO_NOISE = re.compile(
    r"^(?:Presidência da República|Casa Civil|Secretaria-Geral|Subchefia para Assuntos Jurídicos|"
    r"Secretaria Especial para Assuntos Jurídicos|Mensagem de veto|Vigência|Texto compilado|"
    r"Regulamento|Regulamentação|Conversão da Medida Provisória.*|Vide .*|\(Vide .*|Produção de efeito.*)$", re.I)


def clean_planalto_text(text: str) -> list[str]:
    text = strip_markdown(text.replace("\r\n", "\n"))
    paragraphs = []
    for lines in split_blocks(text):
        para = join_wrapped(lines)
        para = re.sub(r"\s+([,.;:])", r"\1", para)
        if _PLANALTO_NOISE.match(para.strip()) or re.fullmatch(r"[\W\d_]*", para):
            continue
        paragraphs.append(para)
    return paragraphs


def source_atos_oficiais(col: Collector, ctx: dict) -> dict:
    stats = {}
    # 1) Códigos e Constituição (texto compilado do Planalto, já estruturado) + súmulas STF/STJ
    owner, repo = VADE_MECUM
    commit, files = git_list(owner, repo)
    ctx["origins"].append(raw_url(owner, repo, commit, "data/indice.json"))
    law_files = sorted(p for p in files if p.startswith("data/leis/") and p.endswith(".json"))
    for path in law_files:
        try:
            law = json.loads(raw_cached(owner, repo, commit, path))
        except Exception as exc:
            ctx["failures"].append({"what": f"atos_oficiais_br: {owner}/{repo}/{path}", "reason": str(exc)})
            continue
        col.add_many(chunk(render_vade_mecum(law)))
    stats["vade_mecum"] = len(law_files)
    # 2) Leis federais extraídas do Planalto (texto convertido de HTML)
    owner, repo = LOCKBOT
    commit, files = git_list(owner, repo)
    ctx["origins"].append(raw_url(owner, repo, commit, "sd_scrap/importador.csv"))
    seen_laws = set()
    paths = []
    for path in sorted(files):
        if "/txt_laws/" not in path or not path.endswith(".txt"):
            continue
        law = path.rsplit("/", 1)[1][:-4].replace(".", "").lower()
        if law in seen_laws or law in _VADE_LAWS:
            continue
        seen_laws.add(law)
        paths.append(path)
    blobs = git_blobs(owner, repo, [files[p] for p in paths])
    for path in paths:
        data = blobs.get(files[path])
        if data is None:
            continue
        col.add_many(chunk(clean_planalto_text(decode(data))))
    stats["planalto_txt"] = len(paths)
    # 3) LeNER-Br: decisões judiciais e leis (texto bruto)
    owner, repo = LENER
    commit, files = git_list(owner, repo)
    ctx["origins"].append(raw_url(owner, repo, commit, "README.md"))
    paths = sorted(p for p in files if "/raw_text/" in p and p.endswith(".txt"))
    blobs = git_blobs(owner, repo, [files[p] for p in paths])
    for path in paths:
        data = blobs.get(files[path])
        if data is None:
            continue
        paragraphs = clean_planalto_text(decode(data))
        col.add_many(chunk(paragraphs))
    stats["lener_raw"] = len(paths)
    return stats


# ================================================================ fonte: Bíblias
BIBLE_REPO = ("scrollmapper", "bible_databases")


def render_bible_md(text: str, translation: str) -> list[str]:
    """Formato md do scrollmapper: '## Livro', '### Chapter N', '**[c:v]** texto'. Um documento por capítulo."""
    docs, book, chapter, verses = [], "", "", []

    def flush():
        if verses:
            docs.append(f"{book} {chapter}\n\n" + " ".join(verses))

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("## "):
            flush()
            verses = []
            book = line[3:].strip()
        elif line.startswith("### "):
            flush()
            verses = []
            chapter = re.sub(r"(?i)^chapter\s*", "", line[4:].strip())
        else:
            match = re.match(r"^\*\*\[(\d+):(\d+)\]\*\*\s*(.*)$", line)
            if match and match.group(3):
                verses.append(match.group(3).strip())
    flush()
    return docs


# nomes dos livros em português (o arquivo usa os nomes em inglês nos cabeçalhos)
_BOOKS_PT = {
    "Genesis": "Gênesis", "Exodus": "Êxodo", "Leviticus": "Levítico", "Numbers": "Números", "Deuteronomy": "Deuteronômio",
    "Joshua": "Josué", "Judges": "Juízes", "Ruth": "Rute", "I Samuel": "1 Samuel", "II Samuel": "2 Samuel",
    "I Kings": "1 Reis", "II Kings": "2 Reis", "I Chronicles": "1 Crônicas", "II Chronicles": "2 Crônicas",
    "Ezra": "Esdras", "Nehemiah": "Neemias", "Esther": "Ester", "Job": "Jó", "Psalms": "Salmos",
    "Proverbs": "Provérbios", "Ecclesiastes": "Eclesiastes", "Song of Solomon": "Cânticos", "Isaiah": "Isaías",
    "Jeremiah": "Jeremias", "Lamentations": "Lamentações", "Ezekiel": "Ezequiel", "Daniel": "Daniel",
    "Hosea": "Oseias", "Joel": "Joel", "Amos": "Amós", "Obadiah": "Obadias", "Jonah": "Jonas", "Micah": "Miqueias",
    "Nahum": "Naum", "Habakkuk": "Habacuque", "Zephaniah": "Sofonias", "Haggai": "Ageu", "Zechariah": "Zacarias",
    "Malachi": "Malaquias", "Matthew": "Mateus", "Mark": "Marcos", "Luke": "Lucas", "John": "João", "Acts": "Atos",
    "Romans": "Romanos", "I Corinthians": "1 Coríntios", "II Corinthians": "2 Coríntios", "Galatians": "Gálatas",
    "Ephesians": "Efésios", "Philippians": "Filipenses", "Colossians": "Colossenses",
    "I Thessalonians": "1 Tessalonicenses", "II Thessalonians": "2 Tessalonicenses", "I Timothy": "1 Timóteo",
    "II Timothy": "2 Timóteo", "Titus": "Tito", "Philemon": "Filemom", "Hebrews": "Hebreus", "James": "Tiago",
    "I Peter": "1 Pedro", "II Peter": "2 Pedro", "I John": "1 João", "II John": "2 João", "III John": "3 João",
    "Jude": "Judas", "Revelation of John": "Apocalipse", "Revelation": "Apocalipse",
}


def _bible_source(translations: list[str]):
    def build(col: Collector, ctx: dict) -> dict:
        owner, repo = BIBLE_REPO
        commit, files = git_list(owner, repo)
        stats = {}
        for name in translations:
            readme = decode(raw_cached(owner, repo, commit, f"sources/pt/{name}/README.md"))
            ctx["origins"].append(raw_url(owner, repo, commit, f"sources/pt/{name}/README.md"))
            ctx.setdefault("notes", []).append(readme.strip().replace("\n", " "))
            text = decode(raw_cached(owner, repo, commit, f"formats/md/{name}.md"))
            docs = render_bible_md(text, name)
            for doc in docs:
                head, _, body = doc.partition("\n\n")
                book, _, chap = head.rpartition(" ")
                col.add(f"{_BOOKS_PT.get(book, book)} {chap}\n\n{body}")
            stats[name] = len(docs)
        return stats
    return build


# ================================================== fonte: Universal Dependencies
def _conllu_docs(text: str, group_by_prefix: bool) -> list[str]:
    """Reconstrói texto corrido de um .conllu: uma sentença por '# text =', agrupadas por
    documento ('# newdoc' ou prefixo do sent_id); sem estrutura de documento, em blocos de ~3 mil caracteres."""
    docs, current, current_key, size = [], [], None, 0
    sent_id = ""
    for line in text.split("\n"):
        if line.startswith("# newdoc"):
            if current:
                docs.append(" ".join(current))
            current, size, current_key = [], 0, None
        elif line.startswith("# sent_id"):
            sent_id = line.split("=", 1)[1].strip()
        elif line.startswith("# text ="):
            sentence = line.split("=", 1)[1].strip()
            key = re.split(r"[-_.]\d+$|-s?\d+$", sent_id)[0] if group_by_prefix else None
            if current and ((group_by_prefix and key != current_key) or (not group_by_prefix and size >= 3000)):
                docs.append(" ".join(current))
                current, size = [], 0
            current_key = key
            current.append(sentence)
            size += len(sentence) + 1
    if current:
        docs.append(" ".join(current))
    return docs


UD_TREEBANKS = {
    # nome: (repositório, agrupar por prefixo do sent_id)
    "ud_treebanks_pt": [("UD_Portuguese-Bosque", True), ("UD_Portuguese-GSD", False),
                        ("UD_Portuguese-PetroGOLD", True), ("UD_Portuguese-PUD", False)],
    "ud_porttinari_pt": [("UD_Portuguese-Porttinari", True)],
}


def _ud_source(name: str):
    def build(col: Collector, ctx: dict) -> dict:
        stats = {}
        for repo, by_prefix in UD_TREEBANKS[name]:
            commit, files = git_list("UniversalDependencies", repo)
            ctx["origins"].append(raw_url("UniversalDependencies", repo, commit, "README.md"))
            readme = decode(raw_cached("UniversalDependencies", repo, commit, "README.md"))
            license_line = re.search(r"^License:\s*(.+)$", readme, re.M)
            ctx.setdefault("notes", []).append(f"{repo}: License: {license_line.group(1).strip() if license_line else '?'}")
            n = 0
            for path in sorted(p for p in files if p.endswith(".conllu")):
                text = decode(raw_cached("UniversalDependencies", repo, commit, path))
                for doc in _conllu_docs(text, by_prefix):
                    col.add(doc)
                    n += 1
            stats[repo] = n
        return stats
    return build


# ===================================================== fonte: Common Voice (CC0)
def source_common_voice(col: Collector, ctx: dict) -> dict:
    owner, repo = "common-voice", "common-voice"
    commit, files = git_list(owner, repo)
    paths = sorted(p for p in files if p.startswith("server/data/pt/") and p.endswith(".txt")
                   and "benchmark" not in p)
    ctx["origins"].append(raw_url(owner, repo, commit, "server/data/pt/"))
    sentences: list[str] = []
    for path in paths:
        text = decode(raw_cached(owner, repo, commit, path))
        sentences.extend(s.strip() for s in text.split("\n") if s.strip())
    current, size = [], 0
    for sentence in sentences:
        current.append(sentence)
        size += len(sentence) + 1
        if size >= 2500:
            col.add("\n".join(current))
            current, size = [], 0
    if current:
        col.add("\n".join(current))
    return {"files": paths, "sentences": len(sentences)}


# ====================================================================== registro
SOURCES = [
    {
        "name": "literatura_gutenberg_pt",
        "language": "pt",
        "license": "Public Domain",
        "license_basis": (
            "Livros do Project Gutenberg marcados 'Public domain in the USA' e com idioma 'pt' no catálogo "
            "(metadados do pacote gutenbergr); só autores falecidos até 1955 (ou sem data registrada, obras "
            "antigas), ou seja, também em domínio público no Brasil e em Portugal (vida + 70 anos). Textos lidos "
            "dos espelhos GITenberg (github.com/GITenberg/<Título>_<id>), cabeçalho e rodapé do PG removidos. "
            "Machado de Assis fica de fora (já incluído na família nltk)."),
        "build": source_gutenberg,
        "origins": [f"{RAW}/ropensci/gutenbergr/main/data/gutenberg_metadata.rda",
                    "https://pypi.org/pypi/gitberg/0.8.8/json (gitenberg/data/GITenberg_repo_list.tsv)",
                    f"{RAW}/GITenberg/<repo>/master/<id>-8.txt|<id>-0.txt|<id>.txt"],
    },
    {
        "name": "atos_oficiais_br",
        "language": "pt",
        "license": "Public Domain (atos oficiais, Lei 9.610/98 art. 8º, IV)",
        "license_basis": (
            "Constituição Federal, códigos (CC, CPC, CP, CPP, CLT, CDC, CTN, ECA), súmulas do STF/STJ, leis "
            "federais do Planalto e decisões judiciais: 'os textos de tratados ou convenções, leis, decretos, "
            "regulamentos, decisões judiciais e demais atos oficiais' não são objeto de proteção (Lei 9.610/98, "
            "art. 8º, IV). Repositórios-espelho: andreramon/vade-mecum (texto compilado do Planalto), "
            "lockbot/sd_internet_benevolencia (txt_laws, raspados do Planalto), peluz/lener-br (raw_text, MIT)."),
        "build": source_atos_oficiais,
        "origins": [],
    },
    {
        "name": "biblia_livre_pt",
        "language": "pt",
        "license": "CC-BY-3.0-BR",
        "license_basis": "Bíblia Livre e Bíblia Livre Textus Receptus (README do scrollmapper/bible_databases: "
                         "'License: Creative Commons Attribution 3.0 Brazil'). Um documento por capítulo.",
        "build": _bible_source(["PorBLivre", "PorBLivreTR"]),
        "origins": [],
    },
    {
        "name": "biblia_nva_pt",
        "language": "pt",
        "license": "CC-BY-SA-4.0",
        "license_basis": "Bíblia Nova Versão de Acesso Livre (README do scrollmapper/bible_databases: "
                         "'License: Creative Commons: BY-SA 4.0'). Um documento por capítulo.",
        "build": _bible_source(["PorNVA"]),
        "origins": [],
    },
    {
        "name": "ud_treebanks_pt",
        "language": "pt",
        "license": "CC-BY-SA-4.0 (Bosque, GSD, PetroGold) / CC-BY-SA-3.0 (PUD)",
        "license_basis": "README de cada treebank Universal Dependencies (campo 'License:'). Texto corrido "
                         "reconstruído das linhas '# text =' (notícias do CETEMPúblico/CETENFolha no Bosque, "
                         "web no GSD, textos acadêmicos de petróleo e gás no PetroGold, Wikipédia no PUD).",
        "build": _ud_source("ud_treebanks_pt"),
        "origins": [],
    },
    {
        "name": "ud_porttinari_pt",
        "language": "pt",
        "license": "CC-BY-4.0",
        "license_basis": "UD_Portuguese-Porttinari (porção jornalística Porttinari-base, Folha de S.Paulo): "
                         "README 'License: CC BY 4.0'. Sentenças agrupadas por documento de origem.",
        "build": _ud_source("ud_porttinari_pt"),
        "origins": [],
    },
    {
        "name": "common_voice_frases_pt",
        "language": "pt",
        "license": "CC0-1.0",
        "license_basis": "Frases do Sentence Collector do Mozilla Common Voice (server/data/pt), dedicadas ao "
                         "domínio público (CC0). Frases curtas agrupadas em blocos de ~2,5 mil caracteres.",
        "build": source_common_voice,
        "origins": [],
    },
]

STATIC_FAILURES = [
    {"what": "agc2020/consulta (atos normativos brasileiros em JSON/TXT)",
     "reason": "Os textos legais são de domínio público, mas o repositório é distribuído sob a 'Consulta "
               "License v1.1' (uso comercial/institucional proibido; estrutura e metadados protegidos): "
               "licença não comercial, pulado por regra."},
    {"what": "danalec/legalize-br (198 mil normas no formato Legalize)",
     "reason": "Só traz metadados e ementa (poucas centenas de caracteres por norma), não o texto integral."},
    {"what": "UD_Portuguese-CINTIL", "reason": "Licença CC BY-NC-ND 4.0 (não comercial, sem derivados)."},
    {"what": "UD_Portuguese-DANTEStocks", "reason": "CC BY 4.0, mas são tweets curtos sobre ações; ruído para pré-treino."},
    {"what": "cookieukw/LivrosDominioPublico (~2 mil PDFs do portal Domínio Público)",
     "reason": "Só PDFs; extração de texto confiável não é viável só com a biblioteca padrão."},
    {"what": "huggingface.co / wikipedia.org / dumps.wikimedia.org / gutenberg.org",
     "reason": "Bloqueados pelo proxy de saída."},
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", help="fontes separadas por vírgula")
    parser.add_argument("--list", action="store_true", help="só lista as fontes")
    args = parser.parse_args()
    if args.list:
        for source in SOURCES:
            print(source["name"], "-", source["license"])
        return 0
    wanted = set(args.only.split(",")) if args.only else None
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "manifest.json"
    previous_sources, previous_failures = {}, []
    if manifest_path.exists():
        try:
            saved = json.loads(manifest_path.read_text("utf-8"))
            previous_sources = {s["name"]: s for s in saved.get("sources", [])}
            previous_failures = saved.get("failures", [])
        except Exception:
            pass
    results, failures = [], []
    for source in SOURCES:
        name = source["name"]
        if wanted and name not in wanted:
            if name in previous_sources:
                results.append(previous_sources[name])
                failures.extend(f for f in previous_failures if str(f.get("what", "")).startswith(name))
            continue
        log(f"{name}: começando")
        col = Collector(name)
        ctx = {"failures": [], "origins": list(source.get("origins", []))}
        started = time.time()
        try:
            stats = source["build"](col, ctx)
        except Exception as exc:
            log(f"{name}: ERRO {type(exc).__name__}: {exc}")
            failures.append({"what": name, "reason": f"{type(exc).__name__}: {exc}"})
            failures.extend(ctx["failures"])
            continue
        failures.extend(ctx["failures"])
        if not col.docs:
            failures.append({"what": name, "reason": "nenhum documento após a limpeza"})
            continue
        target = col.write()
        basis = source["license_basis"]
        if ctx.get("notes"):
            basis += " [" + " | ".join(ctx["notes"]) + "]"
        entry = {
            "name": name,
            "language": source["language"],
            "license": source["license"],
            "license_basis": basis,
            "origin_urls": list(dict.fromkeys(ctx["origins"])),
            "documents": len(col.docs),
            "bytes": target.stat().st_size,
            "text_path": str(target),
            "stats": stats,
        }
        results.append(entry)
        log(f"{name}: {len(col.docs)} docs, {entry['bytes'] / 1e6:.1f} MB em {time.time() - started:.0f}s "
            f"(curtos descartados: {col.dropped_short}, duplicados: {col.dropped_dup})")
    order = {s["name"]: i for i, s in enumerate(SOURCES)}
    results.sort(key=lambda s: order.get(s["name"], 999))
    manifest = {"family": "ptbr_public", "sources": results, "failures": STATIC_FAILURES + failures}
    seen, unique = set(), []
    for failure in manifest["failures"]:
        key = json.dumps(failure, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            unique.append(failure)
    manifest["failures"] = unique
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
    total = sum(s["bytes"] for s in results)
    log(f"manifesto: {len(results)} fontes, {total / 1e6:.1f} MB -> {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
