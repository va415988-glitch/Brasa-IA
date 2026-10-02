import type {AgentEvent} from "./contracts.ts";
import type {TaskIndex} from "./task-store.ts";

/** Read-only projection: suggestions never authorize or execute a tool. */
export function taskWorkflow(task: TaskIndex, events: readonly AgentEvent[]) {
  let verification: "unknown" | "stale" | "passed" | "failed" = "unknown";
  let pendingCriteria: string[] = [];
  let pendingChecks: {id: string; detail: string}[] = [];
  let recoveryAttempts = 0;
  let lastEvent: AgentEvent | undefined;
  let blocker: string | undefined;
  for (const event of events) {
    lastEvent = event;
    if (event.kind === "verification.invalidated") verification = "stale";
    if (event.kind === "verification.passed") verification = "passed";
    if (event.kind === "verification.failed") verification = "failed";
    if (event.kind === "verification.recovery.queued" || event.kind === "planner.recovery.requested") recoveryAttempts++;
    if (event.kind === "acceptance.evaluated" && Array.isArray(event.payload?.checks)) {
      pendingChecks = event.payload.checks.flatMap((check: unknown) => {
        if (!check || typeof check !== "object") return [];
        const row = check as Record<string, unknown>;
        return row.passed === false && typeof row.id === "string"
          ? [{id: row.id, detail: typeof row.detail === "string" ? row.detail : row.id}] : [];
      });
      pendingCriteria = pendingChecks.map(check => check.id);
    }
    if (event.phase === "complete" && event.status === "blocked") blocker = event.detail ?? event.title;
  }
  const next = task.status === "completed" ? {action: "review_delivery", reason: "A execução foi encerrada; consulte a entrega e suas evidências."}
    : task.status === "clarifying" ? {action: "answer_questions", reason: "Responda às perguntas pendentes para que o agente possa montar o plano."}
    : task.status === "awaiting_approval" ? {action: "review_approval", reason: "Revise a ação proposta antes de autorizar sua execução."}
    : task.status === "interrupted" ? {action: "inspect_effects", reason: "Confira os efeitos da execução interrompida antes de repetir ações."}
    : task.status === "failed" || task.status === "blocked" ? {action: "inspect_blocker", reason: task.error ?? blocker ?? "Consulte os critérios pendentes e a última observação."}
    : verification === "failed" ? {action: "diagnose", reason: "Use a saída da verificação para escolher uma correção; não repita o mesmo check sem progresso."}
    : verification === "stale" ? {action: "verify_changes", reason: "Houve alteração após a verificação; o resultado anterior não comprova o estado atual."}
    : {action: "observe", reason: "Acompanhe a próxima evidência; ainda não há conclusão da tarefa."};
  return {
    schema: "agent-workflow/v1", taskId: task.taskId, status: task.status,
    objective: task.request.objective, workspaceRoot: task.request.workspaceRoot,
    updatedAt: task.updatedAt, eventCount: events.length,
    currentStage: lastEvent?.phase ?? "observe",
    lastObservation: lastEvent ? {seq: lastEvent.seq, kind: lastEvent.kind, title: lastEvent.title, detail: lastEvent.detail} : null,
    verification, pendingCriteria, pendingChecks, recoveryAttempts,
    next: {...next, advisory: true, authorized: false},
  };
}
