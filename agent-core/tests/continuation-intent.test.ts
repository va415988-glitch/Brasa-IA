import assert from "node:assert/strict";
import test from "node:test";
import {compatibleTaskContinuation} from "../src/task-continuity.ts";
import type {AgentInput} from "../src/contracts.ts";

const stored = {prompt: "Crie um programa Python para registrar gastos.", objective: "build" as const};
const request = (history: AgentInput["history"]): AgentInput => ({prompt: "Continue.", objective: "auto", history});

test("continuação automática exige o objetivo humano mais recente compatível com a tarefa salva", () => {
  assert.equal(compatibleTaskContinuation(request([{role: "user", content: stored.prompt}]), stored), true);
  assert.equal(compatibleTaskContinuation(request([{role: "user", content: stored.prompt + "\n\nWorkspace local: /tmp/projeto"}]), stored), true);
  assert.equal(compatibleTaskContinuation(request(undefined), stored), false);
  assert.equal(compatibleTaskContinuation(request([{role: "assistant", content: stored.prompt}]), stored), false);
  assert.equal(compatibleTaskContinuation(request([{role: "user", content: "Crie um app para consultas médicas."}]), stored), false);
});

test("planejamento, cancelamento e conversa posterior impedem retomada de uma criação antiga", () => {
  for (const latest of [
    "Me ajuda a planejar um app para entregadores autônomos?",
    "Não quero código agora; quais telas esse app precisa ter?",
    "Cancele a tarefa anterior.",
    "Vamos falar de outro assunto.",
    "O que é um token?",
  ]) {
    const history = [{role: "user" as const, content: stored.prompt},
      {role: "user" as const, content: latest},
      {role: "assistant" as const, content: "Vou continuar criando os arquivos."}];
    assert.equal(compatibleTaskContinuation(request(history), stored), false, latest);
  }
});

test("uma nova ordem produtiva usa o novo pedido em vez de retomar o checkpoint antigo", () => {
  assert.equal(compatibleTaskContinuation({...request([{role: "user", content: stored.prompt}]),
    prompt: "Agora crie o protótipo desse app."}, stored), false);
});
