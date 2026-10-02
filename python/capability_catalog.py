"""Catálogo local de APIs/capacidades derivado dos contratos de ferramentas."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tool_registry import ROOT, ToolRegistry


METADATA_PATH = ROOT / "capabilities" / "metadata.json"
VALID_NETWORK_POLICIES = {"offline", "workspace-inherited", "external-optional"}


class CapabilityCatalog:
    """Combina contratos executáveis com metadados de domínio e provider.

    Os contratos JSON seguem sendo a fonte dos argumentos, efeitos e schemas;
    o manifesto adiciona IDs estáveis, domínios, provider e política de rede.
    """

    def __init__(self, registry: ToolRegistry | None = None, metadata_path: Path | None = None):
        self.registry = registry or ToolRegistry()
        self.metadata_path = metadata_path or METADATA_PATH
        payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        if payload.get("schema") != "local-api-metadata/v1":
            raise ValueError("schema do catálogo de capacidades inválido")
        self.provider_default = str(payload.get("provider_default") or "local-runtime")
        metadata = payload.get("capabilities")
        service_metadata = payload.get("service_apis", [])
        if not isinstance(metadata, list):
            raise ValueError("capabilities deve ser uma lista")
        if not isinstance(service_metadata, list):
            raise ValueError("service_apis deve ser uma lista")

        by_tool: dict[str, dict[str, Any]] = {}
        by_id: dict[str, dict[str, Any]] = {}
        service_apis_by_id: dict[str, dict[str, Any]] = {}
        for entry in metadata:
            self._validate_metadata(entry)
            tool = entry["tool"]
            if tool in by_tool or entry["id"] in by_id:
                raise ValueError("ID de capacidade ou ferramenta duplicada")
            if not self.registry.has(tool):
                raise ValueError(f"capacidade referencia ferramenta indisponível: {tool}")
            contract = self.registry.describe(tool)
            by_tool[tool] = {**entry, "kind": "tool", "contract": contract}
            by_id[entry["id"]] = by_tool[tool]

        seen_endpoints: set[tuple[str, str]] = set()
        for entry in service_metadata:
            self._validate_service_api(entry)
            endpoint = (entry["method"], entry["path"])
            if entry["id"] in by_id or endpoint in seen_endpoints:
                raise ValueError("ID ou endpoint de service API duplicado")
            seen_endpoints.add(endpoint)
            service_entry = {**entry, "kind": "service-api"}
            service_apis_by_id[entry["id"]] = service_entry
            by_id[entry["id"]] = service_entry

        contract_tools = {name for name in self.registry.tools if self.registry.has(name)}
        if contract_tools != set(by_tool):
            missing = sorted(contract_tools - set(by_tool))
            stale = sorted(set(by_tool) - contract_tools)
            raise ValueError(f"catálogo divergente; sem metadata={missing}; sem contrato={stale}")
        self.by_tool = by_tool
        self.service_apis_by_id = service_apis_by_id
        self.by_id = by_id

    @staticmethod
    def _validate_service_api(entry: Any) -> None:
        if not isinstance(entry, dict):
            raise ValueError("service API deve ser objeto")
        api_id = entry.get("id")
        method = entry.get("method")
        path = entry.get("path")
        tags = entry.get("domain_tags")
        policy = entry.get("network_policy")
        if not isinstance(api_id, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*", api_id):
            raise ValueError("ID de service API inválido")
        if method not in {"GET", "POST"} or not isinstance(path, str) or not path.startswith("/api/"):
            raise ValueError(f"endpoint local inválido para {api_id}")
        if not isinstance(entry.get("provider"), str) or not entry["provider"].strip():
            raise ValueError(f"provider inválido para {api_id}")
        if not isinstance(entry.get("description"), str) or not entry["description"].strip():
            raise ValueError(f"description inválida para {api_id}")
        if not isinstance(tags, list) or not tags or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            raise ValueError(f"domain_tags inválidas para {api_id}")
        if policy not in VALID_NETWORK_POLICIES:
            raise ValueError(f"network_policy inválida para {api_id}")
        if not isinstance(entry.get("requires_approval"), bool) or not isinstance(entry.get("idempotent"), bool):
            raise ValueError(f"política de execução inválida para {api_id}")
        side_effects = entry.get("side_effects")
        if not (isinstance(side_effects, bool) or isinstance(side_effects, str) and side_effects.strip()):
            raise ValueError(f"side_effects inválido para {api_id}")
        if not isinstance(entry.get("risk"), str) or not entry["risk"].strip():
            raise ValueError(f"risk inválido para {api_id}")
        if not isinstance(entry.get("request_schema"), dict) or not isinstance(entry.get("response_schema"), dict):
            raise ValueError(f"schemas de entrada/saída inválidos para {api_id}")
        if not isinstance(entry.get("async"), bool):
            raise ValueError(f"async inválido para {api_id}")
        timeout_ms = entry.get("timeout_ms")
        if not isinstance(timeout_ms, int) or not 100 <= timeout_ms <= 900000:
            raise ValueError(f"timeout_ms inválido para {api_id}")

    @staticmethod
    def _validate_metadata(entry: Any) -> None:
        if not isinstance(entry, dict):
            raise ValueError("metadado de capacidade deve ser objeto")
        capability_id = entry.get("id")
        tool = entry.get("tool")
        tags = entry.get("domain_tags")
        policy = entry.get("network_policy")
        if not isinstance(capability_id, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*", capability_id):
            raise ValueError("ID de capacidade inválido")
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError("capacidade sem ferramenta")
        if not isinstance(tags, list) or not tags or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            raise ValueError(f"domain_tags inválidas para {tool}")
        if policy not in VALID_NETWORK_POLICIES:
            raise ValueError(f"network_policy inválida para {tool}")

    def get(self, capability_id: str) -> dict[str, Any] | None:
        return self.by_id.get(capability_id)

    def for_tool(self, tool: str) -> dict[str, Any] | None:
        return self.by_tool.get(tool)

    def public_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        contract = entry["contract"]
        return {
            "id": entry["id"],
            "tool": entry["tool"],
            "version": contract["version"],
            "description": contract["description"],
            "domain_tags": list(entry["domain_tags"]),
            "provider": self.provider_default,
            "network_policy": entry["network_policy"],
            "availability": "contract_valid",
            "requires_approval": contract["requires_approval"],
            "risk": contract["risk"],
            "idempotent": contract["idempotent"],
            "timeout_ms": contract["timeout_ms"],
            "arguments": contract["arguments"],
            "result": contract["result"],
        }

    @staticmethod
    def public_service_entry(entry: dict[str, Any]) -> dict[str, Any]:
        return {key: entry[key] for key in (
            "id", "method", "path", "description", "domain_tags", "provider",
            "network_policy", "side_effects", "requires_approval", "risk",
            "idempotent", "timeout_ms", "async", "request_schema", "response_schema",
        ) if key in entry} | {"kind": "service-api", "availability": "local-endpoint-registered"}

    def snapshot(self) -> dict[str, Any]:
        entries = [self.public_entry(self.by_tool[name]) for name in sorted(self.by_tool)]
        service_apis = [self.public_service_entry(self.service_apis_by_id[api_id])
                        for api_id in sorted(self.service_apis_by_id)]
        return {
            "schema": "local-api-catalog/v1",
            "mode": "offline-first",
            "provider_default": self.provider_default,
            "count": len(entries),
            "external_optional_count": sum(item["network_policy"] == "external-optional" for item in entries),
            "service_api_count": len(service_apis),
            "service_external_optional_count": sum(item["network_policy"] == "external-optional" for item in service_apis),
            "capabilities": entries,
            "service_apis": service_apis,
        }
