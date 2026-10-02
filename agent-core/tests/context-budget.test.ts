import assert from "node:assert/strict";
import test from "node:test";
import {
  TARGET_GENERATION_TOKENS,
  TARGET_PRODUCTION_CONTEXT_TOKENS,
  estimateTokens,
  fitContextWindow,
} from "../src/index.ts";

test("o orçamento reserva 4096 tokens dentro do contexto permanente de 32k", () => {
  const window = fitContextWindow([
    {role: "system", content: "Regras do agente."},
    {role: "user", content: "Pedido atual."},
  ]);
  assert.equal(window.maxContextTokens, TARGET_PRODUCTION_CONTEXT_TOKENS);
  assert.equal(window.reservedGenerationTokens, TARGET_GENERATION_TOKENS);
  assert.equal(window.inputBudgetTokens, 28_672);
  assert.ok(window.estimatedInputTokens <= window.inputBudgetTokens);
});

test("preserva sistema e mensagem atual e registra descarte do histórico", () => {
  const window = fitContextWindow([
    {role: "system", content: "Nunca invente evidências.", priority: "critical"},
    {role: "user", content: "Contexto antigo ".repeat(80), priority: "discardable"},
    {role: "assistant", content: "Resposta antiga ".repeat(80), priority: "discardable"},
    {role: "user", content: "Faça a verificação final.", priority: "critical"},
  ], {maxContextTokens: 80, reserveGenerationTokens: 20});
  assert.equal(window.messages[0]?.role, "system");
  assert.equal(window.messages.at(-1)?.content, "Faça a verificação final.");
  assert.ok(window.droppedMessages >= 1);
  assert.ok(window.warnings.length >= 1);
});

test("estimativa é determinística para texto e não aceita conteúdo vazio como zero", () => {
  assert.equal(estimateTokens(""), 1);
  assert.equal(estimateTokens("1234"), 1);
  assert.equal(estimateTokens("12345"), 2);
});
