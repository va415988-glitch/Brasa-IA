import {readFileSync} from "node:fs";
import {join} from "node:path";
import type {BrainObjective} from "./brain-contracts.ts";

const actionKinds = new Set(["inspect", "search", "terminal", "edit", "ask_user", "respond"]);
const taskTypes = new Set([
  "repo_navigation", "code_edit", "debug", "testing", "explanation", "clarification", "research", "learning", "operation",
]);

interface CuratedWorkflow {
  id: string;
  taskType: string;
  request: string;
  actions: readonly string[];
  verification: "passed" | "failed" | "not_run";
}

export interface WorkflowDatasetStats {
  trustedRecords: number;
  rejectedRecords: number;
}

export interface WorkflowMatch extends CuratedWorkflow {
  guidance: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

/**
 * Lê somente trajetórias curadas, revisadas por pessoa e explicitamente
 * liberadas para treino. Linhas raw, exemplos não revisados e partições test
 * nunca influenciam o roteamento operacional.
 */
export class CuratedWorkflowDataset {
  private readonly filePath: string;

  constructor(filePath = join(process.cwd(), "datasets", "agent_workflow_v1", "curated", "examples.jsonl")) {
    this.filePath = filePath;
  }

  retrieve(query: string, objective: BrainObjective, limit = 2): {
    stats: WorkflowDatasetStats;
    matches: readonly WorkflowMatch[];
  } {
    let raw: string;
    try {
      raw = readFileSync(this.filePath, "utf8");
    } catch {
      return {stats: {trustedRecords: 0, rejectedRecords: 0}, matches: []};
    }
    if (Buffer.byteLength(raw, "utf8") > 2 * 1024 * 1024) {
      return {stats: {trustedRecords: 0, rejectedRecords: 1}, matches: []};
    }

    const records: CuratedWorkflow[] = [];
    let rejectedRecords = 0;
    for (const line of raw.split(/\r?\n/)) {
      if (!line.trim()) continue;
      try {
        const row: unknown = JSON.parse(line);
        const record = this.validate(row);
        if (record) records.push(record);
        else rejectedRecords += 1;
      } catch {
        rejectedRecords += 1;
      }
    }

    const queryTokens = new Set(this.tokens(query));
    const objectiveTypes = this.taskTypesFor(objective);
    const matches = records.map((record) => {
      const requestTokens = new Set(this.tokens(record.request));
      let overlap = 0;
      for (const token of queryTokens) if (requestTokens.has(token)) overlap += 1;
      const lexicalScore = overlap / Math.max(1, Math.sqrt(queryTokens.size * requestTokens.size));
      const taskTypeScore = objectiveTypes.has(record.taskType) ? 0.35 : 0;
      return {record, score: lexicalScore + taskTypeScore, overlap};
    }).filter((item) => item.overlap > 0 || objectiveTypes.has(item.record.taskType))
      .sort((left, right) => right.score - left.score || left.record.id.localeCompare(right.record.id))
      .slice(0, Math.max(1, Math.min(limit, 3)))
      .map(({record, score}) => ({
        ...record,
        guidance: "Fluxo de referência revisado: " + record.actions.join(" → ")
          + "; verificação: " + record.verification + ".",
        score,
      }))
      .filter((item) => item.score >= 0.2);

    return {stats: {trustedRecords: records.length, rejectedRecords}, matches};
  }

  private validate(value: unknown): CuratedWorkflow | undefined {
    if (!isRecord(value) || value.schema_version !== "1.0" || typeof value.id !== "string"
        || typeof value.task_type !== "string" || !taskTypes.has(value.task_type)
        || typeof value.request !== "string" || !value.request.trim()
        || typeof value.language !== "string"
        || !isRecord(value.context)
        || typeof value.final_response !== "string"
        || !["train", "validation"].includes(String(value.split))) return undefined;
    const source = isRecord(value.source) ? value.source : undefined;
    if (!source || !["human_authored", "observed", "synthetic"].includes(String(source.kind))
        || typeof source.reference !== "string") return undefined;
    const quality = isRecord(value.quality) ? value.quality : undefined;
    if (quality?.human_reviewed !== true || quality.safe_to_train !== true) return undefined;
    const verificationRow = isRecord(value.verification) ? value.verification : undefined;
    const verification = verificationRow?.status;
    if (verification !== "passed" && verification !== "failed" && verification !== "not_run") return undefined;
    if (!Array.isArray(verificationRow?.checks) || verificationRow.checks.some((item) => typeof item !== "string")) return undefined;
    if (!Array.isArray(value.steps) || value.steps.length < 1 || value.steps.length > 32) return undefined;
    const actions: string[] = [];
    for (const step of value.steps) {
      if (!isRecord(step) || typeof step.state !== "string" || typeof step.observation !== "string"
          || !isRecord(step.action) || typeof step.action.kind !== "string"
          || !actionKinds.has(step.action.kind)) return undefined;
      if (step.action.tool !== undefined && step.action.tool !== null && typeof step.action.tool !== "string") return undefined;
      if (step.action.arguments !== undefined && !isRecord(step.action.arguments)) return undefined;
      actions.push(step.action.kind);
    }
    return {
      id: value.id.slice(0, 160),
      taskType: value.task_type,
      request: value.request.slice(0, 4000),
      actions,
      verification,
    };
  }

  private taskTypesFor(objective: BrainObjective): Set<string> {
    const mapping: Record<BrainObjective, readonly string[]> = {
      conversation: ["explanation", "clarification"],
      research: ["research", "explanation", "repo_navigation"],
      analyze: ["repo_navigation", "explanation"],
      build: ["code_edit"],
      debug: ["debug", "code_edit"],
      testing: ["testing", "debug"],
      learn: ["learning", "explanation", "clarification"],
      operate: ["operation", "repo_navigation", "testing"],
    };
    return new Set(mapping[objective]);
  }

  private tokens(value: string): string[] {
    return value.normalize("NFKD").toLowerCase().replace(/[\u0300-\u036f]/g, "")
      .split(/[^\p{L}\p{N}_]+/u).filter((token) => token.length > 2);
  }
}
