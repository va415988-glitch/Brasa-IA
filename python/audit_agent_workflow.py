"""Audita um dataset agent-workflow/v1 sem importar exemplos para treino.

Implementa o subconjunto de JSON Schema usado por datasets/agent_workflow_v1
e aplica gates semânticos de revisão humana e divisão de dados. O conteúdo dos
registros é tratado como dado; nenhuma instrução presente neles é executada.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "datasets" / "agent_workflow_v1"


def _type_matches(value: Any, expected: str) -> bool:
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "boolean": lambda item: isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "null": lambda item: item is None,
    }
    check = checks.get(expected)
    return bool(check and check(value))


def _validate_schema(value: Any, schema: dict[str, Any], path: str = "$", errors: list[str] | None = None) -> list[str]:
    errors = [] if errors is None else errors
    expected = schema.get("type")
    if expected:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(value, item) for item in allowed):
            errors.append(f"{path}: tipo esperado {allowed}")
            return errors

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: valor deve ser {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: valor fora do enum permitido")

    if isinstance(value, str) and len(value) < int(schema.get("minLength", 0)):
        errors.append(f"{path}: texto menor que minLength")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            errors.append(f"{path}: lista menor que minItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema(item, item_schema, f"{path}[{index}]", errors)
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: campo obrigatório ausente: {name}")
        for name, child_schema in schema.get("properties", {}).items():
            if name in value and isinstance(child_schema, dict):
                _validate_schema(value[name], child_schema, f"{path}.{name}", errors)
    return errors


def audit(dataset_dir: Path) -> dict[str, Any]:
    schema_path = dataset_dir / "curated" / "schema.json"
    template_path = dataset_dir / "curated" / "example_template.json"
    curated_path = dataset_dir / "curated" / "examples.jsonl"
    candidates_path = dataset_dir / "raw" / "candidates_seed_v1.jsonl"

    errors: list[str] = []
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "errors": [f"schema: {exc}"]}
    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "errors": [f"template: {exc}"]}

    template_errors = _validate_schema(template, schema)
    if template_errors:
        errors.extend(f"template {error}" for error in template_errors)

    rows: list[dict[str, Any]] = []
    line_errors: list[str] = []
    row_counts: dict[str, int] = {}
    for file_path in (curated_path, candidates_path):
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            errors.append(f"{file_path.relative_to(dataset_dir)}: {exc}")
            continue
        relative_name = str(file_path.relative_to(dataset_dir))
        row_counts[relative_name] = sum(1 for line in lines if line.strip())
        for line_number, raw in enumerate(lines, 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                line_errors.append(f"{file_path.relative_to(dataset_dir)}:{line_number}: JSON inválido ({exc.msg})")
                continue
            if not isinstance(row, dict):
                line_errors.append(f"{file_path.relative_to(dataset_dir)}:{line_number}: registro não é objeto")
                continue
            row_errors = _validate_schema(row, schema)
            line_errors.extend(
                f"{file_path.relative_to(dataset_dir)}:{line_number}: {error}"
                for error in row_errors
            )
            rows.append(row)

    ids = [str(row.get("id") or "") for row in rows]
    duplicates = sorted(item for item, count in Counter(ids).items() if item and count > 1)
    if duplicates:
        errors.append(f"IDs duplicados: {duplicates}")

    actions: Counter[str] = Counter()
    task_types: Counter[str] = Counter()
    source_kinds: Counter[str] = Counter()
    reviewed_ids: list[str] = []
    trainable_ids: list[str] = []
    pending_ids: list[str] = []
    invariant_errors: list[str] = []
    for row in rows:
        example_id = str(row.get("id") or "<sem-id>")
        quality = row.get("quality") or {}
        reviewed = quality.get("human_reviewed") is True
        safe = quality.get("safe_to_train") is True
        split = row.get("split")
        if reviewed:
            reviewed_ids.append(example_id)
        if safe and not reviewed:
            invariant_errors.append(f"{example_id}: safe_to_train exige human_reviewed")
        if safe and split not in {"train", "validation", "test"}:
            invariant_errors.append(f"{example_id}: exemplo aprovado precisa de split explícito")
        if safe and reviewed and split in {"train", "validation", "test"}:
            trainable_ids.append(example_id)
        else:
            pending_ids.append(example_id)
        task_type = row.get("task_type")
        if task_type:
            task_types[str(task_type)] += 1
        source_kind = (row.get("source") or {}).get("kind")
        if source_kind:
            source_kinds[str(source_kind)] += 1
        for step in row.get("steps") or []:
            kind = ((step.get("action") or {}).get("kind")) if isinstance(step, dict) else None
            if kind:
                actions[str(kind)] += 1

    errors.extend(line_errors)
    errors.extend(invariant_errors)
    status = "invalid" if errors else ("ready_for_review" if not trainable_ids else "approved_examples_available")
    return {
        "schema": "agent-workflow-audit/v1",
        "status": status,
        "dataset_dir": str(dataset_dir),
        "files": {
            "schema": str(schema_path),
            "template": str(template_path),
            "curated": str(curated_path),
            "candidates": str(candidates_path),
        },
        "counts": {
            "curated_rows": row_counts.get("curated/examples.jsonl", 0),
            "candidate_rows": row_counts.get("raw/candidates_seed_v1.jsonl", 0),
            "parsed_rows": len(rows),
            "human_reviewed": len(reviewed_ids),
            "approved_for_training": len(trainable_ids),
            "pending_review": len(pending_ids),
        },
        "distribution": {
            "actions": dict(sorted(actions.items())),
            "task_types": dict(sorted(task_types.items())),
            "source_kinds": dict(sorted(source_kinds.items())),
        },
        "approved_ids": trainable_ids,
        "pending_ids": pending_ids,
        "errors": errors,
        "training_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audita dados agent-workflow/v1 sem treinar")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    result = audit(args.dataset_dir.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] != "invalid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
