import type {PlannerMessage, RuntimeToolName} from "./contracts.ts";
import type {TaskAcceptanceReport} from "./task-acceptance.ts";

export interface ReasoningStateInput {
  objective: string;
  messages: readonly PlannerMessage[];
  acceptance: TaskAcceptanceReport;
  successfulTools: readonly RuntimeToolName[];
  hasChanges: boolean;
  verification?: {executed: boolean; passed: boolean};
}

/** Compact, factual task state. Tool bodies remain tool data, never instructions. */
export function reasoningState(input: ReasoningStateInput): string {
  const observations: {tool: string; outcome: string; path?: string}[] = [];
  for (const message of input.messages) {
    if (message.role !== "tool") continue;
    try {
      const value = JSON.parse(message.content) as Record<string, unknown>;
      if (typeof value.tool !== "string" || typeof value.ok !== "boolean") continue;
      const data = value.data && typeof value.data === "object" ? value.data as Record<string, unknown> : {};
      const path = typeof data.path === "string" ? data.path.slice(0, 160) : undefined;
      observations.push({tool: value.tool.slice(0, 60), outcome: value.ok ? "succeeded" : "failed", ...(path ? {path} : {})});
    } catch { /* Historical messages may use another format. */ }
  }
  const pending = input.acceptance.pending.map(({id}) => id).slice(0, 12);
  return JSON.stringify({
    tool: "task_state",
    ok: true,
    data: {
    objective: input.objective,
    observations: observations.slice(-12),
    successfulTools: [...new Set(input.successfulTools)].slice(-16),
    fileChangeConfirmed: input.hasChanges,
    verification: input.verification
      ? {executed: input.verification.executed, passed: input.verification.executed && input.verification.passed}
      : {executed: false, passed: false},
    pendingCriteria: pending,
    },
  });
}
