"""Estado persistente de competências e experiência do agente.

O corpus guarda fontes. Este módulo guarda o que o agente consegue fazer com
essas fontes: estágio, evidências, lacunas e práticas verificadas. Nenhuma
fonte isolada promove uma competência para ``mastered``.
"""

from __future__ import annotations

import json
import threading
import time
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "corpus" / "agent" / "skills.json"
LOCK = threading.Lock()


def canonical_topic(topic: str) -> str:
    value = re.sub(r"\s+", " ", str(topic)).strip().strip('"\'“”')
    value = re.sub(r"\b(?:profundamente|a fundo|em profundidade|com profundidade|de forma profunda|detalhadamente)\b", "", value, flags=re.I)
    lower = value.casefold()
    aliases = (
        (("doc.rust-lang.org", "rustlings.rust-lang.org", "tokio-rs/axum"), "Rust"),
        (("ziglang.org",), "Zig"),
        (("typescriptlang.org",), "TypeScript"),
        (("nodejs.org",), "Node.js"),
        (("learn.microsoft.com/dotnet/csharp",), "C#"),
        (("dev.java",), "Java"),
        (("learncpp.com",), "C++"),
        (("python.org", "thealgorithms/python"), "Python"),
        (("sqlbolt.com",), "SQL"),
        (("cheatsheetseries.owasp.org",), "OWASP"),
    )
    for needles, canonical in aliases:
        if any(needle in lower for needle in needles):
            return canonical
    return re.sub(r"\s+", " ", value).strip(" .,:;-") or value


def _empty(topic: str) -> dict[str, Any]:
    return {
        "topic": topic,
        "status": "discovered",
        "confidence": 0.0,
        "version": None,
        "concepts": {"covered": [], "gaps": []},
        "curriculum": {"schema": "curriculum/v1", "levels": [], "completion": {}},
        "evidence": {"sources": [], "independent_hosts": 0, "documents": 0},
        "source_repository": None,
        "technologies": [],
        "practice": {"tasks": 0, "passed": 0, "failed": 0, "verified": []},
        "decisions": [],
        "updated_at": None,
    }


class AgentState:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema": "agent-state/v1", "skills": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"schema": "agent-state/v1", "skills": {}}
        except (OSError, json.JSONDecodeError):
            return {"schema": "agent-state/v1", "skills": {}}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def _evaluation(skill: dict[str, Any]) -> dict[str, Any]:
        curriculum = skill.get("curriculum") or {}
        configured = curriculum.get("completion") or {}
        requirements = {
            "required_levels": int(configured.get("required_levels", 3) or 3),
            "required_pass_rate": float(configured.get("required_pass_rate", 0.9) or 0.9),
            "required_independent_hosts": int(configured.get("required_independent_hosts", 2) or 2),
            "minimum_document_count": int(configured.get("minimum_document_count", 8) or 8),
            "minimum_practice_tasks": int(configured.get("minimum_practice_tasks", 12) or 12),
            "minimum_transfer_tasks": int(configured.get("minimum_transfer_tasks", 2) or 2),
            # Migração compatível: competências antigas podem ter sido
            # gravadas com 0.9. O contrato atual exige cobertura total, então
            # nenhuma delas pode continuar MASTERED com uma lacuna aberta.
            "required_covered_ratio": max(1.0, float(configured.get("required_covered_ratio", 1.0) or 1.0)),
            "requires_integration_task": bool(configured.get("requires_integration_task", True)),
        }
        levels = [level for level in curriculum.get("levels", []) if level.get("concepts")]
        concepts = [concept for level in levels for concept in level.get("concepts", [])]
        covered = [concept for concept in concepts if concept.get("status") == "covered"]
        coverage_ratio = len(covered) / len(concepts) if concepts else 0.0
        practice = skill.get("practice") or {}
        tasks = int(practice.get("tasks", 0) or 0)
        passed = int(practice.get("passed", 0) or 0)
        failed = int(practice.get("failed", 0) or 0)
        pass_rate = passed / tasks if tasks else 0.0
        verified = practice.get("verified") or []
        transfer_tasks = sum(
            1 for item in verified
            if item.get("level") == "transfer"
            or "transfer" in str(item.get("task", "")).lower()
            or "unseen" in str(item.get("task", "")).lower()
        )
        integration = any(
            item.get("level") == "integration"
            or "integration" in str(item.get("task", "")).lower()
            for item in verified
        )
        evidence = skill.get("evidence") or {}
        host_ratio = min(1.0, int(evidence.get("independent_hosts", 0) or 0) /
                         max(1, requirements["required_independent_hosts"]))
        document_ratio = min(1.0, int(evidence.get("documents", 0) or 0) /
                             max(1, requirements["minimum_document_count"]))
        evidence_progress = (host_ratio + document_ratio) / 2
        practice_progress = min(1.0, passed / max(1, requirements["minimum_practice_tasks"]))
        transfer_progress = min(1.0, transfer_tasks / max(1, requirements["minimum_transfer_tasks"]))
        integration_progress = 1.0 if integration else 0.0
        progress = round(
            evidence_progress * 0.20
            + coverage_ratio * 0.25
            + practice_progress * 0.25
            + transfer_progress * 0.15
            + integration_progress * 0.15,
            4,
        )
        criteria = {
            "sources": int(evidence.get("independent_hosts", 0) or 0) >= requirements["required_independent_hosts"]
            and int(evidence.get("documents", 0) or 0) >= requirements["minimum_document_count"],
            "levels": len(levels) >= requirements["required_levels"],
            "coverage": coverage_ratio >= requirements["required_covered_ratio"],
            "practice": tasks >= requirements["minimum_practice_tasks"]
            and pass_rate >= requirements["required_pass_rate"]
            and failed == 0,
            "transfer": transfer_tasks >= requirements["minimum_transfer_tasks"],
            "integration": integration or not requirements["requires_integration_task"],
        }
        return {
            "progress": progress,
            "ready": all(criteria.values()),
            "criteria": criteria,
            "requirements": requirements,
            "coverage": {"covered": len(covered), "total": len(concepts), "ratio": round(coverage_ratio, 4)},
            "practice": {"tasks": tasks, "passed": passed, "failed": failed,
                          "pass_rate": round(pass_rate, 4), "transfer": transfer_tasks,
                          "integration": integration},
        }

    @staticmethod
    def _merge_curriculum(current: dict[str, Any] | None,
                          incoming: dict[str, Any] | None) -> dict[str, Any] | None:
        """Mescla trilhas sem perder objetivos já comprovados."""
        if not incoming:
            return current
        if not current or not current.get("levels"):
            return json.loads(json.dumps(incoming, ensure_ascii=False))
        merged = json.loads(json.dumps(current, ensure_ascii=False))
        levels_by_id = {level.get("id"): level for level in merged.get("levels", [])}
        for incoming_level in incoming.get("levels", []):
            level_id = incoming_level.get("id")
            target = levels_by_id.get(level_id)
            if target is None:
                target = {"id": level_id, "title": incoming_level.get("title", level_id), "concepts": []}
                merged.setdefault("levels", []).append(target)
                levels_by_id[level_id] = target
            concepts_by_id = {concept.get("id"): concept for concept in target.get("concepts", [])}
            for incoming_concept in incoming_level.get("concepts", []):
                concept_id = incoming_concept.get("id")
                existing = concepts_by_id.get(concept_id)
                if existing is None:
                    target.setdefault("concepts", []).append(json.loads(json.dumps(incoming_concept, ensure_ascii=False)))
                    concepts_by_id[concept_id] = target["concepts"][-1]
                elif existing.get("status") != "covered" and incoming_concept.get("status") == "covered":
                    existing["status"] = "covered"
        merged["schema"] = incoming.get("schema", merged.get("schema", "curriculum/v2"))
        merged["topic"] = incoming.get("topic", merged.get("topic"))
        completion = dict(merged.get("completion") or {})
        completion.update(incoming.get("completion") or {})
        # A trilha antiga com 90% não pode rebaixar o contrato atual de 100%.
        completion["required_covered_ratio"] = max(1.0, float(completion.get("required_covered_ratio", 1.0) or 1.0))
        merged["completion"] = completion
        return merged

    @staticmethod
    def _normalize_practice(skill: dict[str, Any]) -> dict[str, Any]:
        """Consolida repetições: repetir um laboratório reforça a prova, mas
        não fabrica novas tarefas para inflar a proficiência."""
        practice = skill.setdefault("practice", {"tasks": 0, "passed": 0, "failed": 0, "verified": []})
        unique = {}
        for item in practice.get("verified") or []:
            key = (str(item.get("task") or ""), str(item.get("level") or "practice"))
            unique.setdefault(key, item)
        practice["verified"] = list(unique.values())
        failed_tasks = set(str(item) for item in practice.get("failed_tasks") or [])
        practice["failed_tasks"] = sorted(failed_tasks)
        practice["passed"] = len(practice["verified"])
        practice["failed"] = max(len(failed_tasks), int(practice.get("failed", 0) or 0))
        practice["tasks"] = practice["passed"] + practice["failed"]
        return practice

    @staticmethod
    def _refresh_metrics(skill: dict[str, Any]) -> dict[str, Any]:
        evidence = skill.setdefault("evidence", {})
        # Uma URL encontrada não é, por si só, um documento aberto e validado.
        evidence["documents"] = max(0, int(evidence.get("documents", 0) or 0))
        hosts = int(evidence.get("independent_hosts", 0) or 0)
        documents = int(evidence.get("documents", 0) or 0)
        evidence_score = min(0.55, 0.15 + min(0.25, hosts * 0.08) + min(0.15, documents * 0.03))
        practice = AgentState._normalize_practice(skill)
        tasks = int(practice.get("tasks", 0) or 0)
        passed = int(practice.get("passed", 0) or 0)
        practice_score = min(0.32, (passed / tasks) * 0.32) if tasks else 0.0
        skill["evaluation"] = AgentState._evaluation(skill)
        if skill["evaluation"]["ready"]:
            skill["status"] = "mastered"
        elif skill.get("status") == "mastered":
            skill["status"] = "partially_known"
        if skill.get("status") != "mastered":
            skill["confidence"] = round(evidence_score + practice_score, 4)
        return skill

    def get(self, topic: str) -> dict[str, Any]:
        topic = canonical_topic(topic)
        with LOCK:
            data = self._load()
            skill = data.setdefault("skills", {}).get(topic)
            if skill is not None:
                before = json.dumps(skill, sort_keys=True, ensure_ascii=False)
                self._refresh_metrics(skill)
                if json.dumps(skill, sort_keys=True, ensure_ascii=False) != before:
                    self._save(data)
            return json.loads(json.dumps(self._refresh_metrics(skill or _empty(topic)), ensure_ascii=False))

    def learning_started(self, topic: str, *, version: str | None = None) -> dict[str, Any]:
        topic = canonical_topic(topic)
        with LOCK:
            data = self._load()
            skill = data.setdefault("skills", {}).setdefault(topic, _empty(topic))
            skill["status"] = "studying"
            skill["version"] = version
            skill["updated_at"] = time.time()
            self._save(data)
            return json.loads(json.dumps(skill, ensure_ascii=False))

    def record_research(self, topic: str, *, sources: list[dict], independent_hosts: int,
                        documents: int, status: str, gaps: list[str] | None = None,
                        curriculum: dict[str, Any] | None = None,
                        source_repository: str | None = None,
                        technologies: list[str] | None = None,
                        technology_evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        topic = canonical_topic(topic)
        with LOCK:
            data = self._load()
            skill = data.setdefault("skills", {}).setdefault(topic, _empty(topic))
            previous_evidence = skill.get("evidence") or {}
            existing_sources = list(previous_evidence.get("sources") or [])
            source_map = {str(item.get("url")): item for item in existing_sources if item.get("url")}
            for item in sources:
                if item.get("url"):
                    source_map[str(item["url"])] = item
            merged_sources = list(source_map.values())
            existing_hosts = int(previous_evidence.get("independent_hosts", 0) or 0)
            merged_documents = max(int(previous_evidence.get("documents", 0) or 0), documents)
            skill["status"] = "partially_known" if status in {"provisional", "corroborated"} else "evidence_gap"
            skill["confidence"] = min(0.55, 0.15 + min(0.25, max(existing_hosts, independent_hosts) * 0.08) + min(0.15, merged_documents * 0.03))
            skill["evidence"] = {"sources": merged_sources[:48],
                                  "independent_hosts": max(existing_hosts, independent_hosts),
                                  "documents": merged_documents}
            if curriculum:
                skill["curriculum"] = self._merge_curriculum(skill.get("curriculum"), curriculum)
                skill["concepts"]["covered"] = [item.get("id") for level in skill["curriculum"].get("levels", [])
                                                   for item in level.get("concepts", []) if item.get("status") == "covered"]
                skill["concepts"]["gaps"] = [item.get("id") for level in skill["curriculum"].get("levels", [])
                                               for item in level.get("concepts", []) if item.get("status") != "covered"]
            elif gaps is not None:
                skill["concepts"]["gaps"] = list(dict.fromkeys(gaps))
            repositories = list(skill.get("source_repositories") or [])
            if skill.get("source_repository"):
                repositories.append(skill["source_repository"])
            if source_repository:
                repositories.append(source_repository)
            repositories = list(dict.fromkeys(item for item in repositories if item))
            if repositories:
                skill["source_repository"] = repositories[0]
                skill["source_repositories"] = repositories
            if technologies:
                skill["technologies"] = list(dict.fromkeys((skill.get("technologies") or []) + technologies))
            if technology_evidence:
                merged_technology_evidence = dict(skill.get("technology_evidence") or {})
                for name, item in technology_evidence.items():
                    old = merged_technology_evidence.get(name) or {}
                    merged_technology_evidence[name] = {
                        **old, **item,
                        "score": min(100, int(old.get("score", 0) or 0) + int(item.get("score", 0) or 0)),
                        "explicit_documents": int(old.get("explicit_documents", 0) or 0) + int(item.get("explicit_documents", 0) or 0),
                        "strong_documents": int(old.get("strong_documents", 0) or 0) + int(item.get("strong_documents", 0) or 0),
                        "sources": list(dict.fromkeys((old.get("sources") or []) + (item.get("sources") or [])))[:48],
                    }
                skill["technology_evidence"] = merged_technology_evidence
            skill["updated_at"] = time.time()
            self._refresh_metrics(skill)
            self._save(data)
            return json.loads(json.dumps(skill, ensure_ascii=False))

    def record_practice(self, topic: str, *, task: str, passed: bool, evidence: str = "",
                        level: str | None = None) -> dict[str, Any]:
        topic = canonical_topic(topic)
        with LOCK:
            data = self._load()
            skill = data.setdefault("skills", {}).setdefault(topic, _empty(topic))
            practice = self._normalize_practice(skill)
            level = level or "practice"
            key = (task[:300], level)
            verified = {(str(item.get("task") or ""), str(item.get("level") or "practice")): item
                        for item in practice.get("verified") or []}
            failed_tasks = set(str(item) for item in practice.get("failed_tasks") or [])
            if passed:
                verified[key] = {"task": task[:300], "level": level,
                                 "evidence": evidence[:500], "at": time.time()}
                failed_tasks.discard(f"{key[0]}|{key[1]}")
                practice["verified"] = list(verified.values())
            else:
                failed_tasks.add(f"{key[0]}|{key[1]}")
            practice["failed_tasks"] = sorted(failed_tasks)
            self._normalize_practice(skill)
            if passed:
                concept_map = {
                    "foundation-control-flow": ["fundamentos", "dados-e-controle"],
                    "error-handling": "erros-e-testes",
                    "module-composition": ["composicao", "modules"],
                    "unseen-inputs": ["problema-novo", "testing"],
                    "transfer-boundary-cases": "problema-novo",
                    "iterator-generator": "iterators",
                    "iterators": "iterators",
                    "exceptions-contract": "exceptions",
                    "serialization-boundary": "serialization",
                    "async-await": "async",
                    "closure-scope": "closures",
                    "promise-contract": "promises",
                    "event-loop-order": "event-loop",
                    "async-error-boundary": "async",
                    "module-boundary": "modules",
                    "node-http-server": "http-server",
                    "node-streams": "streams",
                    "node-observability": "observability",
                    "ownership": "ownership",
                    "borrowing": "borrowing",
                    "result-option": "result-option",
                    "cargo": "cargo",
                    "traits": "traits",
                    "pattern-matching": "pattern-matching",
                    "modules": "modules",
                }
                covered = concept_map.get(task)
                if covered:
                    covered = covered if isinstance(covered, list) else [covered]
                    for level in skill.get("curriculum", {}).get("levels", []):
                        for concept in level.get("concepts", []):
                            if concept.get("id") in covered:
                                concept["status"] = "covered"
                                skill.setdefault("concepts", {}).setdefault("covered", []).append(concept.get("id"))
                                skill["concepts"]["covered"] = list(dict.fromkeys(skill["concepts"]["covered"]))
                                skill["concepts"]["gaps"] = [gap for gap in skill["concepts"].get("gaps", []) if gap != concept.get("id")]
            evaluation = self._evaluation(skill)
            if evaluation["ready"]:
                skill["status"] = "mastered"
                skill["confidence"] = min(0.95, 0.65 + practice["passed"] * 0.05)
            else:
                skill["status"] = "partially_known"
                self._refresh_metrics(skill)
            skill["updated_at"] = time.time()
            self._save(data)
            return json.loads(json.dumps(skill, ensure_ascii=False))

    def all(self) -> dict[str, dict[str, Any]]:
        with LOCK:
            data = self._load()
            skills = data.get("skills", {})
            changed = False
            merged = {}
            for stored_topic, skill in skills.items():
                topic = canonical_topic(stored_topic)
                before = json.dumps(skill, sort_keys=True, ensure_ascii=False)
                self._refresh_metrics(skill)
                changed = changed or json.dumps(skill, sort_keys=True, ensure_ascii=False) != before
                if topic not in merged:
                    merged[topic] = json.loads(json.dumps(skill, ensure_ascii=False))
                    merged[topic]["topic"] = topic
                elif skill.get("updated_at", 0) > merged[topic].get("updated_at", 0):
                    # Mantém a versão mais recente como estado principal; a
                    # limpeza física fica para a próxima gravação segura.
                    merged[topic] = json.loads(json.dumps(skill, ensure_ascii=False))
                    merged[topic]["topic"] = topic
            if changed:
                self._save(data)
            return merged

    def delete(self, topic: str) -> int:
        """Remove todas as variantes canônicas de uma competência."""
        canonical = canonical_topic(topic)
        with LOCK:
            data = self._load()
            skills = data.setdefault("skills", {})
            removed = [key for key in skills if canonical_topic(key) == canonical]
            for key in removed:
                del skills[key]
            if removed:
                self._save(data)
            return len(removed)

    def clear(self) -> int:
        with LOCK:
            data = self._load()
            count = len(data.setdefault("skills", {}))
            if count:
                data["skills"] = {}
                self._save(data)
            return count
