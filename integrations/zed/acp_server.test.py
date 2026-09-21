#!/usr/bin/env python3
"""Teste de contrato do agente ACP sem depender do processo do Zed."""

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv/bin/python"
SERVER = ROOT / "integrations/zed/acp_server.py"


def send(process, payload):
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()
    return json.loads(process.stdout.readline())


def main():
    env = os.environ.copy()
    env["IA_LOCAL_RUNTIME"] = "http://127.0.0.1:3000"
    process = subprocess.Popen(
        [str(PYTHON), str(SERVER), "--acp"],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        initialized = send(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1, "clientCapabilities": {}, "clientInfo": {"name": "contract-test", "version": "1"}}})
        assert initialized["result"]["protocolVersion"] == 1
        assert initialized["result"]["agentInfo"]["name"] == "ia-local-do-zero"
        assert initialized["result"]["agentCapabilities"]["sessionCapabilities"]["close"] == {}

        created = send(process, {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": str(ROOT), "mcpServers": []}})
        session_id = created["result"]["sessionId"]
        assert session_id.startswith("ia-local-")

        # Fechamento como notificação não pode produzir uma resposta JSON-RPC.
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "session/close", "params": {"sessionId": session_id}}) + "\n")
        process.stdin.flush()
        ready, _, _ = select.select([process.stdout], [], [], 0.15)
        assert not ready

        recreated = send(process, {"jsonrpc": "2.0", "id": 20, "method": "session/new", "params": {"cwd": str(ROOT), "mcpServers": []}})
        session_id = recreated["result"]["sessionId"]

        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "session/prompt", "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "Olá"}]}}) + "\n")
        process.stdin.flush()
        messages = []
        while True:
            message = json.loads(process.stdout.readline())
            messages.append(message)
            if message.get("id") == 3:
                break
        updates = [message for message in messages if message.get("method") == "session/update"]
        assert any(update["params"]["update"]["sessionUpdate"] == "agent_message_chunk" for update in updates)
        assert messages[-1]["result"]["stopReason"] == "end_turn"

        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 4, "method": "session/prompt", "params": {"sessionId": session_id, "prompt": [{"type": "resource_link", "uri": f"file://{ROOT}", "name": "IA Local do Zero", "mimeType": "inode/directory"}, {"type": "text", "text": "Liste os arquivos do workspace"}]}}) + "\n")
        process.stdin.flush()
        tool_messages = []
        while True:
            message = json.loads(process.stdout.readline())
            tool_messages.append(message)
            if message.get("id") == 4:
                break
        tool_updates = [message["params"]["update"] for message in tool_messages if message.get("method") == "session/update"]
        assert any(update.get("sessionUpdate") == "tool_call" for update in tool_updates)
        assert any(update.get("sessionUpdate") == "tool_call_update" and update.get("status") == "completed" for update in tool_updates)
        assert tool_messages[-1]["result"]["stopReason"] == "end_turn"

        test_file = ROOT / f".ia-acp-permission-{os.getpid()}.txt"
        if test_file.exists():
            test_file.unlink()
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 5, "method": "session/prompt", "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": f"Crie o arquivo {test_file.name}:\nconteúdo ACP de teste"}]}}) + "\n")
        process.stdin.flush()
        edit_messages = []
        try:
            while True:
                message = json.loads(process.stdout.readline())
                edit_messages.append(message)
                if message.get("method") == "session/request_permission":
                    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": {"outcome": "selected", "optionId": "allow-once"}}}) + "\n")
                    process.stdin.flush()
                if message.get("id") == 5:
                    break
            edit_updates = [message["params"]["update"] for message in edit_messages if message.get("method") == "session/update"]
            assert any(update.get("sessionUpdate") == "tool_call_update" and update.get("status") == "completed" and any(item.get("type") == "diff" for item in update.get("content", [])) for update in edit_updates)
            assert any(message.get("method") == "session/request_permission" for message in edit_messages)
            assert test_file.read_text(encoding="utf-8") == "conteúdo ACP de teste"
            assert edit_messages[-1]["result"]["stopReason"] == "end_turn"
        finally:
            if test_file.exists():
                test_file.unlink()
        print("PASSOU: initialize, sessões, ferramentas, diff, permissão ACP, edição confirmada e resposta final.")
    finally:
        process.kill()
        process.wait(timeout=5)


if __name__ == "__main__":
    main()
