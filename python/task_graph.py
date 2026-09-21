"""Grafo leve de tarefas para pedidos compostos.

É uma camada simbólica: não tenta fingir raciocínio neural. Explicita
dependências, permite medir o caminho percorrido e dá ao agente um plano
reproduzível antes da execução.
"""

from __future__ import annotations

import re

from dialogue import normalize


class TaskGraphPlanner:
    def build(self, question: str, first_tool: str | None = None) -> dict:
        text = normalize(question)
        nodes = []

        def add(tool: str, reason: str, group: int = 0):
            if any(node["tool"] == tool for node in nodes):
                return
            nodes.append({"id": f"step-{len(nodes) + 1}", "tool": tool, "reason": reason, "group": group})

        if first_tool:
            add(first_tool, "etapa inicial derivada do pedido")
        asks_tests = bool(re.search(r"\b(?:teste|testes|verifique|valid[ae]|rode|execute)\b", text))
        asks_read = bool(re.search(r"\b(?:leia|ler|abra|abrir|analise)\b", text))
        asks_sources = bool(re.search(r"\b(?:fonte|fontes|cite|cita[cç][aã]o|refer[êe]ncia)\b", text))

        if first_tool in {"create_file", "create_web_page", "edit_file", "apply_repair", "create_directory"} and asks_tests:
            add("project_checks", "validar a alteração antes de concluir")
        elif first_tool == "inspect_project" and asks_tests:
            add("project_checks", "verificar o projeto depois da inspeção")
        elif first_tool == "search_web" and asks_read:
            add("open_page", "abrir uma fonte retornada pela pesquisa")
        elif first_tool == "research_web" and asks_sources:
            add("cite_sources", "organizar as fontes usadas na pesquisa")
        elif first_tool == "list_files" and asks_read:
            add("read_file", "ler o arquivo identificado na listagem")

        edges = [{"from": nodes[index]["id"], "to": nodes[index + 1]["id"], "type": "depends_on"} for index in range(len(nodes) - 1)]
        return {"version": "task-graph/v1", "nodes": nodes, "edges": edges, "parallel_groups": [[node["id"]] for node in nodes]}

    def next_tool(self, question: str, completed: list[str], first_tool: str | None = None) -> str | None:
        graph = self.build(question, first_tool or (completed[0] if completed else None))
        for node in graph["nodes"]:
            if node["tool"] not in completed:
                return node["tool"]
        return None
