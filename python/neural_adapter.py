"""Camada local de competência para a IA neuro-simbólica.

O projeto não depende de outro modelo para responder aos casos críticos de
regressão. Esta camada lê exemplos supervisionados locais e faz recuperação
exata por intenção normalizada. Ela é deliberadamente pequena, transparente e
auditável: não inventa uma resposta quando não encontra um caso conhecido.

Ela não substitui o checkpoint neural. Serve como uma memória procedural de
alta confiança enquanto a geração livre do checkpoint continua experimental.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path


def normalize_question(value: object) -> str:
    text = str(value or "").strip().casefold()
    text = "".join(
        character for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", text).strip(" .!?\n\t")


class LocalCompetenceAdapter:
    """Recupera respostas autorais aprovadas para intenções bem conhecidas."""

    def __init__(self, dataset_path: str | Path):
        self.dataset_path = Path(dataset_path)
        self._answers: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.dataset_path.is_file():
            return
        for line in self.dataset_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            messages = row.get("messages") or []
            if len(messages) < 2:
                continue
            question = messages[0].get("content")
            answer = messages[1].get("content")
            if messages[0].get("role") != "user" or messages[1].get("role") != "assistant":
                continue
            key = normalize_question(question)
            if key and isinstance(answer, str) and answer.strip():
                self._answers[key] = answer.strip()

    def answer(self, messages: list[dict]) -> tuple[str, dict] | None:
        question = next(
            (item.get("content") for item in reversed(messages or []) if item.get("role") == "user"),
            "",
        )
        key = normalize_question(question)
        answer = self._answers.get(key)
        if not answer:
            return None
        return answer, {
            "backend": "local-competence-dataset",
            "dataset": str(self.dataset_path),
            "match": "normalized-exact-intent",
        }

    def __len__(self) -> int:
        return len(self._answers)
