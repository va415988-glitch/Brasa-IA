import type {
  BrainMemory,
  BrainPreparation,
  BrainRequest,
  Confidence,
  RequirementConstraint,
} from "./brain-contracts.ts";
import type {MemoryPort} from "./contracts.ts";
import {CognitiveBrain, type CognitiveBrainOptions} from "./cognitive-brain.ts";
import {CuratedWorkflowDataset} from "./workflow-dataset.ts";
import {operationalPolicyFor} from "./capability-registry.ts";

export interface OperationalBrainOptions extends CognitiveBrainOptions {
  dataset?: CuratedWorkflowDataset;
}

/** Coordenador de objetivo, política operacional e precedentes curados. */
export class OperationalBrain extends CognitiveBrain {
  private readonly dataset: CuratedWorkflowDataset;
  private readonly workflowMemory?: BrainMemory;

  constructor(options: OperationalBrainOptions = {}) {
    super(options);
    this.dataset = options.dataset ?? new CuratedWorkflowDataset();
    this.workflowMemory = options.memory;
  }

  override async prepare(
    request: BrainRequest,
    priorConstraints: readonly RequirementConstraint[] = request.priorConstraints ?? [],
  ): Promise<BrainPreparation> {
    const prepared = await super.prepare(request, priorConstraints);
    const {stats, matches} = this.dataset.retrieve(request.prompt, request.objective);
    const policy = operationalPolicyFor(request);
    const localGuidance = await this.localProcedureGuidance(request, policy.allowedTools);
    return {
      ...prepared,
      operational: {
        policy,
        // Só envia exemplos com origem verificável e formato aceito pelo contrato Python.
        workflowGuidance: [...matches.map((match) => match.guidance), ...localGuidance].slice(0, 3),
        dataset: {
          trustedRecords: stats.trustedRecords,
          rejectedRecords: stats.rejectedRecords,
          matchedRecords: matches.map((match) => match.id),
        },
      },
    };
  }

  private async localProcedureGuidance(request: BrainRequest, allowedTools: readonly string[]): Promise<string[]> {
    if (!this.workflowMemory) return [];
    let rows: Awaited<ReturnType<BrainMemory["search"]>>;
    try {
      rows = await this.workflowMemory.search("procedure " + request.objective + " " + request.prompt, 12);
    } catch {
      return [];
    }
    const match = rows.find((row) => row.key.toLowerCase() === "procedure:" + request.objective
      && row.confidence.score >= 0.75);
    if (!match) return [];
    try {
      const value: unknown = JSON.parse(match.value);
      if (!value || typeof value !== "object") return [];
      const record = value as Record<string, unknown>;
      if (record.schema !== "operational-procedure/v1" || record.objective !== request.objective
          || record.status !== "completed"
          || !["passed", "not_run"].includes(String(record.verification))
          || !Array.isArray(record.tools)) return [];
      const tools = record.tools.filter((tool): tool is string =>
        typeof tool === "string" && allowedTools.includes(tool)).slice(0, 8);
      const actions = tools.map((tool) => tool === "research_web" ? "search"
        : tool === "project_checks" || tool === "terminal_run" ? "terminal"
          : tool === "create_file" || tool === "edit_file" || tool === "apply_batch" || tool === "apply_repair" ? "edit"
            : tool === "ask_user" ? "ask_user" : "inspect");
      if (actions.length === 0) return [];
      return ["Trilha local observada: " + actions.join(" → ")
        + "; verificação: " + String(record.verification) + "."];
    } catch {
      return [];
    }
  }
}

/** Faz a memória persistente do agente cumprir a porta cognitiva do TypeScript. */
export class TaskMemoryBrainAdapter implements BrainMemory {
  private readonly memory: MemoryPort;

  constructor(memory: MemoryPort) {
    this.memory = memory;
  }

  async remember(entry: {
    kind: "working" | "semantic" | "episodic" | "procedural";
    key: string;
    value: string;
    source?: string;
    confidence: Confidence;
    taskId: string;
  }): Promise<void> {
    const kind = entry.kind === "working" ? "observation"
      : entry.kind === "episodic" ? "evidence"
        : entry.kind === "procedural" || entry.kind === "semantic" ? "decision" : "goal";
    await this.memory.remember({
      kind,
      key: entry.key,
      value: entry.value,
      source: entry.source,
      confidence: entry.confidence.score,
      taskId: entry.taskId,
    });
  }

  async search(query: string, limit = 8) {
    const entries = await this.memory.search(query, Math.max(1, Math.min(limit, 20)));
    return entries.filter((entry) => entry.kind === "decision" || entry.kind === "observation")
      .map((entry) => ({
        key: entry.key,
        value: entry.value,
        confidence: {
          score: entry.confidence ?? 0.5,
          basis: "retrieved" as const,
          reasons: entry.source ? ["Origem local: " + entry.source] : ["Recuperado da memória local."],
          calibrated: false,
        },
      }));
  }
}
