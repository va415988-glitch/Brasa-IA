"""Roteamento determinístico de tarefas para skills e contratos locais."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from capability_catalog import CapabilityCatalog
from dialogue import normalize
from tool_registry import ROOT
from cognitive_cores import CognitiveCoreRegistry
from competency import infer_domain


MANIFEST_PATH = ROOT / "skills" / "manifest.json"
EXTERNAL_INTENT = re.compile(
    r"\b(?:na internet|pela internet|na web|pela web|search the web|look up current|"
    r"pesquise online|pesquisar online|busque online|buscar online|na rede|fontes atuais|"
    r"fontes recentes|mais recente|atualizado hoje)\b",
    re.I,
)
LEARNING_INTENT = re.compile(r"\b(?:aprenda|estude)\b|\b(?:quero aprender|me ensine|ensine-me)\b", re.I)
AUTONOMOUS_CYCLE_INTENT = re.compile(
    r"\b(?:(?:inicie|iniciar|execute|executar|rode|rodar|continue|continuar)\s+(?:o\s+)?"
    r"(?:pr[oó]ximo\s+)?ciclo\b|pr[oó]ximo\s+ciclo\b).{0,80}\b"
    r"(?:aprendizado|aprendizagem|aut[oô]nomo)\b|\b(?:inicie|execute|continue|rode)\b"
    r".{0,40}\baprendizado aut[oô]nomo\b",
    re.I,
)


class SkillRouter:
    """Resolve uma skill com regras locais e devolve um próximo passo tipado.

    ``planner`` continua aceito por compatibilidade com inicializadores antigos,
    mas a seleção não consulta modelos, embeddings nem o ranking do planejador.
    O endpoint decide a rota; o AgentCore continua responsável por validar e
    executar ferramentas, respeitando os contratos e as aprovações existentes.
    """

    def __init__(
        self,
        catalog: CapabilityCatalog,
        planner: Any | None = None,
        manifest_path: Path | None = None,
        core_registry: CognitiveCoreRegistry | None = None,
        checkpoint_path: str | None = None,
    ):
        self.catalog = catalog
        self.core_registry = core_registry or CognitiveCoreRegistry()
        self.checkpoint_path = checkpoint_path
        path = manifest_path or MANIFEST_PATH
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "local-skills/v1" or not isinstance(payload.get("skills"), list):
            raise ValueError("manifesto de skills inválido")
        self.skills = payload["skills"]
        self._validate_skills()

    def _validate_skills(self) -> None:
        seen: set[str] = set()
        for skill in self.skills:
            if not isinstance(skill, dict):
                raise ValueError("skill deve ser um objeto")
            skill_id = skill.get("id")
            if not isinstance(skill_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", skill_id) or skill_id in seen:
                raise ValueError("ID de skill inválido ou duplicado")
            seen.add(skill_id)
            for key in ("required_capabilities", "optional_capabilities", "triggers", "domain_tags"):
                value = skill.get(key)
                if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                    raise ValueError(f"{key} deve ser uma lista de strings em {skill_id}")
            for key in ("excluded_triggers",):
                if key in skill and (not isinstance(skill[key], list) or any(not isinstance(item, str) for item in skill[key])):
                    raise ValueError(f"{key} deve ser uma lista de strings em {skill_id}")
            required_context = skill.get("required_context", [])
            if not isinstance(required_context, list) or any(not isinstance(item, str) for item in required_context):
                raise ValueError(f"required_context deve ser uma lista de strings em {skill_id}")
            capability_ids = skill["required_capabilities"] + skill["optional_capabilities"]
            for capability_id in capability_ids:
                if self.catalog.get(capability_id) is None:
                    raise ValueError(f"{skill_id} referencia capacidade desconhecida: {capability_id}")
            action_rules = skill.get("action_rules", [])
            if not isinstance(action_rules, list):
                raise ValueError(f"action_rules deve ser uma lista em {skill_id}")
            for rule in action_rules:
                if (not isinstance(rule, dict) or rule.get("capability") not in capability_ids
                        or not isinstance(rule.get("triggers"), list)
                        or any(not isinstance(trigger, str) for trigger in rule.get("triggers", []))
                        or not isinstance(rule.get("priority", 0), int)):
                    raise ValueError(f"regra de ação inválida em {skill_id}")

    @staticmethod
    def _phrase_matches(text: str, phrase: str) -> bool:
        normalized_phrase = normalize(phrase).strip()
        if not normalized_phrase:
            return False
        if " " in normalized_phrase:
            return normalized_phrase in text
        return bool(re.search(rf"\b{re.escape(normalized_phrase)}\b", text))

    @classmethod
    def _matched_triggers(cls, text: str, triggers: list[str]) -> list[str]:
        return [trigger for trigger in triggers if cls._phrase_matches(text, trigger)]

    @staticmethod
    def _score(matches: list[str]) -> float:
        if not matches:
            return 0.0
        # Frases específicas pesam mais que palavras soltas; sinais adicionais
        # da mesma skill aumentam a confiança sem permitir que um termo genérico
        # domine uma intenção explícita de outra área.
        longest = max(len(normalize(item).strip()) for item in matches)
        specificity = min(0.48, longest * 0.006)
        generic_penalty = 0.08 if longest <= 10 else 0.0
        corroboration = min(0.10, max(0, len(matches) - 1) * 0.025)
        return round(min(0.97, 0.37 + specificity - generic_penalty + corroboration), 3)

    @staticmethod
    def _availability(entry: dict[str, Any]) -> str:
        if entry.get("network_policy") == "external-optional":
            return "registered; provider configuration checked at execution"
        if entry.get("kind") == "service-api":
            return "local endpoint registered"
        return "contract validated in local tool registry"

    def _api_candidate(self, capability_id: str, required: bool) -> dict[str, Any] | None:
        entry = self.catalog.get(capability_id)
        if entry is None:
            return None
        common = {
            "id": capability_id,
            "kind": entry["kind"],
            "description": entry["description"] if entry.get("kind") == "service-api"
            else entry["contract"]["description"],
            "provider": entry.get("provider", self.catalog.provider_default),
            "network_policy": entry["network_policy"],
            "availability": self._availability(entry),
            "requires_approval": entry.get("requires_approval", entry.get("contract", {}).get("requires_approval", False)),
            "risk": entry.get("risk", entry.get("contract", {}).get("risk", "unknown")),
            "required_by_selected_skill": required,
            "execution": "not_executed_by_router",
            "dispatchable": True,
        }
        if entry.get("kind") == "service-api":
            request_schema = entry["request_schema"]
            return {
                **common,
                "method": entry["method"],
                "path": entry["path"],
                "request_schema": request_schema,
                "required_inputs": list(request_schema.get("required", [])),
                "response_schema": entry["response_schema"],
                "side_effects": entry["side_effects"],
                "async": entry["async"],
            }
        contract = entry["contract"]
        arguments = contract.get("arguments", {})
        return {
            **common,
            "tool": entry["tool"],
            "arguments_schema": arguments,
            "required_inputs": list(arguments.get("required", [])),
        }

    @staticmethod
    def _action_rule(skill: dict[str, Any], text: str) -> str | None:
        rules = sorted(skill.get("action_rules", []), key=lambda item: item.get("priority", 0), reverse=True)
        for rule in rules:
            if any(SkillRouter._phrase_matches(text, trigger) for trigger in rule["triggers"]):
                return rule["capability"]
        return None

    def _next_action(
        self,
        selected_definitions: list[dict[str, Any]],
        api_candidates: list[dict[str, Any]],
        text: str,
        needs_clarification: bool,
    ) -> dict[str, Any]:
        if needs_clarification:
            return {"type": "clarify", "target": "user", "reason": "intenção ausente, fraca ou ambígua"}
        if not selected_definitions:
            return {"type": "clarify", "target": "user", "reason": "nenhuma skill local correspondeu à tarefa"}
        preferred = next(
            (self._action_rule(skill, text) for skill in selected_definitions if self._action_rule(skill, text)),
            None,
        )
        chosen = next((item for item in api_candidates if item["id"] == preferred), None) if preferred else None
        if chosen is None:
            chosen = next((item for item in api_candidates if item["required_by_selected_skill"]), None)
        if chosen is None:
            chosen = next(iter(api_candidates), None)
        if chosen is None:
            return {
                "type": "compose_response",
                "target": "local-dialogue",
                "skill_id": selected_definitions[0]["id"],
                "reason": "a skill não exige uma API externa ao núcleo de diálogo",
            }
        return {
            "type": "dispatch",
            "target": "agentcore" if chosen["kind"] == "tool" else "local-service-api",
            "skill_id": selected_definitions[0]["id"],
            "capability_id": chosen["id"],
            **({"tool": chosen["tool"]} if chosen["kind"] == "tool" else {
                "method": chosen["method"], "path": chosen["path"],
            }),
            "requires_approval": chosen["requires_approval"],
            "execution_gate": "approval_required" if chosen["requires_approval"] else "contract_validation",
            "arguments_schema": chosen.get("arguments_schema", chosen.get("request_schema")),
            "required_inputs": chosen.get("required_inputs", []),
            "arguments_ready": not chosen.get("required_inputs"),
            "reason": "próximo passo escolhido por gatilho e contrato local",
        }

    def route(self, task: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task é obrigatória")
        task = task.strip()
        if len(task) > 8000:
            raise ValueError("task excede 8000 caracteres")
        if context is not None and not isinstance(context, dict):
            raise ValueError("context deve ser um objeto")

        text = normalize(task)
        context = context or {}
        learning_requested = bool(LEARNING_INTENT.search(task))
        autonomous_cycle_requested = bool(AUTONOMOUS_CYCLE_INTENT.search(task))
        external_requested = bool(EXTERNAL_INTENT.search(text) or learning_requested or autonomous_cycle_requested)
        candidate_skills: list[dict[str, Any]] = []
        missing_context_requirements: list[dict[str, str]] = []

        for skill in self.skills:
            if any(self._phrase_matches(text, trigger) for trigger in skill.get("excluded_triggers", [])):
                continue
            matches = self._matched_triggers(text, skill["triggers"])
            if not matches:
                continue
            required_context = skill.get("required_context", [])
            unmet_context = [key for key in required_context if context.get(key) is not True]
            if unmet_context:
                missing_context_requirements.extend(
                    {"skill_id": skill["id"], "context": key} for key in unmet_context
                )
                continue
            score = self._score(matches)
            # Um erro observado em testes pede diagnóstico antes da correção.
            # A frase genérica "corrija o erro" não deve vencer os sinais
            # específicos de pytest/testes com falha.
            if (skill["id"] == "test-debugging"
                    and re.search(r"\b(?:testes?|pytest)\b", text)
                    and re.search(r"\b(?:falh\w*|erro|corrij\w*|investig\w*)\b", text)):
                score = min(0.97, round(score + 0.20, 3))
            if not score:
                continue
            candidate_skills.append({
                "id": skill["id"],
                "label": skill["label"],
                "description": skill["description"],
                "score": score,
                "matched_triggers": matches,
                "domain_tags": skill["domain_tags"],
                "required_capabilities": skill["required_capabilities"],
                "optional_capabilities": skill["optional_capabilities"],
            })

        candidate_skills.sort(key=lambda item: (-item["score"], item["id"]))
        top = candidate_skills[0] if candidate_skills else None
        second = candidate_skills[1] if len(candidate_skills) > 1 else None
        candidate_ids = {item["id"] for item in candidate_skills}
        composed_learning = bool(
            learning_requested and external_requested
            and {"knowledge-learning", "external-research"}.issubset(candidate_ids)
        )
        composed_autonomous = autonomous_cycle_requested and bool(
            top and top["id"] == "autonomous-learning-cycle"
        )
        ambiguous_top = bool(
            top and second and top["score"] - second["score"] < 0.07
            and not composed_learning and not composed_autonomous
        )
        confidence = top["score"] if top else 0.0
        needs_clarification = confidence < 0.30 or ambiguous_top

        selected = []
        if top and not needs_clarification:
            selected = [top]
            if composed_learning:
                selected = [item for item in candidate_skills if item["id"] in {"knowledge-learning", "external-research"}]
            elif composed_autonomous:
                selected = [top]

        selected_ids = {item["id"] for item in selected}
        selected_definitions = [skill for skill in self.skills if skill["id"] in selected_ids]
        required_ids: list[str] = []
        optional_ids: list[str] = []
        for skill in selected_definitions:
            required_ids.extend(skill["required_capabilities"])
            optional_ids.extend(skill["optional_capabilities"])
        required_ids = list(dict.fromkeys(required_ids))
        optional_ids = [item for item in dict.fromkeys(optional_ids) if item not in required_ids]

        api_candidates = []
        for capability_id in required_ids + optional_ids:
            entry = self.catalog.get(capability_id)
            if entry is None or (entry["network_policy"] == "external-optional" and not external_requested):
                continue
            api = self._api_candidate(capability_id, capability_id in required_ids)
            if api:
                api_candidates.append(api)

        # As regras de ação podem elevar uma capacidade opcional ao primeiro
        # passo (por exemplo, consultar o estado de um processo em vez de iniciá-lo).
        preferred_ids = [
            capability for skill in selected_definitions
            if (capability := self._action_rule(skill, text)) is not None
        ]
        api_candidates.sort(key=lambda item: (
            item["id"] not in preferred_ids,
            not item["required_by_selected_skill"],
            item["id"],
        ))
        next_action = self._next_action(selected_definitions, api_candidates, text, needs_clarification)
        return {
            "schema": "skill-route/v2",
            "mode": "deterministic",
            "execution_allowed": False,
            "actionable": True,
            "dispatch_target": "local-agentcore",
            "task": task,
            "network_requested": external_requested,
            "learning_requested": learning_requested,
            "autonomous_cycle_requested": autonomous_cycle_requested,
            "selected_skills": selected,
            "candidate_skills": candidate_skills[:8],
            "api_candidates": api_candidates,
            "next_action": next_action,
            "context_requirements": missing_context_requirements,
            "clarification_question": (
                "Selecione um projeto/workspace ativo para consultar o estado do sistema."
                if missing_context_requirements and not selected else None
            ),
            "confidence": round(confidence, 3),
            "needs_clarification": needs_clarification,
            "routing_reason": (
                "ambiguidade entre skills candidatas"
                if ambiguous_top else
                "falta contexto de workspace para identificar o projeto ativo"
                if missing_context_requirements and not selected else
                "correspondência determinística por gatilhos, contexto e contratos locais"
                if selected else
                "nenhuma skill atingiu o limiar de roteamento"
            ),
            "execution_note": "O roteador entrega a decisão; AgentCore valida argumentos e aplica as travas do contrato antes de executar.",
            "core_plan": self.core_registry.plan(
                [item['id'] for item in selected], infer_domain(question=task), self.checkpoint_path,
            ),
        }
