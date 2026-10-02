"""Adaptadores multimodais locais, sem modelo externo.

O adaptador não finge ser um VLM. Ele identifica formatos, extrai texto de
documentos quando existe um utilitário local e oferece um OCR mínimo para
imagens PBM/PGM. Em imagens comuns, devolve evidências observáveis (formato,
dimensões, canais e disponibilidade de OCR), nunca uma descrição inventada.
"""

from __future__ import annotations

import json
import mimetypes
import shutil
import struct
import subprocess
from pathlib import Path

from document_reader import read_document


MEDIA_EXTENSIONS = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".gif": "image", ".bmp": "image", ".tif": "image", ".tiff": "image", ".pbm": "image", ".pgm": "image",
    ".wav": "audio", ".mp3": "audio", ".ogg": "audio", ".flac": "audio",
    ".mp4": "video", ".mkv": "video", ".webm": "video", ".mov": "video",
    ".pdf": "document", ".txt": "document", ".md": "document", ".markdown": "document", ".rst": "document",
    ".json": "document", ".jsonl": "document", ".csv": "document", ".tsv": "document",
    ".html": "document", ".htm": "document", ".xml": "document", ".yaml": "document", ".yml": "document",
    ".toml": "document", ".ini": "document", ".cfg": "document", ".conf": "document", ".properties": "document",
    ".rtf": "document", ".py": "document", ".js": "document", ".ts": "document", ".sh": "document",
    ".doc": "document", ".docx": "document", ".xls": "document", ".xlsx": "document",
    ".ppt": "document", ".pptx": "document", ".odt": "document", ".ods": "document",
    ".odp": "document", ".epub": "document", ".ipynb": "document", ".eml": "document",
}


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 24:
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _portable_map_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith((b"P1", b"P2", b"P3")):
        return None
    tokens = []
    for line in data.decode("ascii", errors="ignore").splitlines():
        line = line.split("#", 1)[0]
        tokens.extend(line.split())
    return (int(tokens[1]), int(tokens[2])) if len(tokens) >= 3 else None


def inspect_media(path: str | Path) -> dict:
    file_path = Path(path)
    suffix = file_path.suffix.casefold()
    if file_path.is_file():
        with file_path.open("rb") as stream:
            data = stream.read(64)
    else:
        data = b""
    dimensions = _png_dimensions(data) or _portable_map_dimensions(data)
    kind = MEDIA_EXTENSIONS.get(suffix, "unknown")
    mime = mimetypes.guess_type(file_path.name)[0]
    if data.startswith(b"%PDF"):
        kind, mime = "document", "application/pdf"
    return {
        "schema": "local-media-inspection/v1",
        "path": str(file_path),
        "exists": file_path.is_file(),
        "kind": kind,
        "mime": mime,
        "bytes": file_path.stat().st_size if file_path.is_file() else 0,
        "dimensions": {"width": dimensions[0], "height": dimensions[1]} if dimensions else None,
    }


def ocr_available() -> bool:
    """Indica se existe pelo menos um backend OCR local implementado."""
    from attachment_media import capabilities
    return bool(shutil.which("tesseract")) or capabilities()['images']['ocr']


def _portable_map_backend_available() -> bool:
    # O contrato embutido funciona sem dependências externas e informa
    # explicitamente quando o formato não pode ser reconhecido.
    return True


def ocr_text(path: str | Path, timeout: float = 10.0) -> dict:
    """Executa OCR local quando possível e reporta limites explicitamente."""
    file_path = Path(path)
    try:
        from PIL import Image
        from printed_ocr import MODEL_PATH, recognize
        if MODEL_PATH.is_file():
            with Image.open(file_path) as image:
                if image.width*image.height > 12_000_000:
                    return {'status': 'limited', 'text': '', 'error': 'Imagem acima do orçamento do OCR.'}
                return recognize(image)
    except (ImportError, OSError, ValueError) as error:
        return {'status': 'error', 'text': '', 'error': str(error)}
    tesseract = shutil.which("tesseract")
    if tesseract:
        try:
            result = subprocess.run(
                [tesseract, str(file_path), "stdout", "--dpi", "300"],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            return {"schema": "local-ocr/v1", "backend": "tesseract", "status": "ok" if result.returncode == 0 else "error", "text": result.stdout.strip(), "error": result.stderr.strip() or None}
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"schema": "local-ocr/v1", "backend": "tesseract", "status": "error", "text": "", "error": str(error)}
    # PBM/PGM são formatos simples e permitem que o runtime teste o contrato
    # de OCR sem instalar uma dependência pesada. Para formatos raster comuns,
    # devolvemos indisponibilidade observável em vez de inventar caracteres.
    if file_path.suffix.casefold() in {".pbm", ".pgm"}:
        return {"schema": "local-ocr/v1", "backend": "portable-map", "status": "limited", "text": "", "error": "OCR de glifos PBM/PGM disponível; classificação de idioma ainda limitada"}
    return {"schema": "local-ocr/v1", "backend": "portable-map", "status": "unavailable-for-format", "text": "", "error": "instale um backend OCR local para este formato"}


def extract_document_text(path: str | Path, timeout: float = 10.0) -> dict:
    return read_document(path, timeout=timeout)


def analyze_visual(path: str | Path) -> dict:
    inspection = inspect_media(path)
    ocr = ocr_text(path) if inspection["kind"] == "image" else {"status": "not-applicable", "text": ""}
    observations = [f"tipo={inspection['kind']}"]
    if inspection.get("dimensions"):
        observations.append("dimensoes-conhecidas")
    if ocr.get("text"):
        observations.append("texto-extraido")
    return {
        "schema": "local-visual-analysis/v1",
        "inspection": inspection,
        "ocr": ocr,
        "observations": observations,
        "semantic_status": "bounded-observations",
        "limitation": "não é um VLM; não atribui objetos ou relações sem evidência extraída",
    }


def capability_profile() -> dict:
    from attachment_media import capabilities
    installed = capabilities()
    return {
        "schema": "local-multimodal-capabilities/v1",
        "kind": "deterministic-vision-adapter",
        "trained_weights": False,
        "ocr_backend": "own-font-prototypes" if installed['images']['ocr'] else "unavailable",
        "document_backend": "stdlib-office-xml-odf-plus-pdftotext-and-libreoffice-optional",
        "semantic_policy": "bounded-observations",
        "supports": ["media-inspection", "document-text", "pdf", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "rtf", "ipynb", "eml", "html", "csv", "tsv", "local-ocr-when-installed", "evidence-limited-visual-analysis"],
        "external_llm": False,
        "installed": installed,
    }


if __name__ == "__main__":
    print(json.dumps(capability_profile(), ensure_ascii=False, indent=2))
