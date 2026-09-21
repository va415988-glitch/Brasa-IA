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
        if not isinstance(arguments, dict):
            return False, "argumentos devem ser um objeto JSON"
        schema = self.tools[name].get("arguments") or {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        missing = [key for key in required if key not in arguments]
        if missing:
            return False, f"argumentos obrigatórios ausentes: {', '.join(missing)}"
        if isinstance(schema, dict) and schema.get("additionalProperties") is False:
            properties = schema.get("properties", {})
            unknown = sorted(set(arguments) - set(properties))
            if unknown:
                return False, f"argumentos não permitidos: {', '.join(unknown)}"
        for key, spec in (schema.get("properties", {}) if isinstance(schema, dict) else {}).items():
            if key not in arguments or not isinstance(spec, dict):
                continue
            expected = spec.get("type")
            value = arguments[key]
            expected_types = expected if isinstance(expected, list) else [expected]
            valid = {
                "string": isinstance(value, str),
                "boolean": isinstance(value, bool),
                "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "array": isinstance(value, list),
                "object": isinstance(value, dict),
                "null": value is None,
            }
            type_valid = any(valid.get(item, True) for item in expected_types)
            if not type_valid:
                return False, f"argumento `{key}` deve ser do tipo {expected}"
            if "enum" in spec and value not in spec["enum"]:
                return False, f"argumento `{key}` tem valor inválido"
            if isinstance(value, str):
                if "minLength" in spec and len(value) < int(spec["minLength"]):
                    return False, f"argumento `{key}` é curto demais"
                if "maxLength" in spec and len(value) > int(spec["maxLength"]):
                    return False, f"argumento `{key}` excede o limite de tamanho"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "minimum" in spec and value < spec["minimum"]:
                    return False, f"argumento `{key}` abaixo do mínimo permitido"
                if "maximum" in spec and value > spec["maximum"]:
                    return False, f"argumento `{key}` acima do máximo permitido"
            if isinstance(value, list) and isinstance(spec.get("items"), dict):
                item_type = spec["items"].get("type")
                if item_type and any(not self._matches_type(item, item_type) for item in value):
                    return False, f"argumento `{key}` contém item de tipo inválido"
        return True, ""

    @staticmethod
    def _matches_type(value: Any, expected: str | list[str]) -> bool:
        expected_types = expected if isinstance(expected, list) else [expected]
        return any({
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
            "null": value is None,
        }.get(item, True) for item in expected_types)


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
