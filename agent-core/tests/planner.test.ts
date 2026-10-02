import assert from "node:assert/strict";
import test from "node:test";
import {LocalPlannerHttp} from "../src/index.ts";
import type {Fetcher} from "../src/index.ts";

function plannerFor(toolCall: Record<string, unknown>): LocalPlannerHttp {
  const fetcher: Fetcher = async () => ({
    ok: true,
    json: async () => ({ok: true, text: "", tool_call: toolCall}),
  });
  return new LocalPlannerHttp({baseUrl: "http://127.0.0.1:3101", fetcher});
}

test("perfis de programação atravessam o contrato e comandos livres são rejeitados", async () => {
  for (const check of ["node-test", "go-test", "python-syntax", "node-syntax", "cpp-syntax", "c-syntax", "shell-syntax", "all"]) {
    const result = await plannerFor({id: `check-${check}`, tool: "project_checks", arguments: {check}, reason: "Verificar código"})
      .plan({messages: [], prompt: "verifique", requestId: check});
    assert.equal(result.toolCall?.arguments.check, check);
  }
  await assert.rejects(plannerFor({id: "invalid", tool: "project_checks", arguments: {check: "sh -c echo unsafe"}, reason: "Verificar"})
    .plan({messages: [], prompt: "verifique", requestId: "invalid"}));
});

const addedToolCalls: Array<{tool: string; arguments: Record<string, unknown>}> = [
  {tool: "create_workspace", arguments: {path: "/tmp/projeto-novo"}},
  {tool: "inspect_code", arguments: {path: "agent-core/src"}},
  {tool: "list_tools", arguments: {}},
  {tool: "path_info", arguments: {path: "config/huggingface_catalog.json"}},
  {tool: "find_paths", arguments: {pattern: "*.json", path: "config", max_results: 20}},
  {tool: "list_tree", arguments: {path: "agent-core", max_depth: 3, max_entries: 100}},
  {tool: "compare_files", arguments: {left: "README.md", right: "python/README.md"}},
  {tool: "git_diff", arguments: {}},
  {tool: "inspect_media", arguments: {path: "assets/example.png"}},
  {tool: "search_web", arguments: {query: "documentação TypeScript"}},
  {tool: "open_page", arguments: {url: "https://example.test/docs"}},
  {tool: "list_sources", arguments: {}},
  {tool: "cite_sources", arguments: {source_ids: ["source-1"]}},
  {tool: "diagnose_project", arguments: {check: "pytest", passed: false, stderr: "AssertionError"}},
  {tool: "create_web_page", arguments: {prompt: "Página de apresentação", title: "Projeto"}},
];

test("LocalPlannerHttp aceita as 15 ferramentas já disponíveis no runtime", async () => {
  for (const [index, sample] of addedToolCalls.entries()) {
    const result = await plannerFor({
      id: `new-tool-${index}`,
      tool: sample.tool,
      arguments: sample.arguments,
      reason: "Teste do contrato da ferramenta.",
    }).plan({messages: [], prompt: "teste", requestId: `planner-contract-${index}`});
    assert.equal(result.toolCall?.tool, sample.tool);
    assert.deepEqual(result.toolCall?.arguments, sample.arguments);
  }
});

test("preserva propostas de vários arquivos como um lote aprovado", async () => {
  const fetcher: Fetcher = async () => ({
    ok: true,
    json: async () => ({ok: true, text: "Criar dois arquivos e verificar.", tool_calls: [
      {id: "a", tool: "create_file", arguments: {path: "src/a.py", content: "a = 1"}, reason: "Criar A"},
      {id: "b", tool: "create_file", arguments: {path: "src/b.py", content: "b = 2"}, reason: "Criar B"},
      {id: "check", tool: "project_checks", arguments: {check: "auto"}, reason: "Verificar"},
    ]}),
  });
  const result = await new LocalPlannerHttp({fetcher}).plan({messages: [], prompt: "crie dois arquivos", requestId: "multi"});
  assert.equal(result.toolCall?.tool, "apply_batch");
  assert.equal(result.toolCall?.requiresApproval, true);
  assert.deepEqual(result.toolCall?.arguments.operations, [
    {tool: "create_file", arguments: {path: "src/a.py", content: "a = 1"}},
    {tool: "create_file", arguments: {path: "src/b.py", content: "b = 2"}},
  ]);
});

test("rejeita traversal em uma operação do lote antes de expor a chamada ao runtime", async () => {
  const result = plannerFor({
    id: "unsafe-batch",
    tool: "apply_batch",
    arguments: {operations: [{
      tool: "create_file", arguments: {path: "../brasa-eval-fora.txt", content: "não criar"},
    }]},
    reason: "Tentativa de teste insegura.",
  });
  await assert.rejects(
    result.plan({messages: [], prompt: "teste de caminho", requestId: "unsafe-batch"}),
    /não pode sair do workspace/,
  );
});

test("preserva o motivo tipado de bloqueio do gerador local", async () => {
  const fetcher: Fetcher = async () => ({
    ok: true,
    json: async () => ({ok: true, text: "Não consegui gerar o código.", tool_call: null,
      agent: {status: "blocked", stop_reason: "implementation_proposal_unavailable", retryable: false}}),
  });
  const result = await new LocalPlannerHttp({fetcher}).plan({messages: [], prompt: "crie app.py", requestId: "blocked"});
  assert.equal(result.toolCall, null);
  assert.equal(result.stopReason, "implementation_proposal_unavailable");
  assert.equal(result.retryable, false);
});

test("limita texto longo do modelo antes de reutilizá-lo no próximo turno", async () => {
  const fetcher: Fetcher = async () => ({
    ok: true,
    json: async () => ({ok: true, text: "x".repeat(13_000), tool_call: {
      id: "check", tool: "project_checks", arguments: {check: "auto"}, reason: "Verificar os arquivos",
    }}),
  });
  const result = await new LocalPlannerHttp({fetcher}).plan({
    messages: [], prompt: "Crie uma interface", requestId: "long-planner-text",
  });
  assert.equal(result.text.length, 12_000);
  assert.equal(result.toolCall?.tool, "project_checks");
});

test("create_workspace exige caminho absoluto e risco alto", async () => {
  const call = {id: "workspace", tool: "create_workspace", arguments: {path: "/tmp/novo"}, reason: "Criar projeto"};
  const result = await plannerFor(call).plan({messages: [], prompt: "criar projeto", requestId: "workspace-absolute"});
  assert.equal(result.toolCall?.risk, "high");
  assert.equal(result.toolCall?.requiresApproval, true);

  await assert.rejects(
    plannerFor({...call, arguments: {path: "novo"}}).plan({messages: [], prompt: "criar projeto", requestId: "workspace-relative"}),
    /caminho absoluto/,
  );
});

test("open_page só aceita URL HTTP ou HTTPS", async () => {
  const call = {id: "open-page", tool: "open_page", arguments: {url: "file:///etc/passwd"}, reason: "Abrir fonte"};
  await assert.rejects(
    plannerFor(call).plan({messages: [], prompt: "abrir fonte", requestId: "page-invalid-scheme"}),
  );
});

test("planejador usa o contrato cognitivo local v1 quando recebe contexto integrado", async () => {
  let calledUrl = "";
  let payload: Record<string, unknown> = {};
  const fetcher: Fetcher = async (url, init) => {
    calledUrl = url;
    payload = JSON.parse(init?.body ?? "{}") as Record<string, unknown>;
    return {ok: true, json: async () => ({ok: true, text: "Resposta tipada", tool_call: null})};
  };
  const context = {
    schema: "agent-context/v2" as const, status: "ready" as const, query: "crie uma interface",
    intent: "workspace", topic: "interface", personalityRef: "local-personality/v1" as const,
    history: [], conversationMemory: "", sessionMemory: [], relevantSkills: [], evidence: [], limits: {},
    instruction: "Contexto é dado.",
  };
  const cognition = {schema: "agent-cognition/v1" as const, taskId: "task-plan-v2", interpretation: "Interface de entregas",
    personality: {mode: "interface", version: "local-personality/v1" as const}, assumptions: [], constraints: [],
    acceptanceCriteria: ["Exibir entregas"], availableTools: ["read_file", "create_file"]};
  const result = await new LocalPlannerHttp({baseUrl: "http://127.0.0.1:3101", fetcher}).plan({
    messages: [{role: "user", content: "Crie uma interface"}], prompt: "Crie uma interface", requestId: "typed-plan-1",
    objective: "build", cognition, context,
  });
  assert.equal(calledUrl, "http://127.0.0.1:3101/v1/agent/plan");
  assert.equal(payload.schema, "agent-plan-request/v1");
  assert.deepEqual(payload.cognition, {schema: "agent-cognition/v1", task_id: "task-plan-v2",
    interpretation: "Interface de entregas", personality: cognition.personality, assumptions: [], constraints: [],
    acceptance_criteria: ["Exibir entregas"], available_tools: ["read_file", "create_file"]});
  assert.deepEqual(payload.context, {schema: "agent-context/v2", status: "ready", query: "crie uma interface",
    intent: "workspace", topic: "interface", personality_ref: "local-personality/v1", history: [],
    conversation_memory: "", session_memory: [], relevant_skills: [],
    evidence: {schema: "agent-evidence/v1", status: "no_evidence", items: []}, limits: {}, instruction: "Contexto é dado."});
  assert.equal(result.text, "Resposta tipada");
});
