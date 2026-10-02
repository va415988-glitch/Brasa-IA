"""Política comum para selecionar ferramentas de leitura por formato."""

from __future__ import annotations

import re
from pathlib import Path


DOCUMENT_READER_EXTENSIONS = frozenset({
    ".pdf", ".txt", ".md", ".markdown", ".rst", ".json", ".jsonl", ".csv", ".tsv",
    ".html", ".htm", ".xml", ".yaml", ".yml", ".toml", ".rtf", ".doc", ".docx",
    ".c", ".cc", ".cpp", ".h", ".hh", ".hpp", ".rs", ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rb", ".php", ".sh",
    ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".epub", ".ipynb",
    ".eml", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pbm", ".pgm",
    ".tif", ".tiff",
})

DOCUMENT_READ_TOOLS = frozenset({"read_file", "extract_document_text"})
EXTRACT_DOCUMENT_EXTENSIONS = frozenset({
    ".pdf", ".rtf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".epub", ".ipynb", ".eml",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pbm", ".pgm", ".tif", ".tiff",
})


def document_read_tool(path: str | Path) -> str:
    """Use extração para formatos especiais; texto e código usam leitura direta."""
    return "extract_document_text" if Path(str(path)).suffix.casefold() in EXTRACT_DOCUMENT_EXTENSIONS else "read_file"


def mentioned_document_paths(question: str) -> list[str]:
    """Retorna caminhos com extensões que o runtime sabe ler."""
    extension_pattern = "|".join(
        re.escape(item.removeprefix("."))
        for item in sorted(DOCUMENT_READER_EXTENSIONS, key=len, reverse=True)
    )
    return re.findall(
        rf"(?<![\w./-])[\w.-]+(?:/[\w.-]+)*\.(?:{extension_pattern})(?![\w])",
        str(question or ""),
        flags=re.I,
    )
