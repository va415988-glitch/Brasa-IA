import assert from "node:assert/strict";
import test from "node:test";
import {LocalContextHttp} from "../src/index.ts";
import type {Fetcher} from "../src/index.ts";

test("ContextPort solicita agent-context/v2 e preserva origem e confiança", async () => {
  let payload: Record<string, unknown> = {};
  const fetcher: Fetcher = async (_url, init) => {
    payload = JSON.parse(init?.body ?? "{}") as Record<string, unknown>;
    return {ok: true, json: async () => ({ok: true, schema: "agent-context/v2", status: "ready",
      query: "sistema de entregas", intent: "workspace", topic: "entregas", personality_ref: "local-personality/v1",
      history: [{role: "user", content: "resumo"}], conversation_memory: "Contexto breve.",
      session_memory: [{kind: "decisão", text: "TypeScript", source: "conversation-history", confidence: 0.9}],
      relevant_skills: [{topic: "TypeScript", source: "local-skill-registry", confidence: 0.8}],
      evidence: {items: [{title: "Guia", excerpt: "Conteúdo", source: "local://guide", confidence: 0.6}]}, limits: {history_messages: 12}})};
  };
  const context = await new LocalContextHttp({baseUrl: "http://127.0.0.1:3101", fetcher}).build({
    query: "sistema de entregas", messages: [{role: "user", content: "resumo"}], conversationId: "chat-14",
    preferences: {language: "pt-BR"},
  });
  assert.equal(payload.schema, "agent-context-request/v2");
  assert.equal(payload.conversation_id, "chat-14");
  assert.equal(context.schema, "agent-context/v2");
  assert.equal(context.sessionMemory[0]?.source, "conversation-history");
  assert.equal(context.sessionMemory[0]?.confidence, 0.9);
  assert.equal(context.evidence[0]?.source, "local://guide");
  assert.equal(context.relevantSkills[0]?.topic, "TypeScript");
});

test("ContextPort bloqueia endpoints que não são loopback local", () => {
  assert.throws(() => new LocalContextHttp({baseUrl: "https://example.com"}), /loopback local/);
});
