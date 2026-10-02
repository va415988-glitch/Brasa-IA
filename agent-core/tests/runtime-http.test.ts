import assert from "node:assert/strict";
import test from "node:test";
import {AgentCore, RuntimeHttpPorts} from "../src/index.ts";
import type {ApprovalRequest, Fetcher, HttpResponse} from "../src/index.ts";

function response(payload: unknown): HttpResponse {
  return {ok: true, json: async () => payload};
}

test("adapta o ciclo do agente aos endpoints reais do runtime", async () => {
  const calls: string[] = [];
  const fetcher: Fetcher = async (url, init) => {
    const body = JSON.parse(init?.body ?? "{}") as {tool?: string};
    if (url.endsWith("/api/v1/research")) {
      calls.push("research_web");
      return response({
        ok: true,
        data: {
          pages: [{
            title: "Fonte local",
            url: "https://example.test/typescript",
            text: "Contrato de tipos.",
          }],
        },
      });
    }
    calls.push(body.tool ?? "unknown");
    switch (body.tool) {
      case "set_workspace":
        return response({ok: true, data: {workspace: "/tmp/assistente", selected: true}});
      case "inspect_project":
        return response({ok: true, data: {workspace: "/tmp/assistente", files: [], manifests: [], test_files: [], entrypoints: []}});
      case "create_file":
        return response({ok: true, data: {path: "ASSISTENTE_PLANO.md", created: true}});
      case "project_checks":
        return response({ok: true, data: {passed: true, executed: true, summary: "aprovado", evidence: ["smoke"]}});
      default:
        return response({ok: false, error: "ferramenta inesperada"});
    }
  };
  const approval = {request: async (_input: ApprovalRequest) => true};
  const ports = new RuntimeHttpPorts({
    baseUrl: "http://127.0.0.1:3000",
    workspaceRoot: "/tmp/assistente",
    fetcher,
  }, approval);
  const result = await new AgentCore(ports).pursue({
    prompt: "Criar um assistente pessoal local.",
    objective: "build",
    workspaceRoot: "/tmp/assistente",
  });

  assert.equal(result.status, "blocked");
  assert.match(result.error ?? "", /planejador local conectado/);
  assert.deepEqual(calls, ["set_workspace", "inspect_project", "research_web", "project_checks"]);
  assert.equal(result.evidence[0]?.title, "Fonte local");
});

test("rejeita escrita absoluta ou com traversal antes de chamar o runtime", async () => {
  const requested: string[] = [];
  const fetcher: Fetcher = async (url) => {
    requested.push(url);
    return response({ok: true, data: {}});
  };
  const ports = new RuntimeHttpPorts({
    baseUrl: "http://127.0.0.1:3000",
    workspaceRoot: "/tmp/assistente",
    fetcher,
  }, {request: async () => true});

  await assert.rejects(
    ports.workspace.write({path: "../fora.txt", content: "não escrever", language: "text"}),
    /não pode sair do workspace/,
  );
  await assert.rejects(
    ports.workspace.write({path: "/tmp/fora.txt", content: "não escrever", language: "text"}),
    /deve ser relativo ao workspace/,
  );
  assert.deepEqual(requested, []);
});

test("preserva o caminho absoluto de create_workspace para o runtime", async () => {
  const requests: Array<{url: string; body: Record<string, unknown>}> = [];
  const fetcher: Fetcher = async (url, init) => {
    requests.push({url, body: JSON.parse(init?.body ?? "{}") as Record<string, unknown>});
    return response({ok: true, data: {workspace: "/tmp/novo-projeto", created: true, selected: true}});
  };
  const ports = new RuntimeHttpPorts({
    baseUrl: "http://127.0.0.1:3000",
    workspaceRoot: "/tmp/assistente",
    fetcher,
  }, {request: async () => true});

  const result = await ports.tools.call("create_workspace", {path: "/tmp/novo projeto"});
  assert.equal(result.ok, true);
  assert.equal(requests[0]?.url, "http://127.0.0.1:3000/api/v1/tools/call");
  assert.deepEqual(requests[0]?.body, {
    tool: "create_workspace",
    arguments: {path: "/tmp/novo projeto"},
    request_id: "agent-core-1",
  });
});

test("lê o catálogo real, remove nomes desconhecidos e rejeita resposta inválida", async () => {
  const fetcher: Fetcher = async (_url, init) => {
    const body = JSON.parse(init?.body ?? "{}") as {tool?: string};
    assert.equal(body.tool, "list_tools");
    return response({ok: true, data: {tools: [
      {name: "read_file", description: "ler"},
      {name: "apply_batch"},
      {name: "read_file"},
      {name: "tool_inexistente"},
      {description: "sem nome"},
    ]}});
  };
  const ports = new RuntimeHttpPorts({baseUrl: "http://runtime.test", workspaceRoot: "/tmp/assistente", fetcher},
    {request: async () => true});
  assert.deepEqual(await ports.tools.listAvailable?.(), ["read_file", "apply_batch"]);

  const invalid = new RuntimeHttpPorts({
    baseUrl: "http://runtime.test", workspaceRoot: "/tmp/assistente",
    fetcher: async () => response({ok: true, data: {tools: "não é uma lista"}}),
  }, {request: async () => true});
  await assert.rejects(() => invalid.tools.listAvailable!(), /catálogo de ferramentas inválido/);
});
