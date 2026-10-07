#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ptbr_docs -- Portuguese (mostly Brazilian) translations of technical
documentation, fetched from public GitHub repositories, for the Brasa
pretraining corpus.

Only the Python 3 standard library is used.

Network model (the container sits behind an egress proxy):
  * https://raw.githubusercontent.com/<owner>/<repo>/<ref>/<path> serves single
    files.  GitHub directory listings, the GitHub API and tarballs are blocked,
    so files are discovered by crawling index files that are themselves
    raw-fetchable (Transifex .tx/config, mdbook SUMMARY.md, mkdocs nav, React
    sidebars, Sphinx toctrees, LibreOffice makefiles, Hugo "next:" chains...).
  * https://proxy.golang.org (the public Go module mirror) serves an immutable
    zip snapshot of any public GitHub repository at a branch head, and supports
    HTTP Range requests.  When it works for a repository we read the zip's
    central directory (a few ranged requests) to enumerate files and pull only
    the members we need.  The resolved pseudo-version (commit) is pinned in the
    cache, so re-runs read the same snapshot.  Every source falls back to plain
    raw.githubusercontent.com crawling when the module proxy cannot serve it.

Output (in pretrain_data_raw/ptbr_docs/):
  <source>.txt    UTF-8 clean text, documents separated by "\\x00"
  manifest.json   {"family": "ptbr_docs", "sources": [...]}
  _failures.json  sources that were skipped (license) or failed (network)
  _cache/         gzip-compressed copies of everything downloaded; re-runs only
                  download what is missing (404s are remembered too).

Usage:
  python ptbr_docs.py                 # all sources
  python ptbr_docs.py --only mdn_ptbr,react_ptbr
  python ptbr_docs.py --list
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import html
import io
import json
import os
import re
import struct
import sys
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

FAMILY = "ptbr_docs"
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[2]
OUT_DIR = REPO_ROOT / "pretrain_data_raw" / FAMILY
CACHE = OUT_DIR / "_cache"

SLEEP = 0.15                     # seconds between requests
TIMEOUT = 20                     # socket timeout per request
CAP_BYTES = 150 * 1024 * 1024    # max text bytes per source
MIN_CHARS = 200                  # drop shorter documents
RAW = "https://raw.githubusercontent.com"
GOPROXY = "https://proxy.golang.org"
UA = "brasa-corpus-fetch/1.0 (+ptbr_docs; python-urllib)"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# HTTP + cache
# --------------------------------------------------------------------------

class Net:
    def __init__(self):
        self.last = 0.0
        self.requests = 0
        self.bytes = 0

    def get(self, url, headers=None, tries=3):
        """Return (status, body, headers). status is None on network error."""
        hdr = {"User-Agent": UA}
        hdr.update(headers or {})
        err = None
        for attempt in range(tries):
            wait = SLEEP - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.requests += 1
            try:
                req = urllib.request.Request(url, headers=hdr)
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    body = r.read()
                    self.bytes += len(body)
                    return r.status, body, r.headers
            except urllib.error.HTTPError as e:
                try:
                    body = e.read()
                except Exception:
                    body = b""
                if e.code in (400, 401, 403, 404, 405, 410, 451):
                    return e.code, body, e.headers
                err = "HTTP %s" % e.code
            except Exception as e:  # timeouts, resets, DNS, proxy refusals
                err = repr(e)
            time.sleep(1.0 + 2.0 * attempt)
        return None, (err or "").encode(), {}


NET = Net()


def _atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def cache_get(cpath: Path):
    """Return cached bytes, b'' sentinel None for 'known missing', or False if not cached."""
    gz = cpath.with_name(cpath.name + ".gz")
    if gz.is_file():
        try:
            with gzip.open(gz, "rb") as f:
                return f.read()
        except Exception:
            gz.unlink()
            return False
    if cpath.with_name(cpath.name + ".missing").is_file():
        return None
    return False


def cache_put(cpath: Path, data):
    if data is None:
        p = cpath.with_name(cpath.name + ".missing")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
    else:
        _atomic_write(cpath.with_name(cpath.name + ".gz"), gzip.compress(data, 6))


def _safe_rel(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return "/".join(parts)


def raw_url(repo, ref, path):
    return "%s/%s/%s/%s" % (RAW, repo, ref, urllib.parse.quote(path, safe="/@+-_.~!$&'()*,;=:"))


def fetch_raw(repo, ref, path):
    """Fetch one file from raw.githubusercontent.com with on-disk caching."""
    cpath = CACHE / "raw" / repo.replace("/", "__") / ref.replace("/", "__") / _safe_rel(path)
    c = cache_get(cpath)
    if c is not False:
        return c
    st, body, _ = NET.get(raw_url(repo, ref, path))
    if st == 200:
        cache_put(cpath, body)
        return body
    if st == 404:
        cache_put(cpath, None)
    return None


def fetch_text(repo, ref, path):
    b = fetch_raw(repo, ref, path)
    if b is None:
        return None
    return b.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# Go module proxy snapshots (ranged zip reading)
# --------------------------------------------------------------------------

def gomod_escape(p):
    return re.sub(r"[A-Z]", lambda m: "!" + m.group(0).lower(), p)


class _RangeIO(io.RawIOBase):
    def __init__(self, gz):
        self.gz = gz
        self.pos = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        if whence == 0:
            self.pos = off
        elif whence == 1:
            self.pos += off
        else:
            self.pos = self.gz.size + off
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.gz.size - self.pos
        d = self.gz.read_at(self.pos, n)
        self.pos += len(d)
        return d

    def readinto(self, b):
        d = self.read(len(b))
        b[: len(d)] = d
        return len(d)


class GoZip:
    """A GitHub repository snapshot served by proxy.golang.org as a module zip."""

    BLOCK = 1 << 21
    MAX_BLOCKS = 6

    def __init__(self, repo, ref):
        self.repo, self.ref = repo, ref
        self.mod = "github.com/" + gomod_escape(repo)
        self.key = repo.replace("/", "__") + "@" + ref.replace("/", "__")
        self.meta_path = CACHE / "gomod" / (self.key + ".json")
        self.version = self.commit = self.size = None
        self.entries = None
        self.error = None
        self.blocks = collections.OrderedDict()

    # -- metadata ----------------------------------------------------------
    def info_url(self):
        return "%s/%s/@v/%s.info" % (GOPROXY, self.mod, urllib.parse.quote(self.ref, safe=""))

    def zip_url(self):
        return "%s/%s/@v/%s.zip" % (GOPROXY, self.mod, self.version)

    def load(self):
        if self.entries is not None:
            return True
        if self.error:
            return False
        if self.meta_path.is_file():
            try:
                m = json.loads(self.meta_path.read_text())
                self.version, self.commit, self.size = m["version"], m.get("commit"), m["size"]
                self.entries = {k: tuple(v) for k, v in m["entries"].items()}
                return True
            except Exception:
                pass
        for attempt in range(4):
            st, body, _ = NET.get(self.info_url())
            if st == 200:
                info = json.loads(body)
                self.version = info["Version"]
                self.commit = (info.get("Origin") or {}).get("Hash")
                break
            msg = body.decode("utf-8", "replace")[:300]
            if "timed out" in msg and attempt < 3:
                log("  goproxy busy for %s (%s); retrying" % (self.repo, msg.strip()))
                time.sleep(15)
                continue
            self.error = "goproxy %s: %s" % (st, msg.strip())
            return False
        else:
            self.error = "goproxy: no version"
            return False
        try:
            st, _, hdr = NET.get(self.zip_url(), headers={"Range": "bytes=0-0"})
            if st != 206:
                self.error = "goproxy zip: HTTP %s" % st
                return False
            self.size = int(hdr.get("Content-Range").split("/")[-1])
            zf = zipfile.ZipFile(_RangeIO(self))
            prefix_len = None
            ents = {}
            for zi in zf.infolist():
                name = zi.filename
                if prefix_len is None:
                    prefix_len = name.index("/") if "@" not in name else name.index("/", name.index("@"))
                rel = name[prefix_len + 1:]
                if not rel or name.endswith("/"):
                    continue
                ents[rel] = (zi.header_offset, zi.compress_size, zi.file_size, zi.compress_type)
            self.entries = ents
        except Exception as e:
            self.error = "goproxy zip: %r" % (e,)
            return False
        meta = {"repo": self.repo, "ref": self.ref, "version": self.version, "commit": self.commit,
                "size": self.size, "entries": self.entries}
        _atomic_write(self.meta_path, json.dumps(meta).encode())
        log("  goproxy %s@%s -> %s (%d files, %.1f MB zip)" % (
            self.repo, self.ref, self.version, len(self.entries), self.size / 1e6))
        return True

    # -- byte access -------------------------------------------------------
    def _block(self, i):
        if i in self.blocks:
            self.blocks.move_to_end(i)
            return self.blocks[i]
        s = i * self.BLOCK
        e = min(self.size, s + self.BLOCK) - 1
        st, body, _ = NET.get(self.zip_url(), headers={"Range": "bytes=%d-%d" % (s, e)})
        if st == 200 and len(body) == self.size:
            body = body[s:e + 1]
        elif st != 206 or len(body) != e - s + 1:
            raise IOError("range read failed (%s) for %s" % (st, self.repo))
        self.blocks[i] = body
        while len(self.blocks) > self.MAX_BLOCKS:
            self.blocks.popitem(last=False)
        return body

    def read_at(self, off, n):
        n = max(0, min(n, self.size - off))
        out = []
        while n > 0:
            i = off // self.BLOCK
            b = self._block(i)
            o = off - i * self.BLOCK
            c = b[o:o + n]
            if not c:
                break
            out.append(c)
            off += len(c)
            n -= len(c)
        return b"".join(out)

    # -- members -------------------------------------------------------------
    def list(self, pattern=None):
        if not self.load():
            return None
        names = sorted(self.entries)
        if pattern is None:
            return names
        rx = re.compile(pattern)
        return [n for n in names if rx.search(n)]

    def _cpath(self, rel):
        return CACHE / "gomod_files" / self.key / _safe_rel(rel)

    def read(self, rel):
        cp = self._cpath(rel)
        c = cache_get(cp)
        if c is not False:
            return c
        if not self.load():
            return None
        ent = self.entries.get(rel)
        if ent is None:
            return None
        off, csize, usize, method = ent
        try:
            hdr = self.read_at(off, 30)
            if hdr[:4] != b"PK\x03\x04":
                raise IOError("bad local header")
            n, m = struct.unpack("<HH", hdr[26:30])
            data = self.read_at(off + 30 + n + m, csize)
            if method == 0:
                out = data
            elif method == 8:
                out = zlib.decompress(data, -15)
            else:
                raise IOError("unsupported compression %d" % method)
        except Exception as e:
            log("  ! %s:%s %r" % (self.repo, rel, e))
            return None
        cache_put(cp, out)
        return out

    def prefetch(self, rels):
        """Read members in archive order so that ranged blocks are reused."""
        if not self.load():
            return
        todo = [r for r in rels if r in self.entries and cache_get(self._cpath(r)) is False]
        todo.sort(key=lambda r: self.entries[r][0])
        for k, r in enumerate(todo):
            self.read(r)
            if k and k % 500 == 0:
                log("    prefetched %d/%d" % (k, len(todo)))

    def text(self, rel):
        b = self.read(rel)
        return None if b is None else b.decode("utf-8", "replace")


class Repo:
    """Uniform access to a GitHub repo: Go-proxy snapshot if available, else raw."""

    def __init__(self, repo, ref, use_goproxy=True, content_from_raw=False):
        self.repo, self.ref = repo, ref
        self.gz = GoZip(repo, ref) if use_goproxy else None
        self.content_from_raw = content_from_raw
        self._gz_ok = None

    @property
    def gz_ok(self):
        if self._gz_ok is None:
            self._gz_ok = bool(self.gz and self.gz.load())
            if self.gz and not self._gz_ok:
                log("  goproxy unavailable for %s@%s: %s" % (self.repo, self.ref, self.gz.error))
        return self._gz_ok

    def list(self, pattern=None):
        return self.gz.list(pattern) if self.gz_ok else None

    def prefetch(self, rels):
        if self.gz_ok and not self.content_from_raw:
            self.gz.prefetch(rels)

    def read(self, path):
        if self.gz_ok and not self.content_from_raw:
            b = self.gz.read(path)
            if b is not None:
                return b
        return fetch_raw(self.repo, self.ref, path)

    def text(self, path):
        b = self.read(path)
        return None if b is None else b.decode("utf-8", "replace")

    def origin(self):
        urls = ["https://github.com/%s/tree/%s" % (self.repo, self.ref)]
        if self.gz_ok:
            urls.append("%s/%s/@v/%s.zip" % (GOPROXY, self.gz.mod, self.gz.version))
        return urls


# --------------------------------------------------------------------------
# Language heuristics (Portuguese vs English)
# --------------------------------------------------------------------------

PT_WORDS = set("""
de que e do da em um para é com não uma os no se na por mais dos como mas foi ao ele
das tem à seu sua ou ser quando muito há nos já está também só pelo pela até isso ela entre
era depois sem mesmo aos ter seus quem nas esse eles estão você vocês foram essa num nem suas
numa pelos elas seja qual será nós deles essas esses pelas este dele esta estes estas
aquele aquela isto aquilo são pode podem deve devem usar usando uso arquivo arquivos exemplo
função funções então assim onde porque cada todos todas outro outra outros sobre ainda
também apenas sempre agora aqui caso através seguinte seguintes valor valores maneira forma
código dados página usuário sistema também mesma pode-se possível também quanto enquanto
""".split())
EN_WORDS = set("""
the of and to in is that it with was on be by this are from at an not have has which can
you if will but all they their one would there what so when your more use used using should
may also these than then its into only other such how each does file files example function
functions value values here we our about which following must been were being between
through user data page system any most some way while where because however
""".split())
_WORD_RE = re.compile(r"[a-zà-öø-ÿ]+(?:-[a-zà-öø-ÿ]+)?")


def lang_counts(text):
    pt = en = 0
    for w in _WORD_RE.findall(text.lower()):
        if w in PT_WORDS:
            pt += 1
        elif w in EN_WORDS:
            en += 1
    return pt, en


def looks_english(par):
    pt, en = lang_counts(par)
    return en >= 4 and en > 2 * pt


def doc_is_portuguese(text):
    pt, en = lang_counts(text)
    return pt >= 5 and pt >= 1.5 * en


# --------------------------------------------------------------------------
# Generic text assembly
# --------------------------------------------------------------------------

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​‌‍⁠﻿]")


def _dedent(lines):
    ind = [len(l) - len(l.lstrip(" ")) for l in lines if l.strip()]
    k = min(ind) if ind else 0
    return [l[k:] if len(l) >= k else l.strip() for l in lines]


def assemble(blocks, lang_filter=True):
    """blocks: list of (kind, text); kind 'c' = code (kept verbatim), anything
    else = prose.  Returns the clean document text ('' if rejected)."""
    out = []
    prose_parts = []
    for kind, txt in blocks:
        if txt is None:
            continue
        txt = unicodedata.normalize("NFC", txt)
        txt = _CTRL_RE.sub("", txt).replace("\r", "").replace("\t", "    ")
        if kind == "c":
            lines = [l.rstrip() for l in txt.split("\n")]
            while lines and not lines[0].strip():
                lines.pop(0)
            while lines and not lines[-1].strip():
                lines.pop()
            if not lines:
                continue
            lines = _dedent(lines)
            code = "\n".join(lines)
            code = re.sub(r"\n{3,}", "\n\n", code)
            out.append(code)
        else:
            txt = txt.replace(" ", " ")
            lines = [re.sub(r"[  -   　]+", " ", l).strip() for l in txt.split("\n")]
            lines = [l for l in lines if l]
            if not lines:
                continue
            par = "\n".join(lines)
            if not re.search(r"\w", par):
                continue
            if lang_filter and looks_english(par):
                continue
            out.append(par)
            prose_parts.append(par)
    doc = "\n\n".join(out).strip()
    if not doc:
        return ""
    if lang_filter:
        probe = "\n".join(prose_parts)
        if len(probe) < 300:
            probe = doc
        if not doc_is_portuguese(probe):
            return ""
    return doc


# --------------------------------------------------------------------------
# Markdown / MDX / Hugo / Jekyll
# --------------------------------------------------------------------------

_FM_YAML = re.compile(r"\A﻿?---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.S)
_FM_TOML = re.compile(r"\A﻿?\+\+\+[ \t]*\n(.*?)\n\+\+\+[ \t]*(?:\n|\Z)", re.S)


def split_front_matter(text):
    m = _FM_YAML.match(text) or _FM_TOML.match(text)
    if not m:
        return {}, text
    meta = {}
    lines = m.group(1).split("\n")
    i = 0
    while i < len(lines):
        mm = re.match(r"^([A-Za-z_][\w-]*)\s*[:=]\s*(.*)$", lines[i])
        i += 1
        if not mm:
            continue
        key, val = mm.group(1).lower(), mm.group(2).strip()
        if val in (">", "|", ">-", "|-", ">+", "|+"):
            buf = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                buf.append(lines[i].strip())
                i += 1
            val = " ".join(b for b in buf if b)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        meta.setdefault(key, val)
    return meta, text[m.end():]


_MDN_DROP = re.compile(
    r"^(embed\w*|compat|specifications|.*sidebar|.*ref|previous|next|previousnext|previousmenu\w*|"
    r"listsubpages\w*|interactiveexample|seecompattable|draft|deprecated_header|non-standard_header|"
    r"securecontext_header|availableinworkers|outdated|translationinprogress|default_api_sidebar|"
    r"quicklinkswithsubpages|subpagesWithSummaries|includesubnav|page|section|cssinfo|"
    r"jsfiddleembed|livesamplelink|optional_inline|readonlyinline|experimental_inline|"
    r"deprecated_inline|non-standard_inline|securecontext_inline|htmlattrdef|unimplemented_inline|"
    r"obsolete_inline|domxrefinline|glossarysidebar|addonsidebar)$", re.I)


def _macro_args(s):
    out = []
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|([^,\s][^,]*)', s or ""):
        v = m.group(1) if m.group(1) is not None else (m.group(2) if m.group(2) is not None else m.group(3))
        out.append(v.strip())
    return out


def _mdn_macro(m):
    name = m.group(1)
    if m.group(2) is None:
        return ""
    lname = name.lower()
    if lname in ("htmlelement", "svgelement", "mathmlelement"):
        args = _macro_args(m.group(2))
        if not args:
            return ""
        return args[1] if len(args) > 1 and args[1] else "<%s>" % args[0]
    if lname in ("deprecated_inline", "experimental_inline", "non-standard_inline", "optional_inline",
                 "readonlyinline", "securecontext_inline") or _MDN_DROP.match(lname) and lname not in (
            "domxref", "jsxref", "cssxref", "svgattr", "httpheader", "httpmethod", "httpstatus",
            "htmlattrxref", "glossary", "event", "webextapiref", "rfc", "domxref", "csp",
            "mathmlref", "wasmxref", "permissionspolicy", "xref"):
        return ""
    args = _macro_args(m.group(2))
    if not args:
        return ""
    if lname == "rfc":
        return "RFC " + args[0]
    if len(args) > 1 and args[1] and not re.fullmatch(r"\d+|true|false|null|1|0", args[1]):
        return args[1]
    v = args[0]
    if lname in ("domxref", "jsxref", "webextapiref"):
        v = v.replace("/", ".")
    return v


def md_inline(s, flavor=""):
    codes = []

    def keep(m):
        codes.append(m.group(2).strip() if m.group(2).strip() else m.group(2))
        return "\x01%d\x02" % (len(codes) - 1)

    s = re.sub(r"(?<!\\)(`+)(.+?)(?<!`)\1(?!`)", keep, s, flags=re.S)
    if flavor == "mdn":
        s = re.sub(r"\{\{\s*([A-Za-z_][\w-]*)\s*(?:\((.*?)\))?\s*\}\}", _mdn_macro, s, flags=re.S)
    # Hugo shortcodes
    s = re.sub(r'\{\{[<%]\s*glossary_tooltip\b[^}]*?\btext="([^"]*)"[^}]*?[>%]\}\}', r"\1", s)
    s = re.sub(r"\{\{[<%].*?[>%]\}\}", "", s, flags=re.S)
    # Liquid / Jekyll / Nunjucks
    s = re.sub(r"\{%.*?%\}", "", s, flags=re.S)
    if flavor in ("jekyll", "hugo", "mdn", "eleventy"):
        s = re.sub(r"\{\{.*?\}\}", "", s, flags=re.S)
    # MDX comments / heading ids / kramdown attributes
    s = re.sub(r"\{/\*.*?\*/\}", "", s, flags=re.S)
    s = re.sub(r"\s*\{\s*#[\w:.\-]+\s*\}", "", s)
    s = re.sub(r"\{:\s*[^}]*\}", "", s)
    # images, links
    s = re.sub(r"!\[[^\]]*\]\((?:[^()]|\([^()]*\))*\)", "", s)
    s = re.sub(r"!\[[^\]]*\]\[[^\]]*\]", "", s)
    for _ in range(3):
        s2 = re.sub(r"\[([^\[\]]*)\]\((?:[^()]|\([^()]*\))*\)", r"\1", s)
        s2 = re.sub(r"\[([^\[\]]+)\]\[[^\[\]]*\]", r"\1", s2)
        if s2 == s:
            break
        s = s2
    s = re.sub(r"<((?:https?|ftp|mailto):[^>\s]+)>", r"\1", s)
    # HTML
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<li\b[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"</(?:p|div|li|tr|h[1-6]|ul|ol|table|dl|dd|dt|section|blockquote|details|summary|figcaption)\s*>",
               "\n", s, flags=re.I)
    s = re.sub(r"</t[dh]\s*>", " ", s, flags=re.I)
    s = re.sub(r"<(script|style)\b.*?</\1\s*>", "", s, flags=re.S | re.I)
    s = re.sub(r"</?[A-Za-z][\w:.\-]*(?:\s(?:[^<>\"'{}]|\"[^\"]*\"|'[^']*'|\{\{[^{}]*\}\}|\{[^{}]*\})*)?/?>", "", s, flags=re.S)
    # emphasis
    s = re.sub(r"\*\*\*(\S(?:.*?\S)?)\*\*\*", r"\1", s, flags=re.S)
    s = re.sub(r"\*\*(\S(?:.*?\S)?)\*\*", r"\1", s, flags=re.S)
    s = re.sub(r"(?<![\w\\])__(\S(?:.*?\S)?)__(?!\w)", r"\1", s, flags=re.S)
    s = re.sub(r"(?<![\w*\\])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"\1", s)
    s = re.sub(r"(?<![\w\\])_(?=\S)([^_\n]+?)(?<=\S)_(?!\w)", r"\1", s)
    s = re.sub(r"~~(\S(?:.*?\S)?)~~", r"\1", s)
    s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|<>~])", r"\1", s)
    s = html.unescape(s)
    s = re.sub("\x01(\\d+)\x02", lambda m: codes[int(m.group(1))], s)
    return s


_FENCE_OPEN = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")
_LIST_RE = re.compile(r"^([*+\-]|\d{1,3}[.)])\s+(.*)$")
_ADMON_RE = re.compile(r"^(?:\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]|(?:NOTE|TIP|INFO|WARNING|CAUTION|IMPORTANT|DANGER):)\s*", re.I)


def _md_para(par, flavor):
    lines = par.split("\n")
    out = []  # list of [is_block_line, text]
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if re.fullmatch(r"=+|-+", s) and out:
            continue  # setext underline
        if re.fullmatch(r"([-*_])(\s*\1){2,}", s):
            continue  # horizontal rule
        if re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", s):
            continue  # table separator
        if re.match(r"^\[[^\]]+\]:\s*\S+", s):
            continue  # link reference definition
        if flavor == "mdx" and re.match(r"^(import|export)\s", s):
            continue
        s = re.sub(r"^(>\s?)+", "", s).strip()
        s = re.sub(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*", "", s, flags=re.I)
        if not s:
            continue
        block = False
        m = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", s)
        if m:
            s, block = m.group(1), True
        else:
            m = _LIST_RE.match(s)
            if m:
                mk = "-" if m.group(1) in "*+-" else m.group(1)[:-1] + "."
                body = re.sub(r"^\[[ xX]\]\s+", "", m.group(2))
                if body.startswith(": ") and out and out[-1][0]:
                    # MDN definition list: "- term" followed by "  - : definition"
                    out[-1][1] += ": " + body[2:]
                    continue
                s, block = mk + " " + body, True
            elif s.startswith("|") and s.count("|") >= 2:
                cells = [c.strip() for c in s.strip("|").split("|")]
                s, block = " | ".join(c for c in cells if c), True
        if not s:
            continue
        if block or not out:
            out.append([block, s])
        else:
            out[-1][1] += " " + s
    text = "\n".join(t for _, t in out)
    return md_inline(text, flavor)


def _split_code_regions(text):
    """Split markdown into ('t', text) and ('c', code) segments (fences, <pre>)."""
    lines = text.split("\n")
    segs, buf = [], []
    i = 0
    while i < len(lines):
        m = _FENCE_OPEN.match(lines[i])
        if m:
            if buf:
                segs.append(("t", "\n".join(buf)))
                buf = []
            indent = len(m.group(1))
            fch, flen = m.group(2)[0], len(m.group(2))
            j = i + 1
            code = []
            while j < len(lines):
                mm = re.match(r"^[ \t]*(`{3,}|~{3,})[ \t]*$", lines[j])
                if mm and mm.group(1)[0] == fch and len(mm.group(1)) >= flen:
                    break
                ln = lines[j]
                if indent and ln[:indent].strip() == "":
                    ln = ln[indent:]
                code.append(ln)
                j += 1
            segs.append(("c", "\n".join(code)))
            i = j + 1
            continue
        buf.append(lines[i])
        i += 1
    if buf:
        segs.append(("t", "\n".join(buf)))
    # <pre> blocks inside text segments
    out = []
    for kind, s in segs:
        if kind != "t" or "<pre" not in s.lower():
            out.append((kind, s))
            continue
        pos = 0
        for m in re.finditer(r"<pre\b[^>]*>(.*?)</pre\s*>", s, flags=re.S | re.I):
            out.append(("t", s[pos:m.start()]))
            code = re.sub(r"</?code\b[^>]*>", "", m.group(1))
            code = re.sub(r"<[^>]+>", "", code)
            out.append(("c", html.unescape(code)))
            pos = m.end()
        out.append(("t", s[pos:]))
    return out


def md_to_blocks(text, flavor="", title_from_meta=True):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    meta, body = split_front_matter(text)
    blocks = []
    if title_from_meta:
        t = meta.get("title") or meta.get("linktitle")
        if t:
            blocks.append(("h", md_inline(t, flavor)))
        d = meta.get("description") or meta.get("summary")
        if d and len(d) > 40 and len(body) < 600 and d[:60] not in body:
            blocks.append(("p", md_inline(d, flavor)))
    for kind, seg in _split_code_regions(body):
        if kind == "c":
            blocks.append(("c", seg))
            continue
        seg = re.sub(r"<!--.*?-->", "", seg, flags=re.S)
        seg = re.sub(r"\{/\*.*?\*/\}", "", seg, flags=re.S)
        if flavor in ("jekyll", "hugo", "eleventy"):
            seg = re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", "", seg, flags=re.S)
        for par in re.split(r"\n[ \t]*\n", seg):
            if par.strip():
                t = _md_para(par, flavor)
                if t.strip():
                    blocks.append(("p", t))
    return blocks


def md_doc(text, flavor="", lang_filter=True):
    return assemble(md_to_blocks(text, flavor), lang_filter)


# --------------------------------------------------------------------------
# gettext .po
# --------------------------------------------------------------------------

_PO_ESC = {"n": "\n", "t": "\t", "r": "", '"': '"', "\\": "\\", "a": "", "b": "", "f": "", "v": ""}


def _po_unq(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return re.sub(r"\\(.)", lambda m: _PO_ESC.get(m.group(1), m.group(1)), s)


def parse_po(text):
    """Yield dicts with keys refs, flags, ctxt, id, str, comments."""
    entries = []
    cur = None
    field = None

    def new():
        return {"refs": [], "flags": "", "ctxt": None, "id": None, "str": None, "extracted": [], "obsolete": False}

    def push():
        if cur and cur["id"] is not None and not cur["obsolete"]:
            entries.append(cur)

    cur = new()
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            push()
            cur, field = new(), None
            continue
        if line.startswith("#~"):
            cur["obsolete"] = True
            continue
        if line.startswith("#"):
            if cur["str"] is not None:
                push()
                cur, field = new(), None
            if line.startswith("#:"):
                cur["refs"].extend(line[2:].split())
            elif line.startswith("#,"):
                cur["flags"] += line[2:]
            elif line.startswith("#."):
                cur["extracted"].append(line[2:].strip())
            continue
        if line.startswith("msgctxt"):
            if cur["str"] is not None:
                push()
                cur = new()
            cur["ctxt"] = _po_unq(line[7:])
            field = "ctxt"
        elif line.startswith("msgid_plural"):
            field = "plural"
        elif line.startswith("msgid"):
            if cur["str"] is not None:
                push()
                cur = new()
            cur["id"] = _po_unq(line[5:])
            field = "id"
        elif line.startswith("msgstr["):
            idx = line[7:line.index("]")]
            if idx == "0":
                cur["str"] = _po_unq(line[line.index("]") + 1:])
                field = "str"
            else:
                field = "skip"
        elif line.startswith("msgstr"):
            cur["str"] = _po_unq(line[6:])
            field = "str"
        elif line.startswith('"'):
            if field in ("id", "str", "ctxt"):
                cur[field] = (cur[field] or "") + _po_unq(line)
    push()
    return entries


def po_translated(entries):
    """Translated, non-fuzzy entries (header removed)."""
    out = []
    for e in entries:
        if not e["id"] or not e["str"] or "fuzzy" in e["flags"]:
            continue
        out.append(e)
    return out


def rst_inline(s):
    s = re.sub(r":[\w:.+\-]+:`!?~?([^`<]*?)\s*<[^`>]*>`", r"\1", s)
    s = re.sub(r":[\w:.+\-]+:`!?~([^`]*)`", lambda m: m.group(1).split(".")[-1], s)
    s = re.sub(r":[\w:.+\-]+:`!?([^`]*)`", r"\1", s)
    s = re.sub(r"``(.+?)``", r"\1", s, flags=re.S)
    s = re.sub(r"`([^`<]*?)\s*<[^`>]*>`__?", r"\1", s)
    s = re.sub(r"`([^`]+)`__?", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\*\*(\S(?:.*?\S)?)\*\*", r"\1", s, flags=re.S)
    s = re.sub(r"(?<![\w*\\])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"\1", s)
    s = re.sub(r"\|(\w[\w \-]*\w)\|_?", r"\1", s)
    s = s.replace("\\ ", "")
    s = re.sub(r"\\(.)", r"\1", s)
    s = re.sub(r"\s*::$", ":", s)
    s = re.sub(r"\s?\[(?:#\w*|\d+|\*)\]_", "", s)
    s = re.sub(r"^\.\. [\w:-]+::.*$", "", s, flags=re.M)
    return s


def sphinx_msg_blocks(msg, msgid=None):
    """A Sphinx msgstr -> blocks.  Sphinx catalogs keep ordinary paragraphs on
    a single line, so a message with newlines is a literal (code) block."""
    if "\n" in msg.strip("\n"):
        return [("c", msg)]
    if msgid is not None and msg == msgid and len(msg) > 40:
        return []  # copied, untranslated English
    t = rst_inline(msg)
    lines = [l.strip() for l in t.split("\n")]
    out = []
    for l in lines:
        if not l:
            continue
        if out and not re.match(r"^([*\-+•]|\d+[.)]|#\.)\s", l):
            out[-1] += " " + l
        else:
            out.append(l)
    return [("p", "\n".join(out))] if out else []


def po_group_by_ref(entries, ref_filter=None, key=None):
    """Group translated entries by (first matching) source reference file.
    Returns an OrderedDict ref -> [entries] preserving first-seen order."""
    groups = collections.OrderedDict()
    for e in entries:
        refs = [r.rsplit(":", 1)[0] if re.search(r":\d+$", r) else r for r in e["refs"]]
        if ref_filter:
            refs = [r for r in refs if ref_filter(r)]
        if not refs:
            continue
        k = key(refs[0]) if key else refs[0]
        groups.setdefault(k, []).append(e)
    return groups


def chunk_blocks(blocks, target=6000):
    """Split a long stream of prose blocks into ~target-char documents."""
    cur, n = [], 0
    for b in blocks:
        cur.append(b)
        n += len(b[1])
        if n >= target:
            yield cur
            cur, n = [], 0
    if cur:
        yield cur


# --------------------------------------------------------------------------
# AsciiDoc (FreeBSD documentation)
# --------------------------------------------------------------------------

def adoc_inline(s):
    codes = []

    def keep(m):
        codes.append(m.group(1))
        return "\x01%d\x02" % (len(codes) - 1)

    s = re.sub(r"`\+?(.+?)\+?`", keep, s)
    s = re.sub(r"<<[^,>]+,\s*([^>]+)>>", r"\1", s)
    s = re.sub(r"<<([^>]+)>>", lambda m: m.group(1).split("#")[-1].replace("-", " "), s)
    s = re.sub(r"(?:crossref|extref|xref):[^\[\s]*\[[^\],\]]*,\s*([^\]]*)\]", r"\1", s)
    s = re.sub(r"(?:crossref|extref):[^\[\s]*\[([^\]]*)\]", lambda m: m.group(1).replace("-", " "), s)
    s = re.sub(r"xref:[^\[\s]+\[([^\]]*)\]", r"\1", s)
    s = re.sub(r"link:\+*([^\[\s]+?)\+*\[([^\]]*)\]", lambda m: m.group(2) or m.group(1), s)
    s = re.sub(r"((?:https?|ftp)://[^\s\[\]]+)\[([^\]]*)\]", lambda m: m.group(2) or m.group(1), s)
    s = re.sub(r"mailto:([^\s\[]+)\[([^\]]*)\]", lambda m: m.group(2) or m.group(1), s)
    s = re.sub(r"man:([\w.+\-:]+)\[([\w]+)\]", r"\1(\2)", s)
    s = re.sub(r"menu:([^\[]+)\[([^\]]*)\]", lambda m: m.group(1) + (" > " + m.group(2) if m.group(2) else ""), s)
    s = re.sub(r"(?:kbd|btn|footnote|pass|footnoteref|indexterm2?|acronym):[\w\-]*\[([^\]]*)\]", r"\1", s)
    s = re.sub(r"\(\(\((.*?)\)\)\)", "", s)
    s = re.sub(r"\(\((.*?)\)\)", r"\1", s)
    s = re.sub(r"image:[^\[\s]+\[[^\]]*\]", "", s)
    s = re.sub(r"\[\.[\w\-]+\]#([^#]*)#", r"\1", s)
    s = re.sub(r"\[[\w\-.#]+\]#([^#]*)#", r"\1", s)
    s = re.sub(r"(?<![\w#])#([^#\n]+)#(?![\w#])", r"\1", s)
    s = re.sub(r"\{([\w\-]+)\}", lambda m: m.group(1) if not m.group(1).startswith(("nbsp", "empty", "sp", "zwsp")) else " ", s)
    s = re.sub(r"\*\*(\S(?:.*?\S)?)\*\*", r"\1", s)
    s = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"\1", s)
    s = re.sub(r"__(\S(?:.*?\S)?)__", r"\1", s)
    s = re.sub(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])", r"\1", s)
    s = re.sub(r"(?<![\w+])\+(?=\S)([^+\n]+?)(?<=\S)\+(?![\w+])", r"\1", s)
    s = re.sub(r"\((TM|R|C)\)", "", s)
    s = re.sub(r"\s\+$", "", s, flags=re.M)
    s = html.unescape(s)
    s = re.sub("\x01(\\d+)\x02", lambda m: codes[int(m.group(1))], s)
    return s


def adoc_to_blocks(text):
    text = text.replace("\r\n", "\n")
    meta, body = split_front_matter(text)
    blocks = []
    if meta.get("title") and not re.search(r"^=\s", body, flags=re.M):
        blocks.append(("h", meta["title"]))
    lines = body.split("\n")
    i, n = 0, len(lines)
    para = []

    state = {"list": False}

    def flush():
        if para:
            t = adoc_inline("\n".join(para))
            if t.strip():
                blocks.append(("p", t))
            para.clear()
        state["list"] = False

    def add_line(line):
        if para and not state["list"]:
            para[-1] += " " + line
        elif para and state["list"]:
            para[-1] += " " + line
        else:
            para.append(line)

    def add_item(line):
        if para and not state["list"]:
            flush()
        para.append(line)
        state["list"] = True

    while i < n:
        ln = lines[i]
        s = ln.strip()
        i += 1
        if s.startswith("////"):
            flush()
            while i < n and not lines[i].strip().startswith("////"):
                i += 1
            i += 1
            continue
        if s.startswith("//"):
            continue
        if re.match(r"^(ifdef|ifndef|ifeval|endif)::", s) or re.match(r"^(include|toc|image|video|audio)::", s):
            continue
        if re.match(r"^:!?[\w\-]+!?:", s):
            continue
        if re.fullmatch(r"\[\[[^\]]*\]\]", s) or (s.startswith("[") and s.endswith("]") and not s.startswith("[[") and " " not in s.split(",")[0] and len(s) < 120 and not re.search(r"[.!?]$", s[:-1])):
            # anchors and block attribute lines like [source,shell] [.programlisting] [NOTE]
            continue
        m = re.fullmatch(r"(\.{4,}|-{4,})", s)
        if m:
            flush()
            delim, code = s, []
            while i < n and lines[i].strip() != delim:
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append(("c", "\n".join(code)))
            continue
        if re.fullmatch(r"\+{4,}", s):
            flush()
            while i < n and not re.fullmatch(r"\+{4,}", lines[i].strip()):
                i += 1
            i += 1
            continue
        if re.fullmatch(r"(={4,}|\*{4,}|_{4,}|-{2})", s):
            flush()
            continue
        if s.startswith("|==="):
            flush()
            rows = []
            while i < n and not lines[i].strip().startswith("|==="):
                r = lines[i].strip()
                if r:
                    cells = [c.strip() for c in re.split(r"(?<!\\)\|", r) if c.strip()]
                    cells = [re.sub(r"^[\d.*+<>^a-z]*$", "", c) or c for c in cells]
                    rows.append(" | ".join(adoc_inline(c) for c in cells))
                i += 1
            i += 1
            if rows:
                blocks.append(("p", "\n".join(rows)))
            continue
        if not s:
            flush()
            continue
        if s in ("'''", "<<<", "+"):
            flush()
            continue
        m = re.match(r"^(=+)\s+(.*)$", s)
        if m:
            flush()
            blocks.append(("h", adoc_inline(m.group(2))))
            continue
        m = re.match(r"^\.(?=[^\s.])(.*)$", s)
        if m and not para:
            blocks.append(("h", adoc_inline(m.group(1))))
            continue
        s = re.sub(r"^(NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s*", "", s)
        m = re.match(r"^(\*+|\.+|-)\s+(.*)$", s)
        if m:
            add_item("- " + m.group(2))
            continue
        m = re.match(r"^\d+\.\s+(.*)$", s)
        if m:
            add_item(s)
            continue
        m = re.match(r"^(.+?)(::+|;;)\s*(.*)$", s)
        if m and not re.match(r"^\w+://", s) and len(m.group(1)) < 120 and "`" not in m.group(1) \
                and not re.search(r"\w:\S", m.group(1)):
            term = m.group(1).strip()
            add_item(term + (": " + m.group(3) if m.group(3) else ":"))
            continue
        add_line(s)
    flush()
    return blocks


# --------------------------------------------------------------------------
# DocBook XML (PHP manual)
# --------------------------------------------------------------------------

_ENT_DEF = re.compile(r"<!ENTITY\s+([\w.\-]+)\s+(\"([^\"]*)\"|'([^']*)')\s*>", re.S)


_ENT_XML = re.compile(r"<entity\s+name=\"([\w.\-]+)\"\s*>(.*?)</entity>", re.S)


def load_entities(texts):
    """Entity definitions from DTD-style (<!ENTITY ...>) and PHP's XML-style
    (<entity name="...">...</entity>) files."""
    ents = {}
    for t in texts:
        for m in _ENT_DEF.finditer(t):
            v = m.group(3) if m.group(3) is not None else m.group(4)
            ents.setdefault(m.group(1), v)
        for m in _ENT_XML.finditer(t):
            ents.setdefault(m.group(1), m.group(2))
    return ents


_DB_BLOCK = ("para", "simpara", "title", "refpurpose", "refname", "listitem", "entry", "row", "term",
             "varlistentry", "note", "warning", "tip", "caution", "example", "informalexample",
             "section", "sect1", "sect2", "sect3", "sect4", "refsect1", "refsect2", "refsect3",
             "chapter", "preface", "refnamediv", "table", "informaltable", "variablelist",
             "itemizedlist", "orderedlist", "simplelist", "member", "partintro", "abstract",
             "formalpara", "classsynopsis", "methodsynopsis", "constructorsynopsis",
             "destructorsynopsis", "fieldsynopsis", "refentry", "reference", "book", "part",
             "appendix", "article", "glossentry", "glossdef", "glossterm", "qandaentry",
             "question", "answer", "blockquote", "info", "titleabbrev", "caption")
_DB_CODE = ("programlisting", "screen", "synopsis", "literallayout")


def _db_synopsis(m):
    x = m.group(0)
    name = re.search(r"<methodname>(.*?)</methodname>", x, re.S)
    if not name:
        return "\n"
    pre = x[: name.start()]
    rtypes = re.findall(r"<type>(.*?)</type>", pre, re.S)
    mods = re.findall(r"<modifier>(.*?)</modifier>", pre, re.S)
    params = []
    for p in re.findall(r"<methodparam\b([^>]*)>(.*?)</methodparam>", x, re.S):
        attrs, body = p
        ptype = "|".join(re.findall(r"<type>(.*?)</type>", body, re.S))
        pname = re.search(r"<parameter[^>]*>(.*?)</parameter>", body, re.S)
        init = re.search(r"<initializer>(.*?)</initializer>", body, re.S)
        piece = ("%s $%s" % (ptype, pname.group(1) if pname else "")).strip()
        if "role=\"reference\"" in body:
            piece = piece.replace(" $", " &$")
        if init:
            piece += " = " + re.sub(r"<[^>]+>", "", init.group(1))
        if 'choice="opt"' in attrs:
            piece = piece
        params.append(piece)
    sig = " ".join(mods + ["|".join(rtypes)] if rtypes else mods)
    return "\n\x03%s %s(%s)\x04\n" % (sig.strip(), name.group(1), ", ".join(params))


def docbook_to_blocks(xml, ents):
    x = xml.replace("\r\n", "\n")
    x = re.sub(r"<\?xml[^>]*\?>", "", x)
    x = re.sub(r"<!--.*?-->", "", x, flags=re.S)
    x = re.sub(r"<!DOCTYPE[^>\[]*(\[.*?\])?\s*>", "", x, flags=re.S)
    cdata = []

    def keep_cdata(m):
        cdata.append(m.group(1))
        return "\x05%d\x06" % (len(cdata) - 1)

    x = re.sub(r"<!\[CDATA\[(.*?)\]\]>", keep_cdata, x, flags=re.S)
    # resolve language entities (they may contain markup); a few passes for nesting
    base = {"true": "true", "false": "false", "null": "null", "php": "PHP", "amp": "&amp;", "lt": "&lt;",
            "gt": "&gt;", "quot": "&quot;", "apos": "&apos;", "nbsp": " ", "mdash": "—", "ndash": "–",
            "hellip": "…", "copy": "©", "reg": "®", "trade": "™", "rarr": "→", "larr": "←", "times": "×"}
    for _ in range(3):
        if "&" not in x:
            break
        x = re.sub(r"&([\w.\-]+);",
                   lambda m: base.get(m.group(1)) if m.group(1) in base else
                   ents.get(m.group(1), "" if not re.fullmatch(r"#\w+", m.group(1)) else m.group(0)), x)
    x = re.sub(r"<(methodsynopsis|constructorsynopsis|destructorsynopsis)\b.*?</\1>", _db_synopsis, x, flags=re.S)
    x = re.sub(r"<function>(.*?)</function>", r"\1()", x, flags=re.S)
    x = re.sub(r"<methodname>(.*?)</methodname>", r"\1()", x, flags=re.S)
    x = re.sub(r"<parameter[^>]*>(.*?)</parameter>", r"$\1", x, flags=re.S)
    x = re.sub(r"<(?:xref|link)\s+linkend=\"([^\"]+)\"\s*/>", lambda m: m.group(1).split(".")[-1], x)
    x = re.sub(r"<(?:co|area|areaspec|imageobject|mediaobject|inlinemediaobject|imagedata)\b[^>]*/?>", "", x)
    # code blocks
    codes = []

    def keep_code(m):
        parts = re.split("(\x05\\d+\x06)", m.group(2))
        out = []
        for part in parts:
            mm = re.fullmatch("\x05(\\d+)\x06", part)
            if mm:
                out.append(cdata[int(mm.group(1))])
            else:
                out.append(html.unescape(re.sub(r"<[^>]+>", "", part)))
        codes.append("".join(out))
        return "\n\x07%d\x08\n" % (len(codes) - 1)

    x = re.sub(r"<(%s)\b[^>]*>(.*?)</\1>" % "|".join(_DB_CODE), keep_code, x, flags=re.S)
    x = re.sub("\x05(\\d+)\x06", lambda m: cdata[int(m.group(1))], x)
    blk = "|".join(_DB_BLOCK)
    x = re.sub(r"</?(?:%s)\b[^>]*>" % blk, "\n\n", x)
    x = re.sub(r"<[^>]+>", "", x)
    x = html.unescape(x)
    blocks = []
    for part in re.split(r"(\n\x07\d+\x08\n)", x):
        m = re.fullmatch(r"\n\x07(\d+)\x08\n", part)
        if m:
            blocks.append(("c", codes[int(m.group(1))]))
            continue
        for p in re.split(r"\n\s*\n", part):
            p = p.replace("\x03", "").replace("\x04", "")
            p = " ".join(p.split())
            if p:
                blocks.append(("p", p))
    return blocks


# --------------------------------------------------------------------------
# Output writer
# --------------------------------------------------------------------------

class Writer:
    def __init__(self, name):
        self.name = name
        self.final = OUT_DIR / (name + ".txt")
        self.tmp = OUT_DIR / (name + ".txt.part")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        self.f = open(self.tmp, "w", encoding="utf-8", newline="\n")
        self.seen = set()
        self.docs = 0
        self.bytes = 0
        self.full = False
        self.dropped_short = self.dropped_dup = 0

    def add(self, doc):
        if not doc:
            return False
        doc = doc.replace("\x00", "").strip()
        doc = re.sub(r"\n{3,}", "\n\n", doc)
        if len(doc) < MIN_CHARS:
            self.dropped_short += 1
            return False
        h = hashlib.sha1(doc.encode("utf-8")).digest()
        if h in self.seen:
            self.dropped_dup += 1
            return False
        b = len(doc.encode("utf-8")) + (1 if self.docs else 0)
        if self.bytes + b > CAP_BYTES:
            self.full = True
            return False
        if self.docs:
            self.f.write("\x00")
        self.f.write(doc)
        self.seen.add(h)
        self.docs += 1
        self.bytes += b
        return True

    def close(self):
        self.f.close()
        if self.docs:
            os.replace(self.tmp, self.final)
        else:
            self.tmp.unlink()
            if self.final.exists():
                self.final.unlink()


# --------------------------------------------------------------------------
# Source registry
# --------------------------------------------------------------------------

SOURCES = []


def source(name, license, license_basis, language="pt-BR"):
    def deco(fn):
        SOURCES.append({"name": name, "license": license, "license_basis": license_basis,
                        "language": language, "fn": fn})
        return fn
    return deco


class Ctx:
    def __init__(self, name):
        self.name = name
        self.origins = []
        self.notes = []

    def origin(self, *urls):
        for u in urls:
            if isinstance(u, (list, tuple)):
                self.origin(*u)
            elif u and u not in self.origins:
                self.origins.append(u)


def _paths_from_listing_or(repo, pattern, fallback):
    lst = repo.list(pattern)
    if lst:
        return lst
    return fallback() if fallback else []


# ---------------------------------------------------------------- Python ----

@source("python_docs_ptbr", "CC0-1.0 (pt-BR translation) / PSF-2.0 (Python documentation)",
        "python/python-docs-pt-br LICENSE (main branch) dedicates the translation to the public domain "
        "under CC0 1.0; the original Python documentation is under the PSF License Agreement.")
def src_python(ctx, w):
    for ref in ("3.14", "3.13"):
        cfg = fetch_text("python/python-docs-pt-br", ref, ".tx/config")
        if cfg:
            break
    if not cfg:
        raise RuntimeError("could not fetch .tx/config")
    files = re.findall(r"^file_filter\s*=\s*(\S+\.po)\s*$", cfg, flags=re.M)
    files = [f[2:] if f.startswith("./") else f for f in files]
    ctx.origin(raw_url("python/python-docs-pt-br", ref, ".tx/config"))
    repo = Repo("python/python-docs-pt-br", ref)
    ctx.origin(repo.origin())
    repo.prefetch(files)
    for f in files:
        t = repo.text(f)
        if not t:
            continue
        ents = po_translated(parse_po(t))
        blocks = []
        for e in ents:
            blocks.extend(sphinx_msg_blocks(e["str"], e["id"]))
        doc = assemble(blocks)
        w.add(doc)
        if w.full:
            break


# ---------------------------------------------------------------- Django ----

@source("django_docs_ptbr", "BSD-3-Clause",
        "Django documentation is distributed with Django under the BSD 3-Clause license; translations "
        "in django/django-docs-translations are contributions to that documentation.")
def src_django(ctx, w):
    repo_name, ref = "django/django-docs-translations", "stable/5.2.x"
    cfg = fetch_text(repo_name, ref, ".tx/config")
    if not cfg:
        ref = "stable/5.1.x"
        cfg = fetch_text(repo_name, ref, ".tx/config")
    if not cfg:
        raise RuntimeError("no .tx/config")
    ctx.origin(raw_url(repo_name, ref, ".tx/config"))
    for ff in re.findall(r"^file_filter\s*=\s*(\S+)", cfg, flags=re.M):
        path = ff.replace("<lang>", "pt_BR")
        t = fetch_text(repo_name, ref, path)
        if not t:
            continue
        blocks = []
        for e in po_translated(parse_po(t)):
            blocks.extend(sphinx_msg_blocks(e["str"], e["id"]))
        for chunk in chunk_blocks(blocks, 6000):
            w.add(assemble(chunk))
            if w.full:
                return


# ------------------------------------------------------------------ Odoo ----

ODOO_FALLBACK = ["administration", "applications", "contributing", "developer", "essentials", "finance",
                 "general", "hr", "inventory_and_mrp", "legal", "marketing", "productivity", "sales",
                 "services", "studio", "websites", "index"]


@source("odoo_docs_ptbr", "CC-BY-SA-4.0",
        "odoo/documentation LICENSE: Creative Commons Attribution-ShareAlike 4.0 International.")
def src_odoo(ctx, w):
    repo_name, ref = "odoo/documentation", "19.0"
    names = []
    for idx in ("content/index.rst", "content/applications.rst"):
        t = fetch_text(repo_name, ref, idx)
        if t:
            ctx.origin(raw_url(repo_name, ref, idx))
            for m in re.finditer(r"^\s{2,}([\w/\-]+)\s*$", t, flags=re.M):
                names.append(m.group(1).split("/")[-1])
    seen = []
    for n in names + ODOO_FALLBACK:
        if n not in seen:
            seen.append(n)
    for n in seen:
        t = fetch_text(repo_name, ref, "locale/pt_BR/LC_MESSAGES/%s.po" % n)
        if not t:
            continue
        groups = po_group_by_ref(po_translated(parse_po(t)), ref_filter=lambda r: r.endswith(".rst"))
        for ref_file, ents in groups.items():
            blocks = []
            for e in ents:
                blocks.extend(sphinx_msg_blocks(e["str"], e["id"]))
            w.add(assemble(blocks))
            if w.full:
                return


# ----------------------------------------------------------------- Godot ----

@source("godot_docs_ptbr", "CC-BY-3.0",
        "godotengine/godot-docs-l10n README/LICENSE.txt: translation content (msgid, msgstr) is licensed "
        "under CC BY 3.0, attributed to Juan Linietsky, Ariel Manzur and the Godot community.")
def src_godot_docs(ctx, w):
    repo_name, ref, path = "godotengine/godot-docs-l10n", "master", "weblate/pt_BR.po"
    t = fetch_text(repo_name, ref, path)
    if not t:
        raise RuntimeError("could not fetch %s" % path)
    ctx.origin(raw_url(repo_name, ref, path))
    groups = po_group_by_ref(po_translated(parse_po(t)), ref_filter=lambda r: r.endswith(".rst"))
    for ref_file, ents in groups.items():
        blocks = []
        for e in ents:
            blocks.extend(sphinx_msg_blocks(e["str"], e["id"]))
        w.add(assemble(blocks))
        if w.full:
            return


def bbcode_clean(s):
    s = re.sub(r"\[/?codeblocks\]", "", s)
    s = re.sub(r"\[(gdscript|csharp|codeblock|code|text)(?:\s[^\]]*)?\](.*?)\[/\1\]", r"\2", s, flags=re.S)
    s = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", s, flags=re.S)
    s = re.sub(r"\[/?(?:b|i|u|s|kbd|center|font[^\]]*|color[^\]]*)\]", "", s)
    s = re.sub(r"\[(?:method|member|signal|constant|enum|param|annotation|theme_item|constructor|operator)\s+([^\]]+)\]",
               r"\1", s)
    s = re.sub(r"\[([A-Z@][\w.@]*)\]", r"\1", s)
    s = s.replace("[lb]", "[").replace("[rb]", "]")
    return s


@source("godot_classref_ptbr", "MIT",
        "godotengine/godot-editor-l10n README: all translation content (msgid, msgstr) is licensed under "
        "the MIT license, like Godot itself.")
def src_godot_classref(ctx, w):
    repo_name, ref, path = "godotengine/godot-editor-l10n", "main", "classes/pt_BR.po"
    t = fetch_text(repo_name, ref, path)
    if not t:
        raise RuntimeError("could not fetch %s" % path)
    ctx.origin(raw_url(repo_name, ref, path))
    groups = po_group_by_ref(po_translated(parse_po(t)), ref_filter=lambda r: r.startswith("doc/classes/") or "/doc_classes/" in r)
    for ref_file, ents in groups.items():
        cls = ref_file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        blocks = [("h", cls)]
        for e in ents:
            txt = bbcode_clean(e["str"])
            for par in txt.split("\n"):
                if par.strip():
                    blocks.append(("p", par))
        w.add(assemble(blocks))
        if w.full:
            return


# ----------------------------------------------------- Comprehensive Rust ----

@source("comprehensive_rust_ptbr", "CC-BY-4.0 (text) / Apache-2.0 (code)",
        "google/comprehensive-rust README: course text under CC BY 4.0, source code and examples under "
        "Apache 2.0; pt-BR translation in po/pt-BR.po is part of the repository.")
def src_comprehensive_rust(ctx, w):
    repo_name, ref, path = "google/comprehensive-rust", "main", "po/pt-BR.po"
    t = fetch_text(repo_name, ref, path)
    if not t:
        raise RuntimeError("could not fetch %s" % path)
    ctx.origin(raw_url(repo_name, ref, path))
    groups = po_group_by_ref(po_translated(parse_po(t)), ref_filter=lambda r: r.startswith("src/"))
    for ref_file, ents in groups.items():
        md = "\n\n".join(e["str"] for e in ents)
        w.add(md_doc(md, "mdbook"))
        if w.full:
            return


# ----------------------------------------------------------- LibreOffice ----

def xhp_clean(s):
    s = re.sub(r"<image\b.*?</image>|<image\b[^>]*/>", "", s, flags=re.S)
    s = re.sub(r"<bookmark_value>.*?</bookmark_value>", "", s, flags=re.S)
    s = re.sub(r"<caseinline\b.*?</caseinline>", "", s, flags=re.S)
    s = re.sub(r"<embedvar\b[^>]*/?>", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return s


@source("libreoffice_help_ptbr", "MPL-2.0",
        "LibreOffice help sources (LibreOffice/help) carry MPL-2.0 headers; the pt-BR help catalogs in "
        "LibreOffice/translations are distributed by The Document Foundation under the same terms.")
def src_libreoffice(ctx, w):
    help_repo, tr_repo, ref = "LibreOffice/help", "LibreOffice/translations", "master"
    mod = fetch_text(help_repo, ref, "Module_helpcontent2.mk") or ""
    ctx.origin(raw_url(help_repo, ref, "Module_helpcontent2.mk"))
    mks = sorted(set(re.findall(r"AllLangHelp_(\w+)", mod))) or [
        "sbasic", "scalc", "schart", "sdatabase", "sdraw", "shared", "simpress", "smath", "swriter"]
    pos = []
    for name in mks:
        mk = fetch_text(help_repo, ref, "AllLangHelp_%s.mk" % name)
        if not mk:
            continue
        for p in re.findall(r"helpcontent2/source/text/([\w/\-]+)", mk):
            d = p.rsplit("/", 1)[0] if "/" in p else p
            po = "source/pt-BR/helpcontent2/source/text/%s.po" % d
            if po not in pos:
                pos.append(po)
    for po in pos:
        t = fetch_text(tr_repo, ref, po)
        if not t:
            continue
        groups = po_group_by_ref(po_translated(parse_po(t)), ref_filter=lambda r: r.endswith(".xhp"))
        for ref_file, ents in groups.items():
            blocks = []
            for e in ents:
                ctxt = (e["ctxt"] or "").split("\n")
                eid = ctxt[1] if len(ctxt) > 1 else ""
                if eid.startswith(("bm_", "hidden")):
                    continue
                txt = xhp_clean(e["str"])
                if txt.strip():
                    blocks.append(("h" if eid in ("tit", "title") or eid.startswith("hd_") else "p", txt))
            w.add(assemble(blocks))
            if w.full:
                return


# ------------------------------------------------------------------- MDN ----

def _mdn_slug_to_path(slug):
    s = slug.lower()
    s = s.replace("::", "_doublecolon_").replace(":", "_colon_").replace("*", "_star_").replace("?", "_question_")
    return "files/pt-br/%s/index.md" % s


@source("mdn_ptbr", "CC-BY-SA-2.5",
        "mdn/translated-content LICENSE.md: all prose content is available under CC-BY-SA 2.5; code "
        "examples added after 2010 are CC0.")
def src_mdn(ctx, w):
    repo = Repo("mdn/translated-content", "main")

    def fallback():
        wh = fetch_text("mdn/translated-content", "main", "files/pt-br/_wikihistory.json")
        rd = fetch_text("mdn/translated-content", "main", "files/pt-br/_redirects.txt") or ""
        ctx.origin(raw_url("mdn/translated-content", "main", "files/pt-br/_wikihistory.json"))
        slugs = list(json.loads(wh)) if wh else []
        for m in re.finditer(r"\t/pt-BR/docs/(\S+)\s*$", rd, flags=re.M):
            slugs.append(m.group(1))
        out = []
        for s in slugs:
            p = _mdn_slug_to_path(s)
            if p not in out:
                out.append(p)
        return out

    paths = _paths_from_listing_or(repo, r"^files/pt-br/.*\.(md|html)$", fallback)
    ctx.origin(repo.origin())
    repo.prefetch(paths)
    for p in paths:
        t = repo.text(p)
        if not t:
            continue
        w.add(md_doc(t, "mdn"))
        if w.full:
            return


# ------------------------------------------------------------ Kubernetes ----

@source("kubernetes_ptbr", "CC-BY-4.0",
        "kubernetes/website LICENSE: Creative Commons Attribution 4.0 International (documentation).")
def src_kubernetes(ctx, w):
    # The module-proxy snapshot of this very large repo can be stale, so it is
    # only used to enumerate paths; page content is read from raw main.
    repo = Repo("kubernetes/website", "main", content_from_raw=True)
    paths = repo.list(r"^content/pt-br/.*\.(md|html)$") or []
    ctx.origin(repo.origin())
    # crawl internal links for pages added after the snapshot
    queue = list(paths) or ["content/pt-br/docs/_index.md", "content/pt-br/_index.html"]
    seen = set(queue)
    i = 0
    while i < len(queue):
        p = queue[i]
        i += 1
        t = repo.text(p)
        if not t:
            continue
        for m in re.finditer(r"\]\((/(?:pt-br/)?docs/[^)#\s]*)", t):
            link = m.group(1).replace("/pt-br/", "/").strip("/")
            for cand in ("content/pt-br/%s/_index.md" % link, "content/pt-br/%s.md" % link,
                         "content/pt-br/%s/index.md" % link):
                if cand in seen:
                    break
            else:
                if paths:
                    continue  # rely on the listing; avoid speculative 404 probing
                for cand in ("content/pt-br/%s/_index.md" % link, "content/pt-br/%s.md" % link):
                    if cand not in seen:
                        seen.add(cand)
                        queue.append(cand)
        if p.endswith(("OWNERS", ".yaml", ".yml")):
            continue
        w.add(md_doc(t, "hugo"))
        if w.full:
            return


# ----------------------------------------------------------------- React ----

@source("react_ptbr", "CC-BY-4.0",
        "reactjs/pt-br.react.dev LICENSE-DOCS.md: documentation content under Creative Commons "
        "Attribution 4.0 International.")
def src_react(ctx, w):
    repo = Repo("reactjs/pt-br.react.dev", "main")

    def fallback():
        out = []
        for sb in ("sidebarHome", "sidebarLearn", "sidebarReference", "sidebarBlog", "sidebarCommunity"):
            t = fetch_text("reactjs/pt-br.react.dev", "main", "src/%s.json" % sb)
            if not t:
                continue
            ctx.origin(raw_url("reactjs/pt-br.react.dev", "main", "src/%s.json" % sb))
            for pth in re.findall(r'"path"\s*:\s*"(/[^"#?]*)"', t):
                pth = pth.strip("/")
                if not pth:
                    continue
                for cand in ("src/content/%s.md" % pth, "src/content/%s/index.md" % pth):
                    if cand not in out:
                        out.append(cand)
        return out

    paths = _paths_from_listing_or(repo, r"^src/content/.*\.mdx?$", fallback)
    ctx.origin(repo.origin())
    repo.prefetch(paths)
    for p in paths:
        t = repo.text(p)
        if t:
            w.add(md_doc(t, "mdx"))
            if w.full:
                return


# ------------------------------------------------------------- Rust book ----

def mdbook_summary_paths(summary, base):
    out = []
    for m in re.finditer(r"\]\(([^)#\s]+\.md)\)", summary):
        p = (base + "/" + m.group(1)).replace("/./", "/")
        if p not in out:
            out.append(p)
    return out


@source("rust_book_ptbr", "MIT OR Apache-2.0",
        "rust-br/rust-book-pt-br ships LICENSE-MIT and LICENSE-APACHE (same dual license as the "
        "upstream Rust book).")
def src_rust_book(ctx, w):
    repo = Repo("rust-br/rust-book-pt-br", "master")
    paths = []
    for base in ("src", "first-edition/src"):
        s = repo.text(base + "/SUMMARY.md")
        if s:
            ctx.origin(raw_url("rust-br/rust-book-pt-br", "master", base + "/SUMMARY.md"))
            paths += mdbook_summary_paths(s, base)
    ctx.origin(repo.origin())
    repo.prefetch(paths)
    for p in paths:
        t = repo.text(p)
        if t:
            w.add(md_doc(t, "mdbook"))
            if w.full:
                return


# --------------------------------------------------------------- FastAPI ----

@source("fastapi_ptbr", "MIT",
        "fastapi/fastapi LICENSE: MIT (documentation lives in the same repository).")
def src_fastapi(ctx, w):
    repo = Repo("fastapi/fastapi", "master")

    def fallback():
        y = fetch_text("fastapi/fastapi", "master", "docs/en/mkdocs.yml") or ""
        ctx.origin(raw_url("fastapi/fastapi", "master", "docs/en/mkdocs.yml"))
        nav = y.split("\nnav:", 1)[-1]
        return ["docs/pt/docs/" + p for p in re.findall(r"([\w/\-.]+\.md)", nav)]

    paths = _paths_from_listing_or(repo, r"^docs/pt/docs/.*\.md$", fallback)
    ctx.origin(repo.origin())
    repo.prefetch(paths)
    for p in paths:
        t = repo.text(p)
        if t:
            w.add(md_doc(t, "mkdocs"))
            if w.full:
                return


# --------------------------------------------------------------- FreeBSD ----

FREEBSD_BOOKS = ["handbook", "faq", "porters-handbook", "fdp-primer", "dev-model", "developers-handbook",
                 "arch-handbook", "design-44bsd"]
FREEBSD_ARTICLES = [
    "bsdl-gpl", "building-products", "committers-guide", "contributing", "contributors", "cups",
    "explaining-bsd", "filtering-bridges", "fonts", "freebsd-questions", "freebsd-releng",
    "freebsd-src-lsp", "freebsd-status-report-process", "freebsd-update-server", "geom-class",
    "gjournal-desktop", "hubs", "ipsec-must", "ldap-auth", "leap-seconds", "license-guide",
    "linux-emulation", "linux-users", "mailing-list-faq", "nanobsd", "new-users", "pam", "pgpkeys",
    "port-mentor-guidelines", "pr-guidelines", "problem-reports", "rc-scripting", "releng",
    "remote-install", "serial-uart", "solid-state", "vinum", "vm-design"]


@source("freebsd_doc_ptbr", "BSD-2-Clause (FreeBSD Documentation License)",
        "freebsd/freebsd-doc COPYRIGHT: the FreeBSD Documentation Project's documentation is released "
        "under the BSD-style FreeBSD Documentation License (redistribution with or without modification).")
def src_freebsd(ctx, w):
    repo_name, ref = "freebsd/freebsd-doc", "main"
    base = "documentation/content/pt-br"
    ctx.origin(raw_url(repo_name, ref, base + "/books/handbook/_index.adoc"))
    ctx.origin("https://github.com/freebsd/freebsd-doc/tree/main/" + base)
    starts = ["books/%s/_index.adoc" % b for b in FREEBSD_BOOKS] + \
             ["articles/%s/_index.adoc" % a for a in FREEBSD_ARTICLES]
    queue, seen = list(starts), set(starts)
    i = 0
    while i < len(queue):
        rel = queue[i]
        i += 1
        t = fetch_text(repo_name, ref, base + "/" + rel)
        if not t:
            continue
        # follow the book's "next:"/"prev:" chain in the front matter
        for m in re.finditer(r"^(?:next|prev):\s*(books/[\w\-/]+)\s*$", t[:2000], flags=re.M):
            nxt = m.group(1).strip("/") + "/_index.adoc"
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
        # chapters included from a book index (include::chapters-order.adoc etc.)
        for m in re.finditer(r"^include::\{?[\w\-]*\}?([\w\-/]+)/_index\.adoc", t, flags=re.M):
            nxt = rel.rsplit("/", 1)[0] + "/" + m.group(1).strip("/") + "/_index.adoc"
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
        w.add(assemble(adoc_to_blocks(t)))
        if w.full:
            return


# -------------------------------------------------------------- PHP manual ---

@source("php_manual_ptbr", "CC-BY-3.0",
        "php/doc-base LICENSE: documentation content is distributed under the Creative Commons "
        "Attribution 3.0 License or later (PHP Documentation Group).")
def src_php(ctx, w):
    repo = Repo("php/doc-pt_br", "master")
    paths = repo.list(r"\.xml$")
    if not paths:
        raise RuntimeError("module proxy listing unavailable and no raw index for php/doc-pt_br")
    ctx.origin(repo.origin())
    ent_files = [p for p in repo.list(r"\.ent$") or []]
    repo.prefetch(paths + ent_files)
    ents = load_entities([repo.text(p) or "" for p in ent_files])
    # doc-base language-independent entities (titles etc. fall back to English otherwise)
    for p in ("entities/global.ent",):
        t = fetch_text("php/doc-base", "master", p)
        if t:
            for k, v in load_entities([t]).items():
                ents.setdefault(k, v)
    for p in paths:
        t = repo.text(p)
        if not t or "<" not in t:
            continue
        w.add(assemble(docbook_to_blocks(t, ents)))
        if w.full:
            return


# ------------------------------------------------- simple markdown repos ----

def _md_repo(ctx, w, repo_name, ref, pattern, flavor="", exclude=None, fallback=None, order=None):
    repo = Repo(repo_name, ref)
    paths = _paths_from_listing_or(repo, pattern, fallback)
    if exclude:
        rx = re.compile(exclude)
        paths = [p for p in paths if not rx.search(p)]
    if order:
        paths = order(paths)
    ctx.origin(repo.origin())
    repo.prefetch(paths)
    for p in paths:
        t = repo.text(p)
        if not t:
            continue
        fl = "mdx" if p.endswith(".mdx") else flavor
        w.add(md_doc(t, fl))
        if w.full:
            return


@source("opentelemetry_pt", "CC-BY-4.0",
        "open-telemetry/opentelemetry.io LICENSE: Creative Commons Attribution 4.0 International.",
        language="pt-BR/pt")
def src_otel(ctx, w):
    _md_repo(ctx, w, "open-telemetry/opentelemetry.io", "main", r"^content/pt/.*\.md$", "hugo",
             exclude=r"/_includes/")


@source("cncf_glossary_ptbr", "Apache-2.0",
        "cncf/glossary LICENSE: Apache License 2.0.")
def src_cncf_glossary(ctx, w):
    _md_repo(ctx, w, "cncf/glossary", "main", r"^content/pt-br/.*\.md$", "hugo")


@source("electron_docs_ptbr", "MIT",
        "electron/i18n package.json declares license MIT (Electron documentation is MIT licensed).")
def src_electron(ctx, w):
    def fallback():
        rm = fetch_text("electron/i18n", "master", "content/pt-BR/docs/README.md") or ""
        out = ["content/pt-BR/docs/README.md"]
        for m in re.finditer(r"\]\(([\w\-/]+\.md)", rm):
            p = "content/pt-BR/docs/" + m.group(1)
            if p not in out:
                out.append(p)
        return out
    _md_repo(ctx, w, "electron/i18n", "master", r"^content/pt-BR/docs/.*\.md$", "", fallback=fallback)


@source("vue_docs_pt", "CC-BY-4.0",
        "vuejs-translations/docs-pt LICENSE: contents (except images) licensed under CC BY 4.0.",
        language="pt (pt-BR/pt-PT/pt-AO)")
def src_vue(ctx, w):
    _md_repo(ctx, w, "vuejs-translations/docs-pt", "main", r"^src/.*\.md$", "vitepress")


@source("webdev_pt", "CC-BY-3.0 (text) / Apache-2.0 (code samples)",
        "GoogleChrome/web.dev LICENSE: content licensed under Creative Commons Attribution 3.0, code "
        "samples under Apache 2.0.")
def src_webdev(ctx, w):
    _md_repo(ctx, w, "GoogleChrome/web.dev", "main", r"^src/site/content/pt/.*\.md$", "eleventy")


@source("chrome_developers_pt", "CC-BY-SA-4.0 (text) / Apache-2.0 (code samples)",
        "GoogleChrome/developer.chrome.com LICENSE: content licensed under CC BY-SA 4.0, code samples "
        "under Apache 2.0.")
def src_chrome(ctx, w):
    _md_repo(ctx, w, "GoogleChrome/developer.chrome.com", "main", r"^site/pt/.*\.md$", "eleventy",
             exclude=r"/_partials/")


@source("expressjs_ptbr", "CC-BY-4.0",
        "expressjs/expressjs.com LICENSE.md: Creative Commons Attribution 4.0 International.")
def src_express(ctx, w):
    _md_repo(ctx, w, "expressjs/expressjs.com", "main", r"/pt-br/.*\.mdx?$", "mdx")


@source("nodejs_site_ptbr", "MIT",
        "nodejs/nodejs.org LICENSE: MIT.")
def src_nodejs(ctx, w):
    _md_repo(ctx, w, "nodejs/nodejs.org", "main", r"/pt-br/.*\.mdx?$", "mdx")


@source("learnxinyminutes_ptbr", "CC-BY-SA-3.0",
        "adambard/learnxinyminutes-docs LICENSE.txt: Creative Commons Attribution-ShareAlike 3.0 Unported.")
def src_lxiym(ctx, w):
    _md_repo(ctx, w, "adambard/learnxinyminutes-docs", "master", r"^pt-br/.*\.(md|markdown)$", "")


@source("rails_guides_ptbr", "CC-BY-SA-4.0",
        "Translation of the Ruby on Rails Guides, which are licensed CC BY-SA 4.0 (share-alike applies "
        "to the translation); campuscode/rails-guides-pt-BR publishes it openly on GitHub.")
def src_rails(ctx, w):
    _md_repo(ctx, w, "campuscode/rails-guides-pt-BR", "main", r"^pt-BR/.*\.md$", "")


@source("cypress_docs_ptbr", "MIT",
        "pedrohyvo/cypress-docs-pt-br LICENSE: MIT (the upstream Cypress documentation is MIT too).")
def src_cypress(ctx, w):
    _md_repo(ctx, w, "pedrohyvo/cypress-docs-pt-br", "master", r"^pages/.*\.md$", "")


@source("owasp_top10_ptbr", "CC-BY-SA-4.0",
        "OWASP/Top10 LICENSE: Creative Commons Attribution-ShareAlike 4.0 International.")
def src_owasp_top10(ctx, w):
    _md_repo(ctx, w, "OWASP/Top10", "master", r"(pt-br|pt_BR|pt-BR)[^/]*\.md$|/pt-br/.*\.md$|/pt_BR/.*\.md$", "mkdocs")


@source("owasp_api_security_ptbr", "CC-BY-SA-4.0",
        "OWASP/API-Security LICENSE: Creative Commons Attribution-ShareAlike 4.0 International.")
def src_owasp_api(ctx, w):
    _md_repo(ctx, w, "OWASP/API-Security", "master", r"/(pt-br|pt-BR|pt_BR)/.*\.md$", "mkdocs")


@source("nodebestpractices_ptbr", "CC-BY-SA-4.0",
        "goldbergyoni/nodebestpractices LICENSE: Creative Commons Attribution-ShareAlike 4.0 International.")
def src_nodebp(ctx, w):
    _md_repo(ctx, w, "goldbergyoni/nodebestpractices", "master", r"brazilian-portuguese\.md$", "")


@source("opensource_guide_pt", "CC-BY-4.0",
        "github/opensource.guide LICENSE: Creative Commons Attribution 4.0 International.")
def src_osguide(ctx, w):
    _md_repo(ctx, w, "github/opensource.guide", "main", r"^_articles/pt/.*\.md$", "jekyll")


@source("twelve_factor_ptbr", "MIT",
        "heroku/12factor LICENSE: MIT.")
def src_12factor(ctx, w):
    _md_repo(ctx, w, "heroku/12factor", "main", r"^content/pt_br/.*\.md$", "")


@source("aprenda_go_com_testes", "MIT",
        "larien/aprenda-go-com-testes LICENSE.md: MIT.")
def src_go_tests(ctx, w):
    _md_repo(ctx, w, "larien/aprenda-go-com-testes", "main", r"\.md$", "gitbook",
             exclude=r"(^|/)(LICENSE|CONTRIBUTING|CODE_OF_CONDUCT|SUMMARY)[^/]*$|^\.github/")


@source("mostly_adequate_guide_ptbr", "CC-BY-SA-4.0",
        "MostlyAdequate/mostly-adequate-guide-pt-BR LICENSE.md: text under CC BY-SA 4.0.")
def src_mostly_adequate(ctx, w):
    _md_repo(ctx, w, "MostlyAdequate/mostly-adequate-guide-pt-BR", "master", r"\.md$", "gitbook",
             exclude=r"(^|/)(LICENSE|CONTRIBUTING|TRANSLATIONS|SUMMARY|README-original)[^/]*$|^\.github/")


@source("semver_ptbr", "CC-BY-3.0",
        "The Semantic Versioning specification text states it is licensed under Creative Commons "
        "CC BY 3.0 (semver/semver.org).")
def src_semver(ctx, w):
    _md_repo(ctx, w, "semver/semver.org", "gh-pages", r"^lang/pt-BR/.*\.md$", "jekyll")


@source("conventional_commits_ptbr", "CC-BY-3.0",
        "The Conventional Commits specification text is licensed CC BY 3.0 (stated on the spec; repo "
        "conventional-commits/conventionalcommits.org code is MIT).")
def src_cc(ctx, w):
    _md_repo(ctx, w, "conventional-commits/conventionalcommits.org", "master", r"index\.pt-br\.md$", "hugo")


@source("js_questions_ptbr", "MIT",
        "lydiahallie/javascript-questions LICENSE: MIT.")
def src_jsq(ctx, w):
    _md_repo(ctx, w, "lydiahallie/javascript-questions", "master", r"pt-BR/.*\.md$", "")


@source("js_algorithms_ptbr", "MIT",
        "trekhleb/javascript-algorithms LICENSE: MIT.")
def src_jsalgo(ctx, w):
    _md_repo(ctx, w, "trekhleb/javascript-algorithms", "master", r"README\.pt-BR\.md$", "")


@source("hacker_laws_ptbr", "CC-BY-SA-4.0",
        "dwmkerr/hacker-laws LICENSE: Creative Commons Attribution-ShareAlike 4.0 International.")
def src_hacker_laws(ctx, w):
    _md_repo(ctx, w, "dwmkerr/hacker-laws", "main", r"^translations/pt-BR\.md$", "")


@source("gnome_help_ptbr", "CC-BY-3.0",
        "GNOME/gnome-user-docs COPYING: Creative Commons Attribution 3.0 Unported.")
def src_gnome_help(ctx, w):
    repo = Repo("GNOME/gnome-user-docs", "master")
    pos = repo.list(r"/pt_BR/pt_BR\.po$") or ["gnome-help/pt_BR/pt_BR.po", "system-admin-guide/pt_BR/pt_BR.po"]
    ctx.origin(repo.origin())
    for po in pos:
        t = repo.text(po)
        if not t:
            continue
        ctx.origin(raw_url("GNOME/gnome-user-docs", "master", po))
        groups = po_group_by_ref(po_translated(parse_po(t)), ref_filter=lambda r: r.endswith(".page"))
        for ref_file, ents in groups.items():
            blocks = []
            for e in ents:
                s, mid = e["str"], e["id"]
                if mid.startswith(("external ref=", "translator-credits")) or (s == mid and len(s) < 80):
                    continue
                if re.search(r"Creative Commons|creativecommons|@\w+\.\w+", mid):
                    continue
                s = re.sub(r"<[^>]+>", "", s)
                s = html.unescape(s)
                if s.strip():
                    blocks.append(("p", s))
            w.add(assemble(blocks))
            if w.full:
                return


PH_LESSONS = [
    "HTML-lista-palavras-1", "HTML-lista-palavras-2", "algoritmos-agrupamento-scikit-learn-python",
    "analise-correspondencia-pesquisa-historica-R", "analise-sentimento-R-syuzhet",
    "analise-sentimento-exploracao-dados", "aplicacao-web-interativa-r-shiny-leaflet",
    "autoria-sustentavel-texto-simples-pandoc-markdown", "camadas-vetoriais-qgis",
    "contagem-mineracao-dados-investigacao-unix", "contar-frequencias-palavras-python",
    "criacao-visualizacao-ficheiros-html-python", "criar-exposicao-omeka", "download-automatico-wget",
    "download-multiplos-registros-query-strings", "download-paginas-web-python",
    "explorar-analisar-dados-rede-python", "extrair-paginas-ilustradas-com-python",
    "extrair-palavras-chave", "geocodificando-qgis", "georreferenciamento-qgis",
    "git-ferramenta-metodologica-projetos-historia-1", "instalacao-linux", "instalacao-mac",
    "instalacao-modulos-python-pip", "instalacao-windows", "introducao-ao-markdown",
    "introducao-codificacao-textos-tei-1", "introducao-dados-abertos-conectados",
    "introducao-estilometria-python", "introducao-instalacao-python", "introducao-jupyter-notebooks",
    "introducao-linha-comando-bash", "introducao-map-warper", "introducao-mysql-r",
    "introducao-omeka-net", "investigar-literatura-lusofona-literateca",
    "limpar-dados-openrefine", "manipulacao-transformacao-dados-r", "manipular-strings-python",
    "nocoes-basicas-R-dados-tabulares", "nocoes-basicas-paginas-web-html",
    "normalizacao-dados-textuais-python", "palavras-chave-contexto-usando-n-grams-python",
    "preservar-os-seus-dados-de-investigacao", "processamento-basico-texto-r", "qgis-camadas",
    "reutilizacao-codigo-modularidade-python", "saida-dados-ficheiro-html-python",
    "som-dados-sonificacao-historiadores", "sumarizacao-narrativas-web-python",
    "trabalhando-ficheiros-texto-python", "transcricao-automatica-grafias-nao-latinas",
    "visualizacao-animacao-tabelas-historicas-R", "visualizacao-basica-dados-tabulares-r"]


@source("programming_historian_pt", "CC-BY-4.0",
        "The Programming Historian publishes all lessons under Creative Commons Attribution 4.0 "
        "(stated in each lesson's footer and the project's site licence).",
        language="pt (pt-PT/pt-BR)")
def src_ph(ctx, w):
    repo_name, ref = "programminghistorian/jekyll", "gh-pages"
    repo = Repo(repo_name, ref)
    paths = repo.list(r"^pt/licoes/[^/]+\.md$") or ["pt/licoes/%s.md" % s for s in PH_LESSONS]
    ctx.origin(repo.origin() if repo.gz_ok else ["https://github.com/%s/tree/%s/pt/licoes" % (repo_name, ref)])
    for p in paths:
        t = repo.text(p)
        if t:
            w.add(md_doc(t, "jekyll"))
            if w.full:
                return


# --------------------------------------------------------------------------
# Known-but-skipped candidates (licence or reachability), recorded as failures
# --------------------------------------------------------------------------

SKIPPED = [
    {"what": "progit/progit2-pt-br (Pro Git book pt-BR)", "reason": "CC BY-NC-SA 3.0 (non-commercial) - skipped by licence policy"},
    {"what": "braziljs/eloquente-javascript (Eloquent JavaScript pt-BR)", "reason": "CC BY-NC 3.0 (non-commercial) - skipped by licence policy"},
    {"what": "cezaraugusto/You-Dont-Know-JS (pt-BR)", "reason": "CC BY-NC-ND 3.0 - skipped by licence policy"},
    {"what": "caelum/apostila-* (Caelum course books)", "reason": "CC BY-NC-ND - skipped by licence policy"},
    {"what": "Think Python 2e pt-BR (Pense em Python)", "reason": "CC BY-NC 3.0 - skipped by licence policy"},
    {"what": "PHP The Right Way pt-BR", "reason": "CC BY-NC-SA 3.0 - skipped by licence policy"},
    {"what": "EthicalSource/contributor_covenant pt-br", "reason": "repository LICENSE is the Hippocratic License 3.0 (use restrictions, not an open licence) - skipped"},
    {"what": "andreia/symfony-docs-pt-BR", "reason": "no licence file in the translation repo and content is a 2017 snapshot of Symfony 2.x docs - skipped as unclear/stale"},
    {"what": "MicrosoftDocs/*.pt-BR localisation repos", "reason": "azure-docs.pt-br and most .pt-br repos were deleted; remaining ones are nearly empty or not servable (invalid paths)"},
    {"what": "freeCodeCamp/i18n-curriculum (Portuguese)", "reason": "Go module proxy times out (repository too large) and there is no raw-fetchable index of challenge files"},
    {"what": "qgis/QGIS-Documentation pt_BR", "reason": "translations are not committed to the repository (only Transifex config) - nothing raw-fetchable"},
    {"what": "microsoft/TypeScript-Website pt", "reason": "default branch 'v2' cannot be resolved by the Go module proxy (semver-like name) and no complete raw index - skipped"},
]


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def load_manifest():
    p = OUT_DIR / "manifest.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"family": FAMILY, "sources": []}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma separated source names")
    ap.add_argument("--skip", help="comma separated source names to skip")
    ap.add_argument("--list", action="store_true", help="list sources and exit")
    args = ap.parse_args(argv)
    if args.list:
        for s in SOURCES:
            print("%-28s %s" % (s["name"], s["license"]))
        return 0
    only = set(args.only.split(",")) if args.only else None
    skip = set(args.skip.split(",")) if args.skip else set()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    by_name = {s["name"]: s for s in manifest.get("sources", [])}
    fail_path = OUT_DIR / "_failures.json"
    try:
        failures = {f["what"]: f for f in json.loads(fail_path.read_text(encoding="utf-8"))}
    except Exception:
        failures = {}
    for f in SKIPPED:
        failures[f["what"]] = f
    t0 = time.time()
    for s in SOURCES:
        name = s["name"]
        if (only and name not in only) or name in skip:
            continue
        log("== %s" % name)
        ctx = Ctx(name)
        w = Writer(name)
        r0, ts = NET.requests, time.time()
        try:
            s["fn"](ctx, w)
            err = None
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
            log("  ! %s failed: %s" % (name, err))
            traceback.print_exc()
        w.close()
        log("  %s: %d docs, %.2f MB, %d requests, %.0fs%s (dropped: %d short, %d dup)" % (
            name, w.docs, w.bytes / 1e6, NET.requests - r0, time.time() - ts,
            " [CAP]" if w.full else "", w.dropped_short, w.dropped_dup))
        if w.docs:
            by_name[name] = {
                "name": name, "language": s["language"], "license": s["license"],
                "license_basis": s["license_basis"], "origin_urls": ctx.origins,
                "documents": w.docs, "bytes": w.bytes,
                "text_path": str(OUT_DIR / (name + ".txt")),
            }
            failures.pop(name, None)
        else:
            by_name.pop(name, None)
            failures[name] = {"what": name, "reason": err or "no documents produced"}
        if err and w.docs:
            failures[name] = {"what": name, "reason": "partial: " + err}
    order = [s["name"] for s in SOURCES]
    manifest = {"family": FAMILY,
                "sources": sorted(by_name.values(), key=lambda x: order.index(x["name"]) if x["name"] in order else 999)}
    _atomic_write(OUT_DIR / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    _atomic_write(fail_path, json.dumps(list(failures.values()), ensure_ascii=False, indent=2).encode("utf-8"))
    tot = sum(x["bytes"] for x in manifest["sources"])
    log("done in %.0fs: %d sources, %.1f MB total, %d HTTP requests, %.1f MB downloaded" % (
        time.time() - t0, len(manifest["sources"]), tot / 1e6, NET.requests, NET.bytes / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
