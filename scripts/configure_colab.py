#!/usr/bin/env python3
"""Import only the Colab connection keys, preserving other .env settings."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
KEYS = {"BRASA_COLAB_URL", "BRASA_COLAB_TOKEN"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    if bool(args.file) == args.disable:
        parser.error("Informe o arquivo baixado OU --disable.")
    values = {}
    if args.file:
        for line in args.file.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key in KEYS:
                values[key] = value.strip()
        if not re.fullmatch(r"https://[a-z0-9-]+\.trycloudflare\.com", values.get("BRASA_COLAB_URL", "")):
            parser.error("URL inválida: use o arquivo gerado pelo notebook.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", values.get("BRASA_COLAB_TOKEN", "")):
            parser.error("Chave inválida no arquivo.")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        request = urllib.request.Request(values["BRASA_COLAB_URL"] + "/health",
            headers={"Authorization": "Bearer " + values["BRASA_COLAB_TOKEN"]})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
                health = json.load(response)
            if health.get("ok") is not True:
                raise ValueError("health")
        except Exception:
            parser.error("Colab não respondeu com autenticação válida. Confira a sessão e tente novamente; .env não foi alterado.")
    target = ROOT / ".env"
    previous = target.read_text() if target.exists() else ""
    kept = [line for line in previous.splitlines()
            if line.partition("=")[0].strip().removeprefix("export ").strip() not in KEYS]
    content = "\n".join(kept + [f"{key}={value}" for key, value in values.items()]) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".brasa-env-", dir=ROOT)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if args.file:
        args.file.chmod(0o600)
    print("Conexão Colab configurada." if values else "Conexão Colab desabilitada.")
    print("Reinicie o Brasa com ./start.sh para aplicar.")


if __name__ == "__main__":
    main()
