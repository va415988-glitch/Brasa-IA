import assert from "node:assert/strict";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {FileTaskRunStore} from "../src/task-store.ts";
import {taskWorkflow} from "../src/workflow.ts";

test("consulta tarefa persistida e marca execução incerta após reinício", async () => {
  const directory = await mkdtemp(join(tmpdir(), "ia-agent-task-"));
  try {
    const store = new FileTaskRunStore(directory);
    await store.begin("task-a", {
      prompt: "Analise o projeto", objective: "analyze", workspaceRoot: directory,
      operationId: "agent-core-operation-a",
    });
    await store.appendEvent({seq: 1, taskId: "task-a", phase: "observe", status: "running",
      kind: "task.started", title: "Tarefa iniciada"});
    assert.equal((await store.list("agent-core-operation-a")).length, 1);
    assert.equal((await store.read("task-a")).events.length, 1);
    assert.equal(await store.markInterruptedOnStartup(), 1);
    assert.equal((await store.read("task-a")).task.status, "interrupted");

    await store.saveReport({taskId: "task-a", status: "completed", evidence: [], artifacts: [],
      events: [], finalText: "Projeto analisado."});
    const saved = await store.read("task-a");
    assert.equal(saved.task.status, "completed");
    assert.equal((saved.report as {finalText?: string})?.finalText, "Projeto analisado.");
    assert.equal(await store.markInterruptedOnStartup(), 0);
    await store.begin("task-b", {prompt: "Crie um arquivo", objective: "build", operationId: "agent-core-operation-b"});
    await store.saveReport({taskId: "task-b", status: "blocked", evidence: [], artifacts: [], events: []});
    assert.equal((await store.read("task-b")).task.status, "blocked");
    await store.saveReport({taskId: "task-b", status: "blocked", evidence: [], artifacts: [], events: []}, true);
    assert.equal((await store.read("task-b")).task.status, "awaiting_approval");
    await store.savePendingTaskControl("task-b", {schema: "agent-pending-control/v1", kind: "clarification"});
    assert.deepEqual(await store.loadPendingTaskControl("task-b"),
      {schema: "agent-pending-control/v1", kind: "clarification"});
    await store.saveResumeResult("task-b", "resume-one", {ok: true});
    assert.deepEqual(await store.loadResumeResult("task-b", "resume-one"), {ok: true});
    await store.savePendingTaskControl("task-b", null);
    assert.equal(await store.loadPendingTaskControl("task-b"), undefined);
    await store.begin("task-c", {prompt: "Crie algo", objective: "build", operationId: "agent-core-operation-c"}, "clarifying");
    await store.saveClarificationState("task-c", {questions: ["Qual resultado esperado?"]});
    await store.appendEvent({seq: 0, taskId: "task-c", phase: "observe", status: "running",
      kind: "requirements.clarification.required", title: "Aguardando esclarecimento"});
    const clarification = await store.read("task-c");
    assert.equal(clarification.task.status, "clarifying");
    assert.deepEqual(clarification.clarification, {questions: ["Qual resultado esperado?"]});
    assert.equal(clarification.events.length, 1);
    const workflow = taskWorkflow(clarification.task, clarification.events);
    assert.equal(workflow.status, "clarifying");
    assert.equal(workflow.next.action, "answer_questions");
    const blockedWorkflow = taskWorkflow({...clarification.task, status: "blocked"}, [{
      seq: 1, taskId: "task-c", phase: "verify", status: "blocked",
      kind: "acceptance.evaluated", title: "Critérios pendentes", payload: {checks: [
        {id: "build.change", passed: false, detail: "Nenhuma escrita confirmada."},
        {id: "build.verification", passed: false, detail: "Falta executar os testes."},
        {id: "delivery.summary", passed: true, detail: "Resumo disponível."},
      ]},
    }]);
    assert.deepEqual(blockedWorkflow.pendingCriteria, ["build.change", "build.verification"]);
    assert.deepEqual(blockedWorkflow.pendingChecks.map(check => check.detail),
      ["Nenhuma escrita confirmada.", "Falta executar os testes."]);
    await store.begin("task-c", {prompt: "Crie algo", objective: "build", operationId: "agent-core-operation-c"});
    assert.equal((await store.read("task-c")).task.status, "running");
    assert.equal((await store.read("task-c")).clarification, null);
    await assert.rejects(store.read("task-missing"), /não encontrada/);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});
