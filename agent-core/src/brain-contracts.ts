/**
 * Contratos do cérebro operacional.
 *
 * Estes tipos são deliberadamente independentes do modelo neural. O modelo,
 * os índices, os adaptadores Python e o runtime Rust entram por portas
 * explícitas. O estado cognitivo, a proveniência e as permissões ficam sob
 * controle do núcleo TypeScript.
 */

export type BrainStateKind =
  | "observing"
  | "clarifying"
  | "planning"
  | "awaiting_approval"
  | "executing"
  | "verifying"
  | "recovering"
  | "delivering"
  | "abstaining"
  | "completed";

export type BrainObjective =
  | "conversation"
  | "research"
  | "analyze"
  | "build"
  | "debug"
  | "testing"
  | "learn"
  | "operate";

export type RiskLevel = "none" | "low" | "medium" | "high" | "critical";

export type EvidenceBasis =
  | "observed"
  | "retrieved"
  | "derived"
  | "inferred"
  | "user_confirmed"
  | "unknown";

export interface Confidence {
  score: number;
  basis: EvidenceBasis;
  reasons: readonly string[];
  calibrated: boolean;
}

export interface BrainState {
  version: 1;
  taskId: string;
  objective: BrainObjective;
  kind: BrainStateKind;
  startedAt: string;
  updatedAt: string;
  pendingQuestions: readonly string[];
  activePlanId?: string;
  activeActionId?: string;
  lastError?: string;
  confidence?: Confidence;
}

export interface BrainEvent {
  version: 1;
  id: string;
  seq: number;
  taskId: string;
  at: string;
  type: string;
  from: BrainStateKind;
  to: BrainStateKind;
  reason: string;
  payload?: Readonly<Record<string, unknown>>;
}

export interface RequirementConstraint {
  id: string;
  text: string;
  source: "user" | "workspace" | "policy" | "inferred";
  mandatory: boolean;
  confidence: Confidence;
}

export interface AcceptanceCriterion {
  id: string;
  text: string;
  verifiable: boolean;
  source: "user" | "derived";
}

export interface RequirementInterpretation {
  id: string;
  summary: string;
  assumptions: readonly string[];
  missing: readonly string[];
  plausibility: number;
}

export interface RequirementAnalysis {
  prompt: string;
  objective: BrainObjective;
  summary: string;
  interpretations: readonly RequirementInterpretation[];
  constraints: readonly RequirementConstraint[];
  acceptanceCriteria: readonly AcceptanceCriterion[];
  missingInformation: readonly string[];
  questions: readonly string[];
  ambiguityScore: number;
  requiresClarification: boolean;
  confidence: Confidence;
}

export interface BrainRequest {
  taskId: string;
  prompt: string;
  objective: BrainObjective;
  projectId?: string;
  workspaceRoot?: string;
  priorConstraints?: readonly RequirementConstraint[];
  knownCapabilities?: readonly string[];
  /** false calcula a preparação sem persistir evento ou memória; reservado à prévia understand. */
  persist?: boolean;
}

export interface BrainThinkingSummary {
  route: "conversation" | "workspace_read" | "workspace_write" | "web_research" | "learning" | "external_action";
  personalityMode: "conversation" | "creative" | "analysis" | "research" | "engineering" | "interface";
  personalityGuidance: readonly string[];
  interpretation: string;
  rationale: string;
  uncertainties: readonly string[];
  nextStep: string;
  confidence: Confidence;
}

export interface BrainPreparation {
  request: BrainRequest;
  analysis: RequirementAnalysis;
  thinking: BrainThinkingSummary;
  state: BrainState;
  events: readonly BrainEvent[];
  operational?: {
    policy: {
      mode: "conversation" | "read_only" | "mutating" | "operation";
      allowedTools: readonly string[];
      acceptance: readonly string[];
      maxActions: number;
    };
    workflowGuidance: readonly string[];
    dataset: {
      trustedRecords: number;
      rejectedRecords: number;
      matchedRecords: readonly string[];
    };
  };
}

export interface BrainSnapshot {
  version: 1;
  state: BrainState;
  events: readonly BrainEvent[];
}

export interface BrainClock {
  now(): string;
}

export interface BrainEventLog {
  append(event: BrainEvent): Promise<void>;
  read(taskId?: string): Promise<readonly BrainEvent[]>;
}

export interface BrainMemory {
  remember(entry: {
    kind: "working" | "semantic" | "episodic" | "procedural";
    key: string;
    value: string;
    source?: string;
    confidence: Confidence;
    taskId: string;
  }): Promise<void>;
  search(query: string, limit?: number): Promise<readonly {
    key: string;
    value: string;
    confidence: Confidence;
  }[]>;
}

export interface BrainApprovalRequest {
  taskId: string;
  actionId: string;
  tool: string;
  risk: RiskLevel;
  reason: string;
  reversible: boolean;
  expectedEffect: string;
}

export interface BrainApprovalPort {
  request(input: BrainApprovalRequest): Promise<boolean>;
}
