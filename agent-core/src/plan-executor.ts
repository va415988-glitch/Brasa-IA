import type {
  BrainApprovalPort,
  BrainApprovalRequest,
  BrainEvent,
  BrainEventLog,
  BrainStateKind,
  RiskLevel,
} from "./brain-contracts.ts";
import {CognitiveStateMachine} from "./brain-state.ts";

export interface ToolContext {
  taskId: string;
  actionId: string;
  signal: AbortSignal;
}

export interface ToolResult {
  ok: boolean;
  summary: string;
  evidence?: readonly string[];
  output?: unknown;
  retryable?: boolean;
}

export type ToolHandler = (
  input: unknown,
  context: ToolContext,
) => Promise<ToolResult>;

export interface ToolDefinition {
  name: string;
  risk: RiskLevel;
  reversible: boolean;
  description: string;
  handler: ToolHandler;
}

export interface PlanAction {
  id: string;
  tool: string;
  input: unknown;
  reason: string;
  expectedEffect: string;
  requiresApproval?: boolean;
  /** The original user request explicitly authorized this bounded local action. */
  authorizedByRequest?: boolean;
  timeoutMs?: number;
  maxAttempts?: number;
}

export interface BrainPlan {
  id: string;
  taskId: string;
  objective: string;
  actions: readonly PlanAction[];
}

export interface PlanExecutorOptions {
  controller: CognitiveStateMachine;
  tools: readonly ToolDefinition[];
  eventLog?: BrainEventLog;
  approval?: BrainApprovalPort;
  /** Observa transições imediatamente, sem esperar a tarefa terminar. */
  onEvent?: (event: BrainEvent) => void;
  maxActions?: number;
  /** Mantém o cérebro em planejamento para decidir a próxima ação da mesma tarefa. */
  continueTask?: boolean;
}

export interface ExecutedAction {
  actionId: string;
  tool: string;
  status: "completed" | "blocked" | "failed";
  attempts: number;
  summary: string;
  evidence: readonly string[];
}

export interface PlanExecutionReport {
  status: "completed" | "blocked" | "failed";
  planId: string;
  actions: readonly ExecutedAction[];
  evidence: readonly string[];
  error?: string;
}

const riskRank: Record<RiskLevel, number> = {
  none: 0,
  low: 1,
  medium: 2,
  high: 3,
  critical: 4,
};

function sleepWithSignal(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new Error("Ação cancelada antes de iniciar."));
      return;
    }
    const timer = setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new Error("Ação cancelada."));
    }, {once: true});
  });
}

async function withTimeout<T>(
  operation: (signal: AbortSignal) => Promise<T>,
  timeoutMs: number,
): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error("A ação excedeu o timeout de " + timeoutMs + " ms."));
    }, timeoutMs);
  });
  try {
    return await Promise.race([operation(controller.signal), timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export class PlanExecutor {
  private readonly toolMap: Map<string, ToolDefinition>;
  private readonly options: Required<Pick<PlanExecutorOptions, "maxActions">> & Omit<PlanExecutorOptions, "maxActions">;
  private flushedEventCount = 0;

  constructor(options: PlanExecutorOptions) {
    this.toolMap = new Map();
    for (const tool of options.tools) {
      if (!tool.name.trim()) throw new Error("Uma ferramenta precisa de nome.");
      if (this.toolMap.has(tool.name)) throw new Error("Ferramenta duplicada: " + tool.name);
      this.toolMap.set(tool.name, tool);
    }
    this.options = {...options, maxActions: options.maxActions ?? 32};
    if (this.options.maxActions < 1) throw new Error("maxActions deve ser positivo.");
  }

  async execute(plan: BrainPlan): Promise<PlanExecutionReport> {
    if (!plan.id.trim() || !plan.taskId.trim()) {
      return this.block(plan, "O plano precisa de identificadores de plano e tarefa.");
    }
    if (plan.taskId !== this.options.controller.current.taskId) {
      return this.block(plan, "O plano pertence a outra tarefa.");
    }
    const actionIds = plan.actions.map((action) => action.id.trim());
    if (actionIds.some((id) => !id) || new Set(actionIds).size !== actionIds.length) {
      return this.block(plan, "Cada ação do plano precisa de um identificador único.");
    }
    if (plan.actions.length > this.options.maxActions) {
      return this.block(plan, "O plano excede o limite de ações permitido.");
    }
    const executed: ExecutedAction[] = [];
    const evidence: string[] = [];

    for (const action of plan.actions) {
      const tool = this.toolMap.get(action.tool);
      if (!tool) {
        return this.fail(plan, executed, evidence, "Ferramenta não registrada: " + action.tool);
      }
      const needsApproval = action.authorizedByRequest !== true
        && (action.requiresApproval === true || riskRank[tool.risk] >= riskRank.medium);
      if (needsApproval) {
        this.transition(
          "awaiting_approval",
          "A ação exige aprovação por causa do risco " + tool.risk + ".",
          {planId: plan.id, actionId: action.id, tool: tool.name, risk: tool.risk,
            reversible: tool.reversible, expectedEffect: action.expectedEffect},
        );
        const request: BrainApprovalRequest = {
          taskId: plan.taskId,
          actionId: action.id,
          tool: tool.name,
          risk: tool.risk,
          reason: action.reason,
          reversible: tool.reversible,
          expectedEffect: action.expectedEffect,
        };
        if (!this.options.approval || !(await this.options.approval.request(request))) {
          this.transition("abstaining", "A aprovação não foi concedida.", {planId: plan.id, actionId: action.id});
          executed.push({
            actionId: action.id,
            tool: tool.name,
            status: "blocked",
            attempts: 0,
            summary: "Ação bloqueada aguardando aprovação.",
            evidence: [],
          });
          await this.flushNewEvents();
          return {status: "blocked", planId: plan.id, actions: executed, evidence};
        }
      }

      const maxAttempts = Math.max(1, Math.min(action.maxAttempts ?? 1, 3));
      let completed: ToolResult | undefined;
      let lastError = "";
      let attemptsUsed = 0;
      for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        attemptsUsed = attempt;
        try {
          this.transition(
            "executing",
            "Executando a ação autorizada.",
            {planId: plan.id, actionId: action.id, tool: tool.name, attempt, expectedEffect: action.expectedEffect},
          );
          completed = await withTimeout(
            (signal) => tool.handler(action.input, {taskId: plan.taskId, actionId: action.id, signal}),
            Math.max(1, Math.min(action.timeoutMs ?? 30000, 300000)),
          );
          this.transition(
            "verifying",
            completed.ok ? "Resultado bruto recebido; iniciando verificação." : "A ferramenta devolveu falha.",
            {planId: plan.id, actionId: action.id, tool: tool.name, ok: completed.ok,
              summary: completed.summary, evidence: [...(completed.evidence ?? [])],
              error: completed.ok ? undefined : completed.summary},
          );
          if (completed.ok) break;
          lastError = completed.summary;
          if (!completed.retryable || attempt === maxAttempts) break;
          this.transition("recovering", "A falha foi marcada como recuperável.", {
            planId: plan.id,
            actionId: action.id,
            tool: tool.name,
            error: completed.summary,
          });
        } catch (error) {
          lastError = error instanceof Error ? error.message : String(error);
          this.transition("recovering", "A ação lançou uma falha; o efeito pode ser indeterminado, então não será repetida automaticamente.", {
            planId: plan.id,
            actionId: action.id,
            tool: tool.name,
            error: lastError,
          });
          // Só repetimos quando a própria ferramenta retornou ok:false e
          // marcou o resultado como retryable. Uma exceção/timeout pode ocorrer
          // depois do efeito colateral e repetir poderia duplicá-lo.
          break;
        }
      }

      const result = completed?.ok === true;
      const row: ExecutedAction = {
        actionId: action.id,
        tool: tool.name,
        status: result ? "completed" : "failed",
        attempts: attemptsUsed,
        summary: result ? completed?.summary ?? "Ação concluída." : lastError || completed?.summary || "Ação falhou.",
        evidence: result ? [...(completed?.evidence ?? [])] : [],
      };
      executed.push(row);
      evidence.push(...row.evidence);
      if (!result) {
        this.transition("abstaining", "A ação não foi verificada com sucesso.", {
          planId: plan.id,
          actionId: action.id,
          tool: tool.name,
          error: row.summary,
        });
        await this.flushNewEvents();
        return {status: "failed", planId: plan.id, actions: executed, evidence, error: row.summary};
      }
      if (executed.length < plan.actions.length) {
        this.transition("planning", "A ação foi verificada; retomando o plano.", {planId: plan.id});
      }
    }

    if (this.options.continueTask) {
      this.transition("planning", "A ação foi verificada; o núcleo decidirá o próximo passo da tarefa.", {planId: plan.id});
    } else {
      this.transition("delivering", "Todas as ações foram verificadas; preparando a entrega.", {planId: plan.id});
      this.transition("completed", "Plano concluído com evidências.", {planId: plan.id});
    }
    await this.flushNewEvents();
    return {status: "completed", planId: plan.id, actions: executed, evidence};
  }

  private transition(
    to: BrainStateKind,
    reason: string,
    payload: Record<string, unknown>,
  ): void {
    const event = this.options.controller.transition(to, reason, payload);
    this.options.onEvent?.(event);
  }

  private async flushNewEvents(): Promise<void> {
    if (!this.options.eventLog) return;
    const events = this.options.controller.events;
    for (const event of events.slice(this.flushedEventCount)) {
      await this.options.eventLog.append(event);
      this.flushedEventCount += 1;
    }
  }

  private async block(plan: BrainPlan, error: string): Promise<PlanExecutionReport> {
    this.transition("abstaining", error, {planId: plan.id, error});
    await this.flushNewEvents();
    return {status: "blocked", planId: plan.id, actions: [], evidence: [], error};
  }

  private async fail(
    plan: BrainPlan,
    actions: readonly ExecutedAction[],
    evidence: readonly string[],
    error: string,
  ): Promise<PlanExecutionReport> {
    this.transition("abstaining", error, {planId: plan.id, error});
    await this.flushNewEvents();
    return {status: "failed", planId: plan.id, actions, evidence, error};
  }
}

export {sleepWithSignal};
