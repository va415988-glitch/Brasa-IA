#!/usr/bin/env python3
"""Ponte MCP por stdio para as ferramentas do runtime local."""
import json
import os
import sys
import urllib.request
import urllib.error

RUNTIME = os.environ.get("IA_LOCAL_RUNTIME", "http://127.0.0.1:3000")

TOOLS = [
    ("list_files", "Lista arquivos dentro do workspace autorizado.", {"type":"object","properties":{"path":{"type":"string"}}}),
    ("read_file", "Lê um arquivo pequeno do workspace.", {"type":"object","required":["path"],"properties":{"path":{"type":"string"}}}),
    ("search_files", "Busca texto nos arquivos do workspace.", {"type":"object","required":["query"],"properties":{"query":{"type":"string"}}}),
    ("project_checks", "Lista ou executa testes reconhecidos do projeto.", {"type":"object","required":["check"],"properties":{"check":{"type":"string","enum":["list","auto","cargo-test","npm-test","pytest","unittest"]}}}),
    ("create_directory", "Cria uma pasta dentro do workspace.", {"type":"object","required":["path"],"properties":{"path":{"type":"string"}}}),
    ("create_file", "Cria um arquivo novo dentro do workspace.", {"type":"object","required":["path","content"],"properties":{"path":{"type":"string"},"content":{"type":"string"}}}),
    ("edit_file", "Substitui exatamente um trecho e cria backup.", {"type":"object","required":["path","old_text","new_text"],"properties":{"path":{"type":"string"},"old_text":{"type":"string"},"new_text":{"type":"string"}}}),
    ("search_web", "Pesquisa informação atual na internet.", {"type":"object","required":["query"],"properties":{"query":{"type":"string"}}}),
    ("open_page", "Abre e extrai texto de uma página HTTP(S).", {"type":"object","required":["url"],"properties":{"url":{"type":"string"}}}),
]

def reply(request):
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return {"jsonrpc":"2.0","id":request_id,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"ia-local-do-zero","version":"0.1.0"}}}
    if method == "ping":
        return {"jsonrpc":"2.0","id":request_id,"result":{}}
    if method == "tools/list":
        return {"jsonrpc":"2.0","id":request_id,"result":{"tools":[{"name":name,"description":description,"inputSchema":schema} for name,description,schema in TOOLS]}}
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in {item[0] for item in TOOLS}:
            return {"jsonrpc":"2.0","id":request_id,"error":{"code":-32602,"message":"ferramenta desconhecida"}}
        try:
            # Mantém o workspace do processo MCP sincronizado com o runtime.
            if os.getcwd():
                call_http("set_workspace", {"path":os.getcwd()})
            data = call_http(name, arguments)
            text = json.dumps(data, ensure_ascii=False, indent=2)
            return {"jsonrpc":"2.0","id":request_id,"result":{"content":[{"type":"text","text":text}],"structuredContent":data,"isError":False}}
        except Exception as error:
            return {"jsonrpc":"2.0","id":request_id,"result":{"content":[{"type":"text","text":str(error)}],"isError":True}}
    if request_id is None:
        return None
    return {"jsonrpc":"2.0","id":request_id,"error":{"code":-32601,"message":"método não suportado"}}

def call_http(tool, arguments):
    payload = json.dumps({"tool":tool,"arguments":arguments,"request_id":f"mcp-{tool}"}).encode()
    request = urllib.request.Request(f"{RUNTIME}/api/tool-call", data=payload, headers={"content-type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode())
    except urllib.error.URLError as error:
        raise RuntimeError(f"runtime local indisponível: {error.reason}") from error
    if not body.get("ok"):
        raise RuntimeError(body.get("error") or "a ferramenta falhou")
    return body.get("data")

for line in sys.stdin:
    try:
        request = json.loads(line)
        result = reply(request)
        if result is not None:
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    except Exception as error:
        sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":str(error)}}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
