"""Catálogo único das ferramentas expostas ao agente local.

Os contratos JSON são a fonte de verdade. O roteador pode usar o catálogo para
descobrir nomes, argumentos e riscos sem duplicar a lista em cada cliente.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent


class ToolRegistry:
    def __init__(self, contracts_dir: Path | None = None):
        self.contracts_dir = contracts_dir or ROOT / "contracts"
        self.tools: dict[str, dict] = {}
        self.errors: dict[str, str] = {}
        for path in sorted(self.contracts_dir.glob("*.json")):
            try:
                contract = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            name = contract.get("name")
            if name:
                name = str(name)
                error = self.validate_contract(contract)
                if error:
                    self.errors[name] = error
                self.tools[name] = contract

    @staticmethod
    def validate_contract(contract: dict[str, Any]) -> str:
        """Valida a forma do contrato antes de expô-lo ao planejador.

        A execução continua sendo responsabilidade do Rust, mas o planejador
        não deve receber um catálogo que não consiga descrever com precisão.
        """
        if not isinstance(contract, dict):
            return "contrato deve ser um objeto JSON"
        name = contract.get("name")
        if not isinstance(name, str) or not name.strip():
            return "nome da ferramenta ausente"
        arguments = contract.get("arguments", {})
        if not isinstance(arguments, dict):
            return "arguments deve ser um schema JSON object"
        if arguments.get("type", "object") != "object":
            return "arguments deve declarar type=object"
        properties = arguments.get("properties", {})
        if not isinstance(properties, dict):
            return "arguments.properties deve ser um objeto"
        required = arguments.get("required", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            return "arguments.required deve ser uma lista de strings"
        unknown_required = sorted(set(required) - set(properties))
        if unknown_required:
            return f"required referencia campos ausentes: {', '.join(unknown_required)}"
        return ""

    def _contract(self, name: str) -> dict:
        contract = self.tools[name]
        if name in self.errors:
            raise ValueError(f"contrato inválido para {name}: {self.errors[name]}")
        return contract

    def has(self, name: str) -> bool:
        return name in self.tools and name not in self.errors

    def describe(self, name: str) -> dict:
        contract = self._contract(name)
        side_effects = contract.get("side_effects", False)
        if isinstance(side_effects, str):
            side_effects = True
        risk = contract.get("risk") or ("write" if side_effects else "read")
        capabilities = contract.get("capabilities")
        if not isinstance(capabilities, list):
            capabilities = ["workspace.write" if risk == "write" else "workspace.read"]
        return {
            "name": name,
            "version": str(contract.get("version", "1.0.0")),
            "description": contract.get("description", ""),
            "arguments": contract.get("arguments", {}),
            "requires_approval": contract.get("requires_approval", False),
            "side_effects": bool(side_effects),
            "side_effect_description": contract.get("side_effects") if isinstance(contract.get("side_effects"), str) else None,
            "capabilities": capabilities,
            "risk": risk,
            "idempotent": bool(contract.get("idempotent", not side_effects)),
            "timeout_ms": int(contract.get("timeout_ms", 10_000)),
            "limits": contract.get("limits", {}),
            "preconditions": contract.get("preconditions", []),
            "effects": contract.get("effects", []),
            "rollback": contract.get("rollback"),
            "retry_policy": contract.get("retry_policy", "safe_only"),
            "result": contract.get("result", contract.get("returns", {})),
        }

    def all(self) -> list[dict]:
        return [self.describe(name) for name in sorted(self.tools)]

    def validate_arguments(self, name: str, arguments: dict) -> tuple[bool, str]:
        if not self.has(name):
            return False, f"ferramenta não registrada: {name}"
        return self._validate_schema(arguments, self.tools[name].get("arguments") or {}, "arguments")

    def validate_result(self, name: str, result: Any) -> tuple[bool, str]:
        """Valida o payload de saída antes de tratá-lo como evidência confiável."""
        if not self.has(name):
            return False, f"ferramenta não registrada: {name}"
        schema = self.tools[name].get("result") or {}
        if not schema:
            return True, ""
        return self._validate_schema(result, schema, "result")

    @classmethod
    def _validate_schema(cls, value: Any, schema: Any, path: str) -> tuple[bool, str]:
        if not isinstance(schema, dict):
            return True, ""

        if isinstance(schema.get("oneOf"), list):
            outcomes = [
                cls._validate_schema(value, candidate, path)
                for candidate in schema["oneOf"]
            ]
            matches = sum(valid for valid, _ in outcomes)
            if matches == 1:
                return True, ""
            return False, f"{path} não corresponde a exatamente uma forma permitida"

        if "const" in schema and value != schema["const"]:
            return False, f"{path} deve ser {schema['const']!r}"

        expected = schema.get("type")
        expected_types = expected if isinstance(expected, list) else [expected]
        expected_types = [item for item in expected_types if item]
        if expected_types and not any(cls._matches_type(value, item) for item in expected_types):
            return False, f"{path} deve ser do tipo {' ou '.join(expected_types)}"

        if "enum" in schema and value not in schema["enum"]:
            return False, f"{path} tem valor fora do conjunto permitido"

        if isinstance(value, str):
            if "minLength" in schema and len(value) < int(schema["minLength"]):
                return False, f"{path} é curto demais"
            if "maxLength" in schema and len(value) > int(schema["maxLength"]):
                return False, f"{path} excede o limite de tamanho"

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                return False, f"{path} está abaixo do mínimo permitido"
            if "maximum" in schema and value > schema["maximum"]:
                return False, f"{path} excede o máximo permitido"

        if isinstance(value, list):
            if "minItems" in schema and len(value) < int(schema["minItems"]):
                return False, f"{path} contém menos itens que o mínimo permitido"
            if "maxItems" in schema and len(value) > int(schema["maxItems"]):
                return False, f"{path} excede o máximo de itens permitido"
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    valid, error = cls._validate_schema(item, item_schema, f"{path}[{index}]")
                    if not valid:
                        return valid, error

        if isinstance(value, dict):
            required = schema.get("required", [])
            if isinstance(required, list):
                missing = [key for key in required if key not in value]
                if missing:
                    return False, f"{path} sem campos obrigatórios: {', '.join(missing)}"
            properties = schema.get("properties", {})
            if not isinstance(properties, dict):
                properties = {}
            if schema.get("additionalProperties") is False:
                unknown = sorted(set(value) - set(properties))
                if unknown:
                    return False, f"{path} contém campos não permitidos: {', '.join(unknown)}"
            for key, child_schema in properties.items():
                if key not in value:
                    continue
                valid, error = cls._validate_schema(value[key], child_schema, f"{path}.{key}")
                if not valid:
                    return valid, error
        return True, ""

    @staticmethod
    def _matches_type(value: Any, expected: str | list[str]) -> bool:
        expected_types = expected if isinstance(expected, list) else [expected]
        checks = {
            "string": lambda item: isinstance(item, str),
            "boolean": lambda item: isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "array": lambda item: isinstance(item, list),
            "object": lambda item: isinstance(item, dict),
            "null": lambda item: item is None,
        }
        return any(checks.get(item, lambda _value: True)(value) for item in expected_types)


def make_tool_call(
    registry: ToolRegistry,
    name: str,
    arguments: dict,
    reason: str,
    *,
    dry_run: bool = False,
    idempotency_key: str | None = None,
) -> dict:
    if not registry.has(name):
        raise KeyError(f"ferramenta não registrada: {name}")
    valid, error = registry.validate_arguments(name, arguments)
    if not valid:
        raise ValueError(f"chamada inválida para {name}: {error}")
    description = registry.describe(name)
    if name == "research_web":
        if arguments.get("save_to_corpus") is True:
            description = {**description, "requires_approval": True}
        else:
            description = {
                **description,
                "side_effects": False,
                "capabilities": ["network.read"],
                "risk": "read",
                "idempotent": True,
            }
    return {
        "id": f"call-{uuid.uuid4().hex}",
        "tool": name,
        "arguments": arguments,
        "reason": str(reason)[:1000],
        "requires_approval": bool(description["requires_approval"]),
        "contract_version": description["version"],
        "capabilities": description["capabilities"],
        "risk": description["risk"],
        "dry_run": bool(dry_run),
        "idempotency_key": idempotency_key or f"idem-{uuid.uuid4().hex}",
        "lifecycle": {"status": "requested", "retry_policy": description["retry_policy"]},
    }
