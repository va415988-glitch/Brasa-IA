"""Gate estrutural para propostas de produtos full-stack.

Ele avalia o plano antes da aprovação de escrita. Não substitui a execução dos
checks do projeto; impede apenas que uma proposta incompleta seja chamada de
full-stack.
"""

from __future__ import annotations

import re
from typing import Any


CRITERIA = (
    "frontend",
    "backend",
    "contract",
    "persistence",
    "tests",
    "failure_states",
)


def assess_fullstack_plan(plan: dict[str, Any]) -> dict[str, Any]:
    operations = plan.get("operations") if isinstance(plan, dict) else None
    if not isinstance(operations, list):
        return {"passed": False, "criteria": {key: False for key in CRITERIA}, "reason": "operations ausentes"}
    paths: list[str] = []
    contents: list[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        args = operation.get("arguments") or {}
        if isinstance(args.get("path"), str):
            paths.append(args["path"].casefold())
        if isinstance(args.get("content"), str):
            contents.append(args["content"].casefold())
        if isinstance(args.get("new_text"), str):
            contents.append(args["new_text"].casefold())
    corpus = "\n".join(paths + contents)
    criteria = {
        "frontend": bool(re.search(r"(?:index\.html|frontend|components?|pages?|\.tsx?\b|\.jsx?\b|\.css\b|render|dom)", corpus)),
        "backend": bool(re.search(r"(?:backend|server|routes?|api|endpoint|controller|http\.server|express|fastapi|flask)", corpus)),
        "contract": bool(re.search(r"(?:contract|schema|request|response|status\s*code|openapi|\/api\/)", corpus)),
        "persistence": bool(re.search(r"(?:database|banco|sqlite|postgres|persistence|persist|storage|localstorage|repository)", corpus)),
        "tests": sum(bool(re.search(pattern, corpus)) for pattern in (r"test", r"assert", r"unittest", r"vitest", r"jest")) >= 2,
        "failure_states": bool(re.search(r"(?:error|erro|except|catch|loading|carregando|empty|vazio|4\\d\\d|5\\d\\d)", corpus)),
    }
    return {"passed": all(criteria.values()), "criteria": criteria,
            "missing": [key for key, value in criteria.items() if not value]}
