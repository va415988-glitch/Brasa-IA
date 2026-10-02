import assert from "node:assert/strict";
import test from "node:test";
import {parseAgentInput, parseAgentResumeInput} from "../src/server-contract.ts";

test("valida o contrato do endpoint do AgentCore", () => {
  assert.deepEqual(parseAgentInput({
    prompt: "Criar o plano inicial",
    objective: "build",
    workspaceRoot: "/tmp/assistente",
    approved: true,
    operationId: "agent-core-request-1",
  }), {
    prompt: "Criar o plano inicial",
    objective: "build",
    workspaceRoot: "/tmp/assistente",
    history: [],
    approved: true,
    operationId: "agent-core-request-1",
  });
  assert.deepEqual(parseAgentInput({
    prompt: "Pesquise fontes oficiais sobre CMake.",
    objective: "research",
    operationId: "agent-core-research-1",
  }), {
    prompt: "Pesquise fontes oficiais sobre CMake.",
    objective: "research",
    workspaceRoot: undefined,
    history: [],
    approved: false,
    operationId: "agent-core-research-1",
  });
  assert.throws(
    () => parseAgentInput({prompt: "sem workspace", objective: "build"}),
    /workspace absoluto/,
  );
  assert.throws(
    () => parseAgentInput({prompt: "objetivo", objective: "shell", workspaceRoot: "/tmp/assistente"}),
    /Objetivo inválido/,
  );
  assert.throws(
    () => parseAgentInput({prompt: "objetivo", objective: "build", workspaceRoot: "/tmp/assistente", operationId: "other-task"}),
    /Identificador de execução inválido/,
  );
  const withAttachment = parseAgentInput({
    prompt: "Analise este arquivo",
    objective: "analyze",
    workspaceRoot: "/tmp/assistente",
    operationId: "agent-core-attachment",
    attachments: [{path: "docs/spec.md", content: "requisito"}],
  });
  assert.equal(withAttachment.attachments?.[0]?.path, "docs/spec.md");
  assert.throws(() => parseAgentInput({
    prompt: "Analise",
    objective: "analyze",
    workspaceRoot: "/tmp/assistente",
    operationId: "agent-core-attachment-absolute",
    attachments: [{path: "/etc/passwd", content: "x"}],
  }), /Caminho de anexo inválido/);
});

test("normaliza agent-request/v2 snake_case e preferências declaradas", () => {
  const input = parseAgentInput({
    schema: "agent-request/v2",
    request_id: "req-82",
    operation_id: "agent-core-82",
    conversation_id: "chat-14",
    prompt: "Crie um sistema web de entregas.",
    objective: "auto",
    workspace_root: "/tmp/entregas",
    approved: true,
    preferences: {language: "pt-BR", stack: "React e TypeScript"},
    preparation_id: "prep-local-51",
  });
  assert.equal(input.requestId, "req-82");
  assert.equal(input.conversationId, "chat-14");
  assert.deepEqual(input.preferences, {language: "pt-BR", stack: "React e TypeScript"});
  assert.equal(input.workspaceRoot, "/tmp/entregas");
  assert.equal(input.preparationId, "prep-local-51");
  assert.equal(input.approved, false);
  assert.throws(() => parseAgentInput({schema: "agent-request/v2", prompt: "x", objective: "build",
    workspace_root: "/tmp", preferences: {allow_writes: true}}), /Preferência não reconhecida/);
});

test("valida retomadas discriminadas de esclarecimento e aprovação", () => {
  assert.deepEqual(parseAgentResumeInput({schema: "agent-resume/v1", request_id: "resume-1",
    kind: "clarification", answers: ["Node e TypeScript"]}), {
    schema: "agent-resume/v1", requestId: "resume-1", kind: "clarification", answers: ["Node e TypeScript"],
  });
  assert.deepEqual(parseAgentResumeInput({schema: "agent-resume/v1", request_id: "resume-2",
    kind: "approval", action_id: "action-93", decision: "approve"}), {
    schema: "agent-resume/v1", requestId: "resume-2", kind: "approval", actionId: "action-93", decision: "approve",
  });
  assert.throws(() => parseAgentResumeInput({schema: "agent-resume/v1", request_id: "resume-3",
    kind: "approval", action_id: "action-93", decision: "maybe"}), /approve ou reject/);
});
