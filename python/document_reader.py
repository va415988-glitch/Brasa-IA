"""Extração local e limitada de texto para formatos comuns de documentos.

O leitor usa bibliotecas padrão para texto e arquivos Office Open XML/ODF.
PDF e formatos Office legados usam utilitários locais opcionais. Nunca executa
macros, scripts ou instruções encontradas dentro dos arquivos.
"""

from __future__ import annotations

from email import policy
from email.parser import BytesParser
import html.parser
import json
import mimetypes
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote
import xml.etree.ElementTree as ET


MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_CHARS = 200_000
MAX_ZIP_ENTRIES = 4096
MAX_ZIP_UNCOMPRESSED = 128 * 1024 * 1024
MAX_ZIP_MEMBER = 16 * 1024 * 1024
MAX_TABLE_ROWS = 1000
MAX_REPEATED_CELLS = 100

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".json", ".jsonl",
    ".csv", ".tsv", ".xml", ".html", ".htm", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".properties", ".py", ".pyi",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".rs", ".go",
    ".java", ".kt", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs",
    ".sh", ".bash", ".zsh", ".sql", ".css", ".scss", ".sass",
    ".tex", ".svg", ".diff", ".patch", ".makefile", ".dockerfile",
}
ZIP_DOCUMENT_EXTENSIONS = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".epub"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pbm", ".pgm", ".tif", ".tiff"}


class DocumentReadError(ValueError):
    """O arquivo existe, mas não pode ser extraído com segurança/confiabilidade."""


class _HTMLText(html.parser.HTMLParser):
    _BLOCKS = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}
    _IGNORED = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored = 0

    def handle_starttag(self, tag, _attrs):
        tag = tag.casefold()
        if tag in self._IGNORED:
            self.ignored += 1
        elif not self.ignored and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.casefold()
        if tag in self._IGNORED and self.ignored:
            self.ignored -= 1
        elif not self.ignored and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"[ \t\f\v]+", " ", "".join(self.parts)).strip()


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:8192]:
        raise DocumentReadError("arquivo binário: não será interpretado como texto")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1252")
        except UnicodeDecodeError as error:
            raise DocumentReadError("codificação do texto não reconhecida") from error


def _clip(text: str, maximum: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    return (text[:maximum], len(text) > maximum)


def _html_to_text(data: bytes) -> str:
    parser = _HTMLText()
    parser.feed(_decode_text(data))
    parser.close()
    return parser.text()


def _xml(data: bytes, name: str) -> ET.Element:
    if len(data) > MAX_ZIP_MEMBER:
        raise DocumentReadError(f"componente XML acima do limite: {name}")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data, flags=re.I):
        raise DocumentReadError(f"declaração XML não permitida: {name}")
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise DocumentReadError(f"XML inválido em {name}: {error}") from error


def _open_zip(path: Path) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        raise DocumentReadError(f"pacote compactado inválido: {error}") from error
    entries = archive.infolist()
    if len(entries) > MAX_ZIP_ENTRIES:
        archive.close()
        raise DocumentReadError("documento contém entradas compactadas demais")
    if sum(item.file_size for item in entries) > MAX_ZIP_UNCOMPRESSED:
        archive.close()
        raise DocumentReadError("conteúdo descompactado excede 128 MiB")
    return archive


def _member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise DocumentReadError(f"componente ausente: {name}") from error
    if info.file_size > MAX_ZIP_MEMBER:
        raise DocumentReadError(f"componente acima de 16 MiB: {name}")
    try:
        return archive.read(info)
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise DocumentReadError(f"não foi possível ler {name}: {error}") from error


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_paragraphs(root: ET.Element, paragraph_tag: str, text_tag: str, tab_tag: str | None = None, break_tag: str | None = None) -> list[str]:
    paragraphs = []
    for paragraph in root.iter(paragraph_tag):
        parts = []
        for item in paragraph.iter():
            if item.tag == text_tag and item.text:
                parts.append(item.text)
            elif tab_tag and item.tag == tab_tag:
                parts.append("\t")
            elif break_tag and item.tag == break_tag:
                parts.append("\n")
        value = "".join(parts).strip()
        if value:
            paragraphs.append(value)
    return paragraphs


def _docx(archive: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    name = "word/document.xml"
    root = _xml(_member(archive, name), name)
    paragraphs = _xml_paragraphs(
        root,
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p",
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t",
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tab",
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}br",
    )
    return "\n".join(paragraphs), {"parts": ["word/document.xml"]}


def _pptx(archive: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    names = [item for item in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", item)]
    names.sort(key=lambda name: int(re.search(r"slide(\d+)", name).group(1)))
    pages = []
    for index, name in enumerate(names[:200], 1):
        root = _xml(_member(archive, name), name)
        paragraphs = _xml_paragraphs(root, "{http://schemas.openxmlformats.org/drawingml/2006/main}p", "{http://schemas.openxmlformats.org/drawingml/2006/main}t")
        if paragraphs:
            pages.append(f"Slide {index}\n" + "\n".join(paragraphs))
    return "\n\n".join(pages), {"slides": min(len(names), 200), "omitted_slides": max(0, len(names) - 200)}


def _xlsx(archive: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    shared: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        root = _xml(_member(archive, "xl/sharedStrings.xml"), "xl/sharedStrings.xml")
        shared = ["".join(node.itertext()) for node in root if _local_name(node.tag) == "si"]

    sheet_paths: list[tuple[str, str]] = []
    if "xl/workbook.xml" in archive.namelist():
        workbook = _xml(_member(archive, "xl/workbook.xml"), "xl/workbook.xml")
        rels = {}
        rel_path = "xl/_rels/workbook.xml.rels"
        if rel_path in archive.namelist():
            relationships = _xml(_member(archive, rel_path), rel_path)
            rels = {item.get("Id", ""): item.get("Target", "") for item in relationships}
        for sheet in workbook.iter():
            if _local_name(sheet.tag) != "sheet":
                continue
            relation_id = next((value for key, value in sheet.attrib.items() if key.endswith("}id")), "")
            target = rels.get(relation_id, "")
            target = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
            if target and not target.startswith("../") and target in archive.namelist():
                sheet_paths.append((sheet.get("name") or f"Sheet {len(sheet_paths) + 1}", target))
    if not sheet_paths:
        sheet_paths = [(f"Sheet {index}", name) for index, name in enumerate(sorted((n for n in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)), key=lambda n: int(re.search(r"sheet(\d+)", n).group(1))), 1)]

    sheets = []
    for sheet_name, name in sheet_paths[:50]:
        root = _xml(_member(archive, name), name)
        rows = []
        for row_index, row in enumerate((item for item in root.iter() if _local_name(item.tag) == "row"), 1):
            if row_index > MAX_TABLE_ROWS:
                break
            cells = []
            for cell in row:
                if _local_name(cell.tag) != "c":
                    continue
                ref = cell.get("r") or ""
                value_node = next((item for item in cell if _local_name(item.tag) == "v"), None)
                inline_node = next((item for item in cell if _local_name(item.tag) == "is"), None)
                value = ""
                cell_type = cell.get("t")
                if cell_type == "s" and value_node is not None:
                    try:
                        value = shared[int(value_node.text or "0")]
                    except (ValueError, IndexError):
                        value = ""
                elif inline_node is not None:
                    value = "".join(inline_node.itertext())
                elif value_node is not None:
                    value = value_node.text or ""
                if not value:
                    formula = next((item for item in cell if _local_name(item.tag) == "f"), None)
                    value = "=" + (formula.text or "") if formula is not None else ""
                if value:
                    cells.append(f"{ref}={value}" if ref else value)
                if len(cells) >= 100:
                    break
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            sheets.append(f"Sheet: {sheet_name}\n" + "\n".join(rows))
    return "\n\n".join(sheets), {"worksheets": len(sheet_paths), "extracted_worksheets": min(len(sheet_paths), 50)}


def _odf_inline(node: ET.Element) -> str:
    parts = [node.text or ""]
    for child in node:
        local = _local_name(child.tag)
        if local == "s":
            key = next((value for name, value in child.attrib.items() if name.endswith("}c")), "1")
            try:
                parts.append(" " * min(100, max(1, int(key))))
            except ValueError:
                parts.append(" ")
        elif local == "tab":
            parts.append("\t")
        elif local == "line-break":
            parts.append("\n")
        else:
            parts.append(_odf_inline(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _odt(archive: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    root = _xml(_member(archive, "content.xml"), "content.xml")
    paragraphs = []
    for node in root.iter():
        if _local_name(node.tag) in {"h", "p"}:
            value = _odf_inline(node).strip()
            if value:
                paragraphs.append(value)
    return "\n".join(paragraphs), {"paragraphs": len(paragraphs)}


def _ods(archive: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    root = _xml(_member(archive, "content.xml"), "content.xml")
    sheets = []
    table_tag = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table"
    for table in (item for item in root.iter() if item.tag == table_tag):
        name = next((value for key, value in table.attrib.items() if key.endswith("}name")), "Sheet")
        rows_out = []
        row_tag = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-row"
        cell_tags = {"{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-cell", "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}covered-table-cell"}
        for row in (item for item in table.iter() if item.tag == row_tag):
            cells = []
            for cell in row:
                if cell.tag not in cell_tags:
                    continue
                value = _odf_inline(cell).strip()
                if not value:
                    value = next((item for key, item in cell.attrib.items() if key.endswith("}value") or key.endswith("}date-value")), "")
                repeat_value = next((item for key, item in cell.attrib.items() if key.endswith("}number-columns-repeated")), "1")
                try:
                    repeat = min(MAX_REPEATED_CELLS, max(1, int(repeat_value)))
                except ValueError:
                    repeat = 1
                cells.extend([value] * repeat)
                if len(cells) >= 100:
                    break
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):
                rows_out.append(" | ".join(cells[:100]))
            if len(rows_out) >= MAX_TABLE_ROWS:
                break
        if rows_out:
            sheets.append(f"Sheet: {name}\n" + "\n".join(rows_out))
    return "\n\n".join(sheets), {"worksheets": len(sheets)}


def _odp(archive: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    root = _xml(_member(archive, "content.xml"), "content.xml")
    pages = []
    for index, page in enumerate((item for item in root.iter() if _local_name(item.tag) == "page"), 1):
        paragraphs = [_odf_inline(item).strip() for item in page.iter() if _local_name(item.tag) in {"p", "h"}]
        paragraphs = [item for item in paragraphs if item]
        if paragraphs:
            pages.append(f"Slide {index}\n" + "\n".join(paragraphs))
    return "\n\n".join(pages), {"slides": len(pages)}


def _epub(archive: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    container = _xml(_member(archive, "META-INF/container.xml"), "META-INF/container.xml")
    opf_path = next((item.get("full-path") for item in container.iter() if _local_name(item.tag) == "rootfile"), None)
    if not opf_path or opf_path not in archive.namelist():
        raise DocumentReadError("EPUB sem pacote de conteúdo reconhecível")
    opf = _xml(_member(archive, opf_path), opf_path)
    manifest = {item.get("id", ""): item.get("href", "") for item in opf.iter() if _local_name(item.tag) == "item"}
    spine_ids = [item.get("idref", "") for item in opf.iter() if _local_name(item.tag) == "itemref"]
    base = posixpath.dirname(opf_path)
    chapters = []
    for index, item_id in enumerate(spine_ids[:200], 1):
        href = manifest.get(item_id, "")
        member = posixpath.normpath(posixpath.join(base, unquote(href.split("#", 1)[0])))
        if not href or member.startswith("../") or member not in archive.namelist():
            continue
        text = _html_to_text(_member(archive, member))
        if text:
            chapters.append(f"Section {index}\n{text}")
    return "\n\n".join(chapters), {"sections": len(spine_ids), "extracted_sections": len(chapters)}


def _open_xml_document(path: Path) -> tuple[str, str, dict[str, Any]]:
    archive = _open_zip(path)
    try:
        readers = {
            ".docx": _docx,
            ".xlsx": _xlsx,
            ".pptx": _pptx,
            ".odt": _odt,
            ".ods": _ods,
            ".odp": _odp,
            ".epub": _epub,
        }
        text, details = readers[path.suffix.casefold()](archive)
        return text, "python-stdlib-zip-xml", details
    finally:
        archive.close()


def _rtf(data: bytes) -> str:
    text = data.decode("cp1252", errors="replace")
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda match: bytes([int(match.group(1), 16)]).decode("cp1252"), text)
    text = re.sub(r"\\u(-?\d+)\??", lambda match: chr(int(match.group(1)) % 65536), text)
    text = re.sub(r"\\(?:par|line|row|page)\b-?\d*\s?", "\n", text, flags=re.I)
    text = re.sub(r"\\tab\b\s?", "\t", text, flags=re.I)
    text = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", text)
    text = re.sub(r"\\([\\{}])", r"\1", text)
    text = text.replace(r"\~", " ").replace(r"\_", "-")
    return re.sub(r"[{}]", "", text).strip()


def _notebook(data: bytes) -> str:
    try:
        notebook = json.loads(_decode_text(data))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise DocumentReadError(f"notebook inválido: {error}") from error
    sections = []
    for index, cell in enumerate(notebook.get("cells", []), 1):
        kind = str(cell.get("cell_type") or "cell")
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(str(item) for item in source)
        if source:
            sections.append(f"[{kind} {index}]\n{source}")
        for output in cell.get("outputs", []) if kind == "code" else []:
            plain = output.get("text") or (output.get("data") or {}).get("text/plain") or ""
            if isinstance(plain, list):
                plain = "".join(str(item) for item in plain)
            if plain:
                sections.append(f"[output {index}]\n{plain}")
    return "\n\n".join(sections)


def _email(data: bytes) -> str:
    message = BytesParser(policy=policy.default).parsebytes(data)
    parts = [f"From: {message.get('from', '')}", f"To: {message.get('to', '')}", f"Subject: {message.get('subject', '')}", f"Date: {message.get('date', '')}"]
    bodies = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                try:
                    bodies.append(part.get_content())
                except (LookupError, UnicodeError):
                    continue
    elif message.get_content_type() == "text/plain":
        try:
            bodies.append(message.get_content())
        except (LookupError, UnicodeError):
            pass
    return "\n".join(parts) + "\n\n" + "\n\n".join(str(item) for item in bodies)


def _read_bounded_file(path: Path, maximum: int = MAX_OUTPUT_CHARS * 4) -> tuple[str, bool]:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    truncated = len(data) > maximum
    text, text_truncated = _clip(data[:maximum].decode("utf-8", errors="replace"))
    return text, truncated or text_truncated


def _pdf(path: Path, timeout: float) -> tuple[str, str, list[str], bool]:
    executable = shutil.which("pdftotext")
    if not executable:
        raise DocumentReadError("PDF requer o utilitário local pdftotext")
    with tempfile.TemporaryDirectory(prefix="ia-local-pdf-") as directory:
        output_path = Path(directory) / "extracted.txt"
        try:
            result = subprocess.run(
                [executable, "-layout", str(path), str(output_path)],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DocumentReadError(f"extração de PDF falhou: {error}") from error
        if result.returncode != 0:
            raise DocumentReadError(result.stderr.strip() or "pdftotext não conseguiu ler o PDF")
        text, truncated = _read_bounded_file(output_path)
        warnings = ["texto extraído limitado a 200 mil caracteres"] if truncated else []
        return text, "pdftotext", warnings, truncated


def _image_ocr(path: Path, timeout: float) -> tuple[str, str, list[str]]:
    executable = shutil.which("tesseract")
    if not executable:
        raise DocumentReadError("OCR local indisponível: instale Tesseract para extrair texto de imagens")
    try:
        result = subprocess.run([executable, str(path), "stdout", "--dpi", "300"], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DocumentReadError(f"OCR local falhou: {error}") from error
    if result.returncode != 0:
        raise DocumentReadError(result.stderr.strip() or "Tesseract não conseguiu ler a imagem")
    return result.stdout, "tesseract", []


def _legacy_office(path: Path, timeout: float) -> tuple[str, str, list[str], bool]:
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        raise DocumentReadError("formato Office legado requer LibreOffice local")
    extension = path.suffix.casefold()
    target_format = "csv" if extension == ".xls" else "txt"
    warnings = ["para planilhas .xls, o LibreOffice exporta a primeira planilha como CSV"] if extension == ".xls" else []
    with tempfile.TemporaryDirectory(prefix="ia-local-office-") as directory:
        output_dir = Path(directory) / "out"
        output_dir.mkdir()
        profile = (Path(directory) / "profile").resolve().as_uri()
        try:
            result = subprocess.run(
                [executable, "--headless", f"-env:UserInstallation={profile}", "--convert-to", target_format, "--outdir", str(output_dir), str(path)],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DocumentReadError(f"conversão local do Office falhou: {error}") from error
        converted = output_dir / f"{path.stem}.{target_format}"
        if result.returncode != 0 or not converted.is_file():
            raise DocumentReadError(result.stderr.strip() or result.stdout.strip() or "LibreOffice não conseguiu converter o arquivo")
        text, truncated = _read_bounded_file(converted)
        if truncated:
            warnings.append("texto convertido limitado a 200 mil caracteres")
        return text, "libreoffice", warnings, truncated


def read_document(path: str | Path, *, timeout: float = 15.0, max_chars: int = MAX_OUTPUT_CHARS) -> dict[str, Any]:
    """Lê um arquivo local compatível e devolve extração com status explícito."""
    file_path = Path(path)
    suffix = file_path.suffix.casefold()
    result: dict[str, Any] = {
        "schema": "local-document-text/v2",
        "path": str(file_path),
        "format": suffix.removeprefix(".") or "unknown",
        "mime": mimetypes.guess_type(file_path.name)[0],
        "text": "",
        "truncated": False,
        "backend": None,
        "warnings": [],
    }
    try:
        extraction_truncated = False
        if not file_path.is_file():
            raise DocumentReadError("o caminho não é um arquivo regular")
        size = file_path.stat().st_size
        result["bytes"] = size
        if size > MAX_INPUT_BYTES:
            raise DocumentReadError("arquivo acima do limite local de 64 MiB")
        if suffix == ".pdf":
            text, backend, warnings, extraction_truncated = _pdf(file_path, timeout)
            details = {}
        elif suffix in ZIP_DOCUMENT_EXTENSIONS:
            text, backend, details = _open_xml_document(file_path)
            warnings = []
            result["details"] = details
        elif suffix == ".rtf":
            text, backend, warnings = _rtf(file_path.read_bytes()), "python-stdlib-rtf", []
            details = {}
        elif suffix == ".ipynb":
            text, backend, warnings = _notebook(file_path.read_bytes()), "python-stdlib-json", []
            details = {}
        elif suffix == ".eml":
            text, backend, warnings = _email(file_path.read_bytes()), "python-stdlib-email", []
            details = {}
        elif suffix in {".doc", ".xls", ".ppt"}:
            text, backend, warnings, extraction_truncated = _legacy_office(file_path, timeout)
            details = {}
        elif suffix in IMAGE_EXTENSIONS:
            text, backend, warnings = _image_ocr(file_path, timeout)
            details = {}
        elif suffix in TEXT_EXTENSIONS:
            data = file_path.read_bytes()
            text = _html_to_text(data) if suffix in {".html", ".htm"} else _decode_text(data)
            backend, warnings, details = "python-stdlib", [], {}
        else:
            raise DocumentReadError(f"formato .{suffix.removeprefix('.')} sem extrator local registrado")
        maximum = min(MAX_OUTPUT_CHARS, max(1, int(max_chars)))
        text, truncated = _clip(str(text), maximum)
        truncated = truncated or extraction_truncated
        if truncated and not any(str(maximum) in warning for warning in warnings):
            warnings.append(f"retorno limitado a {maximum:,} caracteres")
        result.update({
            "status": "ok" if text.strip() else "no_text",
            "text": text,
            "characters": len(text),
            "truncated": truncated,
            "backend": backend,
            "warnings": warnings,
        })
        if details:
            result["details"] = details
        if not text.strip() and suffix == ".pdf":
            result["warnings"].append("nenhuma camada de texto foi encontrada; PDF pode conter apenas páginas escaneadas")
    except (OSError, DocumentReadError, ValueError, zipfile.BadZipFile) as error:
        result.update({"status": "unavailable" if isinstance(error, DocumentReadError) else "error", "error": str(error)[:1000]})
        result["characters"] = 0
    return result


def extract_document_text(path: str | Path, timeout: float = 15.0) -> dict[str, Any]:
    """Compatibilidade com o adaptador multimodal existente."""
    return read_document(path, timeout=timeout)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Extração local de texto de documentos autorizados")
    parser.add_argument("path", type=Path)
    parser.add_argument("--max-chars", type=int, default=MAX_OUTPUT_CHARS)
    args = parser.parse_args()
    print(json.dumps(read_document(args.path, max_chars=args.max_chars), ensure_ascii=False, separators=(",", ":")))
