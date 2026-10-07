"""Código Python e documentação de pacotes populares do PyPI com licença permissiva.

Usa a API JSON do PyPI para escolher a distribuição-fonte (sdist), confere a
licença declarada (MIT, BSD, Apache-2.0, PSF, ISC) e extrai arquivos .py, .rst e
.md. Grava pretrain_data_raw/pypi_code/pypi_code.txt (documentos separados por
NUL) e um manifesto com pacote, versão e licença de cada um.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "pretrain_data_raw" / "pypi_code"
CACHE = OUT / "cache"
DOC_SEP = "\x00"
MAX_BYTES = 70_000_000
PACKAGES = """requests flask click jinja2 werkzeug itsdangerous markupsafe attrs httpx httpcore h11 anyio sniffio idna
certifi charset-normalizer urllib3 rich typer pydantic fastapi starlette uvicorn sqlalchemy alembic django
pytest pluggy iniconfig packaging pyparsing python-dateutil six pyyaml toml tomli black isort flake8 pycodestyle
pyflakes mccabe mypy-extensions typing-extensions networkx sympy mpmath tqdm colorama tabulate docutils pygments
babel pytz tzdata arrow pendulum marshmallow cerberus jsonschema referencing rpds-py more-itertools toolz
boltons cachetools pyjwt passlib bcrypt itsdangerous wtforms flask-sqlalchemy flask-login aiohttp yarl multidict
frozenlist aiosignal async-timeout websockets beautifulsoup4 soupsieve html5lib lxml-html-clean bleach
python-slugify text-unidecode unidecode faker factory-boy hypothesis coverage tox virtualenv filelock
platformdirs distlib pip setuptools wheel build twine pkginfo readme-renderer requests-toolbelt scrapy
parsel w3lib itemadapter httpie pandas-datareader schedule apscheduler celery kombu vine billiard redis
pymongo peewee pony tortoise-orm dataclasses-json orjson ujson simplejson msgpack cattrs pyrsistent
structlog loguru sentry-sdk python-dotenv environs dynaconf click-plugins sh plumbum fabric invoke paramiko
pexpect ptyprocess psutil watchdog pillow imageio numpy-financial statsmodels patsy scikit-learn joblib
threadpoolctl nltk textblob gensim spacy-legacy streamlit gradio jupyter-core nbformat nbconvert""".split()
PERMISSIVE = re.compile(r"\b(?:MIT|BSD|Apache|PSF|Python Software Foundation|ISC|Unlicense|Zope Public|HPND|MPL-2\.0)\b", re.I)
COPYLEFT = re.compile(r"\b(?:GPL|AGPL|LGPL)\b")


def fetch(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "brasa-corpus/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def license_of(info):
    texts = [str(info.get("license") or "")[:200], str(info.get("license_expression") or "")]
    texts += [c for c in info.get("classifiers") or [] if c.startswith("License ::")]
    joined = " ".join(texts)
    if COPYLEFT.search(joined):
        return None
    match = PERMISSIVE.search(joined)
    return match.group(0) if match else None


def members(blob, filename):
    if filename.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for name in archive.namelist():
                yield name, archive.read(name)
    else:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as archive:
            for member in archive.getmembers():
                if member.isfile() and member.size < 400_000:
                    handle = archive.extractfile(member)
                    if handle:
                        yield member.name, handle.read()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest, written, seen = [], 0, set()
    with (OUT / "pypi_code.txt").open("w", encoding="utf-8") as handle:
        for package in dict.fromkeys(PACKAGES):
            if written >= MAX_BYTES:
                break
            try:
                meta = json.loads(fetch(f"https://pypi.org/pypi/{package}/json"))
            except Exception as error:  # pacote indisponível não interrompe a coleta
                manifest.append({"package": package, "skipped": f"metadados: {error}"})
                continue
            info = meta.get("info") or {}
            license_name = license_of(info)
            if not license_name:
                manifest.append({"package": package, "skipped": "licença não permissiva ou ausente"})
                continue
            sdist = next((item for item in meta.get("urls") or [] if item.get("packagetype") == "sdist"), None)
            if not sdist or sdist.get("size", 0) > 40_000_000:
                manifest.append({"package": package, "skipped": "sem sdist utilizável"})
                continue
            cached = CACHE / sdist["filename"]
            if not cached.exists():
                try:
                    cached.write_bytes(fetch(sdist["url"], timeout=120))
                except Exception as error:
                    manifest.append({"package": package, "skipped": f"download: {error}"})
                    continue
                time.sleep(0.15)
            count = 0
            try:
                for name, raw in members(cached.read_bytes(), sdist["filename"]):
                    if not name.endswith((".py", ".rst", ".md")) or re.search(r"/(?:tests?|testing|vendor|_vendor|examples?/data)/", name):
                        continue
                    try:
                        text = raw.decode("utf-8").replace(DOC_SEP, " ").strip()
                    except UnicodeDecodeError:
                        continue
                    digest = hashlib.sha1(text.encode()).hexdigest()
                    if len(text) < 300 or digest in seen:
                        continue
                    seen.add(digest)
                    relative = name.split("/", 1)[-1]
                    document = f"# arquivo: {package}/{relative}\n{text[:48_000]}"
                    handle.write(document + DOC_SEP)
                    written += len(document.encode())
                    count += 1
            except (tarfile.TarError, zipfile.BadZipFile, EOFError) as error:
                manifest.append({"package": package, "skipped": f"arquivo: {error}"})
                continue
            manifest.append({"package": package, "version": info.get("version"), "license": license_name,
                             "files": count, "sdist": sdist["filename"]})
            print(f"{package} {info.get('version')} [{license_name}]: {count} arquivos; total {written / 1e6:.1f} MB", flush=True)
    kept = [row for row in manifest if "files" in row]
    (OUT / "manifest.json").write_text(json.dumps({
        "family": "pypi_code",
        "sources": [{"name": "pypi_code", "language": "code+en", "license": "permissivas por pacote (ver packages)",
                     "documents": sum(row["files"] for row in kept), "bytes": written,
                     "text_path": "pretrain_data_raw/pypi_code/pypi_code.txt"}],
        "packages": manifest}, ensure_ascii=False, indent=2) + "\n")
    print(f"total: {len(kept)} pacotes, {written / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
