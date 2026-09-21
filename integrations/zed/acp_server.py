#!/usr/bin/env python3
"""Agente ACP do IA Local do Zero para o Zed.

O processo fala exclusivamente JSON-RPC por stdin/stdout. O trabalho pesado
continua no runtime Rust local; este adaptador só traduz o ciclo de sessão do
ACP para a API local e envia atualizações incrementais ao Zed.
"""

from __future__ import annotations

import json
import os
import re
import queue
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


RUNTIME = os.environ.get("IA_LOCAL_RUNTIME", "http://127.0.0.1:3000").rstrip("/")
REQUEST_TIMEOUT = 10


@dataclass
class Session:
    session_id: str
    cwd: str
    history: list[dict[str, str]] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    active_request: Any = None


class AcpServer:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.pending_permissions: dict[str, queue.Queue[dict[str, Any]]] = {}
        self.write_lock = threading.Lock()
        self.state_lock = threading.Lock()

    def send(self, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self.write_lock:
            sys.stdout.write(encoded + "\n")
            sys.stdout.flush()

    def response(self, request_id: Any, result: Any) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def error(self, request_id: Any, code: int, message: str) -> None:
        self.send({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        })

    def update(self, session_id: str, payload: dict[str, Any]) -> None:
        self.send({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": session_id, "update": payload},
        })

    def handle(self, request: dict[str, Any]) -> None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}

        # Respostas a pedidos agent -> client, como session/request_permission,
        # são entregues ao worker que está aguardando a decisão no Zed.
        if method is None and request_id is not None:
            with self.state_lock:
                waiter = self.pending_permissions.get(str(request_id))
            if waiter:
                waiter.put(request)
            return

        if method == "initialize":
            self.response(request_id, {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "promptCapabilities": {"embeddedContext": True},
                    "sessionCapabilities": {"close": {}},
                },
                "agentInfo": {
                    "name": "ia-local-do-zero",
                    "title": "IA Local do Zero",
                    "version": "0.2.0",
                },
                "authMethods": [],
            })
            return

        if method == "session/new":
            # Alguns clientes iniciam a sessão antes de terminar de anexar o
            # workspace. O diretório do processo é um fallback seguro e mantém
            # o handshake vivo; as ferramentas ainda passam pelo allowlist do
            # runtime antes de operar em qualquer arquivo.
            cwd = self.absolute_directory(params.get("cwd")) or str(Path.cwd().resolve())
            session_id = f"ia-local-{uuid.uuid4().hex}"
            with self.state_lock:
                self.sessions[session_id] = Session(session_id=session_id, cwd=cwd)
            self.response(request_id, {"sessionId": session_id})
            return

        if method == "session/close":
            session = self.sessions.get(params.get("sessionId"))
            if session:
                session.cancel_event.set()
                with self.state_lock:
                    self.sessions.pop(session.session_id, None)
            # Notificações JSON-RPC não têm resposta. O Zed pode usar esse
            # formato ao descartar uma thread; responder com id=null pode
            # fazer o cliente considerar o transporte inválido.
            if request_id is not None:
                self.response(request_id, {})
            return

        if method == "session/cancel":
            session = self.sessions.get(params.get("sessionId"))
            if session:
                session.cancel_event.set()
            return

        if method == "session/prompt":
            session_id = params.get("sessionId")
            session = self.sessions.get(session_id)
            if not session:
                self.error(request_id, -32602, "sessão inexistente")
                return
            if session.active_request is not None and session.active_request.is_alive():
                self.error(request_id, -32000, "a sessão já está processando uma mensagem")
                return
            session.cancel_event = threading.Event()
            worker = threading.Thread(
                target=self.process_prompt,
                args=(session, request_id, params.get("prompt") or []),
                daemon=True,
            )
            session.active_request = worker
            worker.start()
            return

        if request_id is not None:
            self.error(request_id, -32601, f"método ACP não suportado: {method}")

    @staticmethod
    def absolute_directory(value: Any) -> str | None:
        if not isinstance(value, str) or not value.startswith("/"):
            return None
        path = Path(value).expanduser()
        if not path.is_dir():
            return None
        try:
            return str(path.resolve())
        except OSError:
            return None

    @staticmethod
    def text_from_block(block: dict[str, Any], cwd: str) -> str:
        block_type = block.get("type")
        if block_type == "text":
            return str(block.get("text") or "")
        if block_type == "resource":
            resource = block.get("resource") or {}
            text = resource.get("text")
            if isinstance(text, str):
                uri = resource.get("uri") or "contexto embutido"
                return f"\n[Contexto: {uri}]\n{text}\n"
        if block_type == "resource_link":
            uri = str(block.get("uri") or "")
            parsed = urllib.parse.urlparse(uri)
            if parsed.scheme == "file":
                path = Path(urllib.request.url2pathname(parsed.path))
                try:
                    relative = path.resolve().relative_to(Path(cwd).resolve())
                    return f"\n[Arquivo anexado: {relative}]\n"
                except ValueError:
                    return f"\n[Arquivo fora do workspace: {path.name}]\n"
            return f"\n[Recurso anexado: {block.get('name') or uri}]\n"
        return ""

    @classmethod
    def prompt_text(cls, prompt: list[Any], cwd: str) -> str:
        parts = [cls.text_from_block(block, cwd) for block in prompt if isinstance(block, dict)]
        return "\n".join(part for part in parts if part.strip()).strip()

    @staticmethod
    def direct_text_blocks(prompt: list[Any]) -> list[str]:
        """Retorna os textos digitados, isolados dos arquivos/contextos do Zed."""
        return [
            str(block.get("text") or "").strip()
            for block in prompt
            if isinstance(block, dict) and block.get("type") == "text" and str(block.get("text") or "").strip()
        ]

    @staticmethod
    def detect_tool(message: str) -> tuple[str, dict[str, Any]] | None:
        """Reconhece pedidos de workspace suficientemente explícitos para agir."""
        text = message.strip()
        lower = text.lower()
        if re.match(r"^/?(?:pesquisa|pesquise|pesquisar)\s+(?:guiada|profunda|na internet)\b", lower):
            query = re.sub(r"^/?(?:pesquisa|pesquise|pesquisar)\s+(?:guiada|profunda|na internet)\s*", "", text, flags=re.I).strip()
            save = bool(re.search(r"\s--salvar\b", query, flags=re.I))
            query = re.sub(r"\s--salvar\b", "", query, flags=re.I).strip()
            return "research_web", {"query": query, "max_results": 2, "save_to_corpus": save}
        if re.match(r"^/?(?:listar|liste|mostre|mostrar)\s+(?:os\s+)?arquivos?(?:\s+(?:em|no|na|do|da)\s+(.+))?$", lower):
            match = re.match(r"^/?(?:listar|liste|mostre|mostrar)\s+(?:os\s+)?arquivos?(?:\s+(?:em|no|na|do|da)\s+(.+))?$", text, flags=re.I)
            path = match.group(1).strip() if match and match.group(1) else ""
            if path.lower() in {"workspace", "projeto", "projeto atual"}:
                path = ""
            return "list_files", {"path": path}
        match = re.match(r"^/?(?:ler|leia|abrir|abra|mostrar|mostre)\s+(?:o\s+)?(?:arquivo\s+)?(.+)$", text, flags=re.I | re.S)
        if match and ("." in match.group(1) or "/" in match.group(1)):
            return "read_file", {"path": match.group(1).strip().strip('`')}
        match = re.match(r"^/?(?:buscar|busque|procurar|procure|pesquisar|pesquise)\s+(?:no\s+c[oó]digo|nos\s+arquivos|em\s+arquivos)?\s*[:\-]?\s*(.+)$", text, flags=re.I | re.S)
        if match and not lower.startswith(("pesquisar na internet", "pesquise na internet")):
            return "search_files", {"query": match.group(1).strip().strip('`"')}
        if re.match(r"^/?(?:rodar|rode|executar|execute|fa[cç]a)\s+(?:os\s+)?(?:testes|verifica[cç][oõ]es)\b", lower):
            return "project_checks", {"check": "auto"}
        if re.match(r"^/?(?:analise|analisa|revise|revisar|inspecione|inspecionar)\s+(?:o\s+)?(?:meu\s+|este\s+|esse\s+)?(?:projeto|workspace)\b", lower):
            return "inspect_project", {"max_depth": 4}
        match = re.match(r"^/?(?:propor|proponha|sugerir|sugira)\s+(?:uma\s+)?corre[cç][aã]o\s+(?:no\s+|para\s+o\s+|para\s+)?arquivo\s+([^\s:]+)\s*\n\s*(?:motivo|reason)\s*:\s*\n([\s\S]*?)\n\s*(?:antigo|old)\s*:\s*\n([\s\S]*?)\n\s*(?:novo|new)\s*:\s*\n([\s\S]+)$", text, flags=re.I)
        if match:
            return "propose_repair", {"path": match.group(1).strip('`'), "reason": match.group(2).strip(), "old_text": match.group(3), "new_text": match.group(4)}
        match = re.match(r"^/?(?:aplicar|aplique)\s+(?:a\s+)?corre[cç][aã]o\s+(?:no\s+|para\s+o\s+|para\s+)?arquivo\s+([^\s:]+)\s*\n\s*(?:motivo|reason)\s*:\s*\n([\s\S]*?)\n\s*(?:antigo|old)\s*:\s*\n([\s\S]*?)\n\s*(?:novo|new)\s*:\s*\n([\s\S]+)$", text, flags=re.I)
        if match:
            return "apply_repair", {"path": match.group(1).strip('`'), "reason": match.group(2).strip(), "old_text": match.group(3), "new_text": match.group(4)}
        match = re.match(r"^/?(?:criar|crie)\s+(?:uma\s+)?pasta\s+(.+)$", text, flags=re.I)
        if match:
            return "create_directory", {"path": match.group(1).strip().strip('`')}
        match = re.match(r"^/?(?:criar|crie)\s+(?:o\s+)?arquivo\s+([^\s:]+)\s*(?::|\n)\s*(?:conte[uú]do\s*:\s*)?([\s\S]+)$", text, flags=re.I)
        if match:
            return "create_file", {"path": match.group(1).strip('`'), "content": match.group(2)}
        match = re.match(r"^/?(?:editar|edite|alterar|altere)\s+(?:o\s+)?arquivo\s+([^\s:]+)\s*\n\s*(?:antigo|old)\s*:\s*\n([\s\S]*?)\n\s*(?:novo|new)\s*:\s*\n([\s\S]+)$", text, flags=re.I)
        if match:
            return "edit_file", {"path": match.group(1).strip('`'), "old_text": match.group(2), "new_text": match.group(3)}
        return None

    @staticmethod
    def tool_kind(tool: str) -> str:
        return {
            "list_files": "read",
            "read_file": "read",
            "search_files": "search",
            "inspect_project": "read",
            "research_web": "search",
            "project_checks": "execute",
            "create_directory": "edit",
            "create_file": "edit",
            "create_web_page": "edit",
            "edit_file": "edit",
            "apply_repair": "edit",
        }.get(tool, "other")

    def request_permission(self, session: Session, tool_call_id: str, tool: str, arguments: dict[str, Any]) -> bool:
        request_id = f"permission-{uuid.uuid4().hex}"
        waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self.state_lock:
            self.pending_permissions[request_id] = waiter
        self.send({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "session/request_permission",
            "params": {
                "sessionId": session.session_id,
                "toolCall": {
                    "toolCallId": tool_call_id,
                    "title": f"Alterar arquivos com {tool}",
                    "kind": "edit",
                    "status": "pending",
                    "rawInput": arguments,
                },
                "options": [
                    {"optionId": "allow-once", "name": "Permitir uma vez", "kind": "allow_once"},
                    {"optionId": "allow-always", "name": "Permitir sempre", "kind": "allow_always"},
                    {"optionId": "reject-once", "name": "Recusar", "kind": "reject_once"},
                    {"optionId": "reject-always", "name": "Recusar sempre", "kind": "reject_always"},
                ],
            },
        })
        try:
            while not session.cancel_event.is_set():
                try:
                    response = waiter.get(timeout=0.25)
                    outcome = ((response.get("result") or {}).get("outcome") or {})
                    return outcome.get("outcome") == "selected" and outcome.get("optionId") in {"allow-once", "allow-always"}
                except queue.Empty:
                    continue
            return False
        finally:
            with self.state_lock:
                self.pending_permissions.pop(request_id, None)

    @staticmethod
    def diff_content(session: Session, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        if tool not in {"create_file", "edit_file", "apply_repair"}:
            return None
        relative = str(arguments.get("path") or "")
        path = str((Path(session.cwd) / relative).resolve())
        if tool == "create_file":
            return {"type": "diff", "path": path, "oldText": None, "newText": str(arguments.get("content") or "")}
        return {
            "type": "diff",
            "path": path,
            "oldText": str(arguments.get("old_text") or ""),
            "newText": str(arguments.get("new_text") or ""),
        }

    @staticmethod
    def format_tool_result(tool: str, data: dict[str, Any]) -> str:
        if tool == "read_file":
            content = str(data.get("content") or "")
            if len(content) > 30000:
                content = content[:30000] + "\n\n[conteúdo truncado para preservar a sessão]"
            path = data.get("path", "arquivo")
            extension = str(path).rsplit(".", 1)[-1] if "." in str(path) else "text"
            return f"Arquivo `{path}` ({data.get('bytes', '?')} bytes):\n\n```{extension}\n{content}\n```"
        if tool == "list_files":
            entries = data.get("entries") or []
            lines = [f"- {'📁' if entry.get('kind') == 'directory' else '📄'} {entry.get('name')}" for entry in entries]
            return f"Workspace: `{data.get('workspace')}`\n\n" + ("\n".join(lines) or "O diretório está vazio.")
        if tool == "search_files":
            matches = data.get("matches") or []
            lines = [f"- `{item.get('path')}:{item.get('line')}` — {item.get('text')}" for item in matches]
            return f"Busca por `{data.get('query')}`:\n\n" + ("\n".join(lines) or "Nenhuma ocorrência encontrada.")
        if tool == "research_web":
            answer = str(data.get("answer") or "As fontes foram consultadas, mas não houve síntese disponível.")
            pages = data.get("pages") or []
            citations = [f"[{page.get('source_id')}] {page.get('title')} — {page.get('url')}" for page in pages if page.get("text")]
            return answer + (("\n\nFontes:\n- " + "\n- ".join(citations)) if citations else "")
        if tool == "project_checks":
            status = "passou" if data.get("passed") else "falhou"
            if data.get("executed") is False:
                return str(data.get("message") or "Nenhuma verificação disponível.")
            output = "\n".join(part for part in (data.get("stdout"), data.get("stderr")) if part)
            return f"Verificação `{data.get('command', data.get('check'))}` {status} em {data.get('elapsed_ms', '?')} ms.\n\n{output}".strip()
        if tool == "inspect_project":
            lines = [str(data.get("summary") or "Estrutura inspecionada."), "", f"Manifestos: {', '.join(data.get('manifests') or []) or 'nenhum'}", f"Entradas: {', '.join(data.get('entrypoints') or []) or 'nenhuma'}", f"Testes: {', '.join(data.get('test_files') or []) or 'nenhum'}", f"Verificações: {', '.join(data.get('checks') or []) or 'nenhuma'}"]
            if data.get("signals"):
                lines.extend(["", "Pontos para revisar:", *[f"- {item}" for item in data["signals"]]])
            return "\n".join(lines)
        if tool == "diagnose_project":
            lines = [str(data.get("summary") or "Não foi possível classificar a falha.")]
            if data.get("evidence"):
                lines.extend(["", "Evidências:", *[f"- {item}" for item in data["evidence"]]])
            if data.get("next_steps"):
                lines.extend(["", "Próximos passos:", *[f"- {item}" for item in data["next_steps"]]])
            return "\n".join(lines)
        if tool == "propose_repair":
            return "\n".join([
                "Proposta de correção validada; nenhum arquivo foi alterado.",
                f"Arquivo: `{data.get('path', '?')}`",
                f"Motivo: {data.get('reason', 'não informado')}",
                "\nDiff proposto:",
                f"- trecho atual:\n{data.get('old_text', '')}",
                f"+ trecho novo:\n{data.get('new_text', '')}",
                "\nPara aplicar, use `apply_repair` com os mesmos trechos após revisar o diff.",
            ])
        if tool == "apply_repair":
            repair = data.get('repair') or {}
            return f"Correção aplicada em `{data.get('path', '?')}`. Backup: `{data.get('backup', 'registrado pelo runtime')}`.\nMotivo: {repair.get('reason', 'não informado')}\n\nA verificação do projeto será executada agora."
        if tool == "create_web_page":
            return f"Página HTML criada em `{data.get('path', '?')}`. A prévia visual foi preparada no chat e o arquivo está editável no workspace."
        if data.get("created"):
            return f"Criado: `{data.get('path')}`."
        if data.get("updated"):
            return f"Alteração aplicada em `{data.get('path')}`. Backup: `{data.get('backup', 'registrado pelo runtime')}`."
        return json.dumps(data, ensure_ascii=False, indent=2)

    def run_workspace_tool(self, session: Session, tool: str, arguments: dict[str, Any], message_id: str) -> tuple[str, dict[str, Any]]:
        tool_call_id = f"call-{uuid.uuid4().hex}"
        self.update(session.session_id, {
            "sessionUpdate": "tool_call",
            "toolCallId": tool_call_id,
            "title": f"Executando {tool}",
            "kind": self.tool_kind(tool),
            "status": "pending",
            "rawInput": arguments,
        })
        diff = self.diff_content(session, tool, arguments)
        if diff:
            self.update(session.session_id, {
                "sessionUpdate": "tool_call_update",
                "toolCallId": tool_call_id,
                "content": [diff],
            })
        if self.tool_kind(tool) == "edit":
            if not self.request_permission(session, tool_call_id, tool, arguments):
                denied = "A alteração foi recusada ou cancelada; nenhum arquivo foi modificado."
                self.update(session.session_id, {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": tool_call_id,
                    "status": "failed",
                    "content": [{"type": "content", "content": {"type": "text", "text": denied}}],
                })
                return denied, {"tool": tool, "ok": False, "error": denied}
        self.update(session.session_id, {
            "sessionUpdate": "tool_call_update",
            "toolCallId": tool_call_id,
            "status": "in_progress",
        })
        try:
            result = self.call_tool(tool, arguments)
            data = result.get("data") or {}
            rendered = self.format_tool_result(tool, data)
            if tool in {"create_file", "create_web_page", "edit_file", "apply_repair"}:
                verification = self.call_tool("project_checks", {"check": "auto", "path": data.get("path", "")})
                verification_data = verification.get("data") or {}
                rendered += "\n\nVerificação após a alteração:\n" + self.format_tool_result("project_checks", verification_data)
                if verification_data.get("executed", True) and verification_data.get("passed") is False:
                    diagnosis = self.call_tool("diagnose_project", verification_data)
                    rendered += "\n\nDiagnóstico:\n" + self.format_tool_result("diagnose_project", diagnosis.get("data") or {})
            completed_content: list[dict[str, Any]] = []
            if diff:
                completed_content.append(diff)
            completed_content.append({"type": "content", "content": {"type": "text", "text": rendered}})
            self.update(session.session_id, {
                "sessionUpdate": "tool_call_update",
                "toolCallId": tool_call_id,
                "status": "completed",
                "content": completed_content,
            })
            return rendered, {"tool": tool, "ok": True, "data": data, "verification_done": tool in {"create_file", "create_web_page", "edit_file", "apply_repair"}}
        except Exception as exc:
            failure = f"A ferramenta `{tool}` falhou: {exc}"
            self.update(session.session_id, {
                "sessionUpdate": "tool_call_update",
                "toolCallId": tool_call_id,
                "status": "failed",
                "content": [{"type": "content", "content": {"type": "text", "text": failure}}],
            })
            return failure, {"tool": tool, "ok": False, "error": str(exc)}

    def continue_agent(self, session: Session, request_id: Any, message_id: str, tool_result: dict[str, Any], depth: int = 0) -> None:
        """Feed a structured tool result back into the same agent session."""
        if depth >= 6:
            return
        with self.state_lock:
            session.history.append({"role": "tool", "content": json.dumps(tool_result, ensure_ascii=False)})
            history = list(session.history)
        data = self.call_chat(history, session.cancel_event)
        requested = data.get("tool_call") or {}
        if requested.get("tool"):
            next_tool = requested["tool"]
            answer, raw_result = self.run_workspace_tool(session, next_tool, requested.get("arguments") or {}, message_id)
            if data.get("trace_id"):
                raw_result["trace_id"] = data["trace_id"]
            with self.state_lock:
                session.history.append({"role": "assistant", "content": answer})
            self.update(session.session_id, {
                "sessionUpdate": "agent_message_chunk",
                "messageId": message_id,
                "content": {"type": "text", "text": answer},
            })
            self.continue_agent(session, request_id, message_id, raw_result, depth + 1)
            return
        answer = str(data.get("text") or data.get("error") or "A etapa foi concluída.")
        with self.state_lock:
            session.history.append({"role": "assistant", "content": answer})
        self.update(session.session_id, {
            "sessionUpdate": "agent_message_chunk",
            "messageId": message_id,
            "content": {"type": "text", "text": answer},
        })

    def process_prompt(self, session: Session, request_id: Any, prompt: list[Any]) -> None:
        message = self.prompt_text(prompt, session.cwd)
        direct_text = self.direct_text_blocks(prompt)
        if not message:
            self.error(request_id, -32602, "a mensagem precisa conter texto ou contexto legível")
            return

        message_id = f"msg-{uuid.uuid4().hex}"
        self.update(session.session_id, {
            "sessionUpdate": "plan",
            "entries": [{"content": "Consultar o runtime local e preparar a resposta", "priority": "high", "status": "in_progress"}],
        })
        self.update(session.session_id, {
            "sessionUpdate": "agent_message_chunk",
            "messageId": message_id,
            "content": {"type": "text", "text": "Estou analisando a mensagem no runtime local…\n\n"},
        })

        try:
            self.call_tool("set_workspace", {"path": session.cwd})
            with self.state_lock:
                session.history.append({"role": "user", "content": message})
                history = list(session.history)
            # O Zed pode anexar o diretório/projeto como resource_link. O texto
            # digitado precisa ser roteado isoladamente, sem o rótulo do anexo.
            detected = next((self.detect_tool(text) for text in direct_text if self.detect_tool(text)), None)
            if detected:
                tool, arguments = detected
                answer, raw_result = self.run_workspace_tool(session, tool, arguments, message_id)
                with self.state_lock:
                    session.history.append({"role": "assistant", "content": answer})
                self.update(session.session_id, {
                    "sessionUpdate": "agent_message_chunk",
                    "messageId": message_id,
                    "content": {"type": "text", "text": answer},
                })
                self.update(session.session_id, {
                    "sessionUpdate": "plan",
                    "entries": [{"content": "Executar a ferramenta solicitada no workspace", "priority": "high", "status": "completed"}],
                })
                self.continue_agent(session, request_id, message_id, raw_result)
                self.response(request_id, {"stopReason": "end_turn"})
                return
            data = self.call_chat(history, session.cancel_event)
            if session.cancel_event.is_set():
                self.response(request_id, {"stopReason": "cancelled"})
                return
            if (data.get("tool_call") or {}).get("tool"):
                requested = data["tool_call"]
                answer, raw_result = self.run_workspace_tool(session, requested["tool"], requested.get("arguments") or {}, message_id)
                if data.get("trace_id"):
                    raw_result["trace_id"] = data["trace_id"]
                with self.state_lock:
                    session.history.append({"role": "assistant", "content": answer})
                self.update(session.session_id, {
                    "sessionUpdate": "agent_message_chunk",
                    "messageId": message_id,
                    "content": {"type": "text", "text": answer},
                })
                self.update(session.session_id, {
                    "sessionUpdate": "plan",
                    "entries": [{"content": "Interpretar a intenção e executar a ferramenta escolhida", "priority": "high", "status": "completed"}],
                })
                self.continue_agent(session, request_id, message_id, raw_result)
                self.response(request_id, {"stopReason": "end_turn"})
                return
            answer = str(data.get("text") or data.get("error") or "Não consegui obter uma resposta do runtime local.")
            with self.state_lock:
                session.history.append({"role": "assistant", "content": answer})
            self.update(session.session_id, {
                "sessionUpdate": "agent_message_chunk",
                "messageId": message_id,
                "content": {"type": "text", "text": answer},
            })
            self.update(session.session_id, {
                "sessionUpdate": "plan",
                "entries": [{"content": "Consultar o runtime local e preparar a resposta", "priority": "high", "status": "completed"}],
            })
            self.response(request_id, {"stopReason": "end_turn"})
        except Exception as exc:  # O Zed deve receber uma resposta legível mesmo quando o runtime cai.
            if session.cancel_event.is_set():
                self.response(request_id, {"stopReason": "cancelled"})
            else:
                detail = str(exc)
                if "Connection refused" in detail or "Errno 111" in detail:
                    detail = "o runtime local está desligado; execute ./start.sh na raiz do projeto e tente novamente"
                self.update(session.session_id, {
                    "sessionUpdate": "agent_message_chunk",
                    "messageId": message_id,
                    "content": {"type": "text", "text": f"Não consegui concluir a requisição local: {detail}"},
                })
                self.response(request_id, {"stopReason": "end_turn"})

    def call_chat(self, messages: list[dict[str, str]], cancel_event: threading.Event) -> dict[str, Any]:
        if cancel_event.is_set():
            raise RuntimeError("requisição cancelada")
        payload = json.dumps({"request_id": f"acp-{uuid.uuid4().hex}", "messages": messages}).encode()
        request = urllib.request.Request(
            f"{RUNTIME}/api/chat",
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            data = json.loads(response.read().decode())
        if not data.get("ok"):
            raise RuntimeError(data.get("error") or "runtime local recusou a mensagem")
        return data

    def call_tool(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps({"tool": tool, "arguments": arguments, "request_id": f"acp-{tool}"}).encode()
        request = urllib.request.Request(
            f"{RUNTIME}/api/tool-call",
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            data = json.loads(response.read().decode())
        if not data.get("ok"):
            raise RuntimeError(data.get("error") or f"ferramenta {tool} recusada")
        return data


def main() -> None:
    server = AcpServer()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if isinstance(request, dict):
                server.handle(request)
        except Exception as exc:
            server.error(None, -32700, str(exc))


if __name__ == "__main__":
    main()
