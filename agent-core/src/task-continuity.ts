import type {AgentInput, AgentPlannerCognition, PlannerMessage, TaskCheckpoint} from "./contracts.ts";
import {capabilityFor} from "./capability-registry.ts";
import {latestHumanIntent} from "./requirements.ts";

/** A concise command resumes persisted state; conversational output never authorizes it. */
export function continuationRequested(prompt: string): boolean {
  return /^(?:por favor[, ]*)?(?:continue|prossiga|retome|siga)(?:\s+(?:o trabalho|a tarefa|o projeto|de onde parou))?[.!\s]*$/i.test(prompt.trim());
}

/** Match the latest human objective before a short command resumes stored work. */
export function compatibleTaskContinuation(input: AgentInput, stored: Pick<AgentInput, "prompt" | "objective">): boolean {
  if (input.objective !== "auto" || !continuationRequested(input.prompt)) return false;
  const anchor = latestHumanIntent(input.prompt, input.history);
  if (!anchor || anchor.objective === "conversation") return false;
  const clean = (text: string) => text.replace(/\n+Workspace local:[\s\S]*$/i, "")
    .replace(/\n+Arquivo ativo: [^\n]+ · [^\n]+ · \d+ linhas · cursor na linha \d+\.?\s*$/i, "").trim();
  return clean(stored.prompt) === anchor.prompt
    && (stored.objective === "auto" || stored.objective === anchor.objective);
}

export function taskWorkingState(input: {
  request: AgentInput;
  cognition?: AgentPlannerCognition;
  pendingCriteria: readonly string[];
  successfulTools: readonly string[];
  changedPaths: readonly string[];
  verification?: {executed: boolean; passed: boolean};
  completedCalls?: readonly {id: string; tool: string; arguments: Record<string, unknown>; ok: boolean}[];
}): Record<string, unknown> {
  return {
    schema: "agent-working-state/v1",
    goal: input.request.prompt,
    objective: input.request.objective,
    constraints: input.cognition?.constraints ?? [],
    assumptions: input.cognition?.assumptions ?? [],
    acceptanceCriteria: input.cognition?.acceptanceCriteria ?? [],
    pendingCriteria: [...input.pendingCriteria],
    successfulTools: [...new Set(input.successfulTools)],
    changedPaths: [...input.changedPaths],
    verification: input.verification ?? {executed: false, passed: false},
    recentActions: (input.completedCalls ?? []).slice(-8).map(call => ({
      id: call.id, tool: call.tool, ok: call.ok,
      ...(typeof call.arguments.path === "string" ? {path: call.arguments.path} : {}),
    })),
    nextStep: input.pendingCriteria.length ? "Resolver os critérios pendentes usando as observações; confirmar alterações com testes."
      : "Preparar a entrega a partir dos resultados observados.",
  };
}

/** Compact bodies without turning partial JSON into a successful source excerpt. */
function compactValue(value: unknown, limit: number, depth = 0): unknown {
  if (typeof value === "string") return value.length > limit
    ? value.slice(0, limit) + "\n[trecho reduzido; consulte novamente para editar ou confirmar detalhes]" : value;
  if (value === null || typeof value !== "object") return value;
  if (depth > 7) return "[estrutura reduzida]";
  if (Array.isArray(value)) return value.slice(0, 20).map(item => compactValue(item, limit, depth + 1));
  if (value && typeof value === "object") return Object.fromEntries(
    Object.entries(value).slice(0, 32).map(([key, item]) => [key, compactValue(item, limit, depth + 1)]));
  return value;
}

export function planningMessages(messages: readonly PlannerMessage[], workingState: Record<string, unknown>): PlannerMessage[] {
  const tools = messages.flatMap((message, index) => message.role === "tool" ? [index] : []);
  const recent = new Set(tools.slice(-3));
  const compact = messages.map((message, index): PlannerMessage => {
    if (message.role !== "tool") return {...message, content: message.content.slice(0, 24000)};
    try {
      const value: unknown = JSON.parse(message.content);
      const original = JSON.stringify(value);
      const budget = recent.has(index) ? 12000 : 3000;
      let limit = recent.has(index) ? 8000 : 800;
      let bounded = compactValue(value, limit);
      while (JSON.stringify(bounded).length > budget && limit > 32) {
        limit = Math.max(32, Math.floor(limit / 2));
        bounded = compactValue(value, limit);
      }
      if (bounded && typeof bounded === "object" && !Array.isArray(bounded)
          && JSON.stringify(bounded) !== original) {
        const row = bounded as Record<string, unknown>;
        if (row.data && typeof row.data === "object" && !Array.isArray(row.data)) {
          (row.data as Record<string, unknown>).context_compacted = true;
          (row.data as Record<string, unknown>).truncated = true;
        }
      }
      return {...message, content: JSON.stringify(bounded)};
    } catch {
      return {...message, content: JSON.stringify({tool: message.tool ?? "unknown", ok: false,
        error: "Resultado histórico fora do contrato; consulte a fonte novamente."})};
    }
  });
  // The objective survives even when earlier chat history is removed.
  return [{role: "tool", tool: "working_state", content: JSON.stringify({tool: "working_state", ok: true, data: workingState})},
    ...compact];
}

export function callIdentity(tool: string, arguments_: Record<string, unknown>): string {
  const sorted = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(sorted);
    if (value && typeof value === "object") return Object.fromEntries(
      Object.entries(value).sort(([left], [right]) => left.localeCompare(right)).map(([key, item]) => [key, sorted(item)]));
    return value;
  };
  return tool + ":" + JSON.stringify(sorted(arguments_));
}

export function checkpointResume(checkpoint: TaskCheckpoint, workspaceRoot?: string) {
  if (workspaceRoot && checkpoint.request.workspaceRoot !== workspaceRoot) {
    throw new Error("A tarefa pertence a outro workspace; retome no projeto original.");
  }
  if (checkpoint.inFlight && capabilityFor(checkpoint.inFlight.tool).retry !== "safe_read") {
    throw new Error("A execução de " + checkpoint.inFlight.tool
      + " foi interrompida sem resultado confirmado. Confira os efeitos no projeto antes de retomar; a ação não será repetida automaticamente.");
  }
  return {...checkpoint.resume, brainSnapshot: undefined,
    ...(checkpoint.inFlight ? {pendingToolCall: checkpoint.inFlight} : {})};
}
