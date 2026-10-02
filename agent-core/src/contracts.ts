import type {BrainObjective, BrainPreparation, BrainSnapshot, RequirementAnalysis} from "./brain-contracts.ts";
import type {RuntimeToolName} from "./capability-registry.ts";
export type {RuntimeToolName} from "./capability-registry.ts";

export type AgentPhase = "observe" | "learn" | "plan" | "act" | "verify" | "complete";
export type AgentStatus = "running" | "completed" | "blocked";

export type AgentObjective = BrainObjective;
export type ProjectCheckName = "auto" | "all" | "cargo-test" | "npm-test" | "npm-check" | "npm-build"
  | "pytest" | "unittest" | "node-test" | "go-test" | "python-syntax" | "node-syntax" | "cpp-syntax" | "c-syntax" | "shell-syntax";

export interface AgentInput {
  prompt: string;
  objective: AgentObjective | "auto";
  workspaceRoot?: string;
  requestId?: string;
  conversationId?: string;
  preparationId?: string;
  preferences?: {language?: string; stack?: string | null};
  /** Anexos já normalizados pelo runtime; conteúdo é evidência, nunca instrução. */
  attachments?: readonly AgentAttachment[];
  /** Mensagens recentes usadas apenas para preservar a continuidade do chat. */
  history?: readonly PlannerMessage[];
}

export interface AgentAttachment {
  path: string;
  content: string;
  mediaType?: string;
  assetId?: string;
  kind?: "image" | "audio" | "video" | "document" | "text";
  analysisStatus?: string;
  warnings?: readonly string[];
  observation?: Record<string, unknown>;
}

export interface AgentResumeContext {
  /** Preserva a identidade e a cognição da tarefa ao retomar uma aprovação. */
  taskId?: string;
  brainSnapshot?: BrainSnapshot;
  brainPreparation?: BrainPreparation;
  inspection?: ProjectInspection;
  evidence: readonly Evidence[];
  /** Histórico enxuto do loop local, preservado durante a aprovação. */
  plannerMessages?: readonly PlannerMessage[];
  /** Ação exata que aguarda autorização; nunca é substituída por nova proposta. */
  pendingToolCall?: ProposedToolCall;
  artifacts?: readonly Artifact[];
  hasChanges?: boolean;
  verification?: Verification;
  /** Fatos de execução preservados, sem depender de mensagens que caibam na janela. */
  successfulTools?: readonly RuntimeToolName[];
  readPaths?: readonly string[];
  analysisContentPaths?: readonly string[];
  analysisReadLineEnds?: readonly [string, number][];
  completedCalls?: readonly {id: string; tool: RuntimeToolName; arguments: Record<string, unknown>; ok: boolean}[];
}

export interface TaskCheckpoint {
  schema: "agent-checkpoint/v1";
  taskId: string;
  updatedAt: string;
  request: AgentInput;
  phase: "planning" | "executing" | "observed";
  /** Uma ação em voo não tem seu resultado confirmado; não pode ser reaplicada às cegas. */
  inFlight?: ProposedToolCall;
  resume: AgentResumeContext;
  workingState: Record<string, unknown>;
}

export interface CheckpointPort {
  save(checkpoint: TaskCheckpoint): Promise<void>;
}

/** Ferramentas que o núcleo pode solicitar ao runtime Rust. */
export interface RuntimeToolArguments {
  set_workspace: {path: string};
  create_workspace: {path: string};
  inspect_project: {max_depth?: number};
  inspect_code: {path?: string};
  calculate: {expression: string; variables?: Record<string, unknown>};
  evaluate_function: {path: string; function: string; args?: unknown[]; kwargs?: Record<string, unknown>};
  list_tools: Record<string, never>;
  list_files: {path?: string};
  path_info: {path: string};
  find_paths: {pattern: string; path?: string; include_hidden?: boolean; max_results?: number};
  list_tree: {path?: string; max_depth?: number; max_entries?: number; include_hidden?: boolean};
  compare_files: {left: string; right: string};
  git_diff: Record<string, never>;
  read_file: {path: string; offset?: number; max_bytes?: number; start_line?: number; end_line?: number};
  extract_document_text: {path: string; max_chars?: number};
  inspect_media: {path: string};
  search_files: {query: string; max_results?: number; context_lines?: number};
  search_web: {query: string; source_id?: string; freshness?: "pd" | "pw" | "pm" | "py"};
  open_page: {url: string; source_id?: string};
  list_sources: Record<string, never>;
  cite_sources: {source_ids: string[]};
  research_web: {
    query: string;
    topic?: string;
    freshness?: "pd" | "pw" | "pm" | "py";
    max_results?: number;
    save_to_corpus?: false;
    category?: string;
  };
  project_checks: {check?: ProjectCheckName | "list"; path?: string};
  diagnose_project: {check?: string; passed?: boolean; executed?: boolean; stdout?: string; stderr?: string};
  terminal_run: {operation: "git_status" | "git_diff_stat" | "project_check"; check?: ProjectCheckName};
  process_start: {profile: "auto-dev"};
  process_status: {process_id?: string; stdout_cursor?: number; stderr_cursor?: number; wait_ms?: number};
  process_stop: {process_id?: string};
  apply_batch: {operations: Array<
    | {tool: "create_file"; arguments: {path: string; content: string}}
    | {tool: "edit_file"; arguments: {path: string; old_text: string; new_text: string}}
    | {tool: "create_directory"; arguments: {path: string}}
  >};
  undo_batch: {transaction_id?: string};
  create_file: {path: string; content: string};
  edit_file: {path: string; old_text: string; new_text: string};
  propose_repair: {path: string; old_text: string; new_text: string; reason: string; check?: string};
  apply_repair: {path: string; old_text: string; new_text: string; reason: string; check?: string};
  create_directory: {path: string};
  create_web_page: {prompt: string; path?: string; title?: string};
}

export type RuntimeToolCall<Name extends RuntimeToolName = RuntimeToolName> = {
  tool: Name;
  arguments: RuntimeToolArguments[Name];
};

export interface RuntimeToolResponse {
  ok: boolean;
  tool: RuntimeToolName;
  data?: unknown;
  error?: string;
}

export interface RuntimeToolsPort {
  call(tool: RuntimeToolName, arguments_: Record<string, unknown>, signal?: AbortSignal): Promise<RuntimeToolResponse>;
  /** Inventário observado no runtime ativo; ausente apenas em portas legadas/de teste. */
  listAvailable?(): Promise<readonly RuntimeToolName[]>;
}

export interface PlannerMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool?: string;
}

export interface ProposedToolCall {
  id: string;
  tool: RuntimeToolName;
  arguments: Record<string, unknown>;
  reason: string;
  requiresApproval: boolean;
  risk: "none" | "low" | "medium" | "high" | "critical";
}

export interface PlannerDecision {
  text: string;
  toolCall: ProposedToolCall | null;
  backend?: string;
  traceId?: string;
  stopReason?: string;
  retryable?: boolean;
}

export interface PlannerPort {
  plan(input: {
    messages: readonly PlannerMessage[];
    prompt: string;
    requestId: string;
    objective?: AgentObjective;
    workflowGuidance?: readonly string[];
    cognition?: AgentPlannerCognition;
    context?: AgentContextV2;
    onDelta?: (fragment: string) => void;
  }): Promise<PlannerDecision>;
}

export interface AgentPlannerCognition {
  schema: "agent-cognition/v1";
  taskId: string;
  interpretation: string;
  personality: {mode: string; version: "local-personality/v1"};
  assumptions: readonly string[];
  constraints: readonly {text: string; source: string; mandatory: boolean}[];
  acceptanceCriteria: readonly string[];
  availableTools: readonly string[];
}

export interface AgentContextV2 {
  schema: "agent-context/v2";
  status: "ready";
  query: string;
  intent: string;
  topic: string | null;
  personalityRef: "local-personality/v1";
  history: readonly {role: "user" | "assistant"; content: string}[];
  conversationMemory: string;
  sessionMemory: readonly {key: string; value: string; source: string; confidence: number}[];
  relevantSkills: readonly {topic: string; source: string; confidence: number}[];
  evidence: readonly {title: string; excerpt: string; source: string; confidence: number}[];
  limits: Record<string, unknown>;
  instruction: string;
}

export interface ContextPort {
  build(input: {
    query: string;
    messages: readonly PlannerMessage[];
    conversationId?: string;
    preferences?: AgentInput["preferences"];
  }): Promise<AgentContextV2>;
}

export interface ProjectInspection {
  workspace: string;
  files: readonly string[];
  directories?: readonly string[];
  truncated?: boolean;
  manifests: readonly string[];
  testFiles: readonly string[];
  entrypoints: readonly string[];
}

export interface Evidence {
  title: string;
  url: string;
  excerpt: string;
  sourceId?: string;
}

export interface Artifact {
  path: string;
  content: string;
  language: string;
}

export interface Verification {
  kind?: "syntax" | "test" | "build" | "typecheck" | "combined";
  testsExecuted?: number;
  behaviorExecuted?: boolean;
  check?: string;
  stdout?: string;
  stderr?: string;
  passed: boolean;
  executed: boolean;
  summary: string;
  evidence: readonly string[];
}

export interface PlanStep {
  id: string;
  phase: AgentPhase;
  description: string;
  requiresApproval: boolean;
}

export interface AgentPlan {
  objective: string;
  steps: readonly PlanStep[];
}

export interface AgentEvent {
  seq: number;
  taskId: string;
  phase: AgentPhase;
  status: AgentStatus;
  kind: string;
  title: string;
  detail?: string;
  payload?: Record<string, unknown>;
  /** UI-only progress; it must not enter the persisted task history. */
  transient?: boolean;
}

export interface WorkspacePort {
  select(root?: string): Promise<string>;
  inspect(): Promise<ProjectInspection>;
  write(artifact: Artifact): Promise<Artifact>;
  verify(): Promise<Verification>;
}

export interface ResearchPort {
  research(query: string): Promise<readonly Evidence[]>;
}

export interface LearningPort {
  remember(topic: string, evidence: readonly Evidence[]): Promise<void>;
}

export interface ApprovalRequest {
  action: "write";
  path: string;
  reason: string;
}

export interface ApprovalPort {
  request(input: ApprovalRequest): Promise<boolean>;
}

export interface EventPort {
  publish(event: AgentEvent): void;
}

export type MemoryKind = "goal" | "workspace" | "observation" | "evidence" | "decision";

export interface MemoryInput {
  kind: MemoryKind;
  key: string;
  value: string;
  source?: string;
  confidence?: number;
  taskId?: string;
}

export interface MemoryEntry extends MemoryInput {
  id: string;
  createdAt: string;
  updatedAt: string;
}

export interface MemoryPort {
  remember(input: MemoryInput): Promise<MemoryEntry>;
  search(query: string, limit?: number): Promise<readonly MemoryEntry[]>;
}

export interface AgentPorts {
  workspace: WorkspacePort;
  research: ResearchPort;
  planner?: PlannerPort;
  context?: ContextPort;
  checkpoints?: CheckpointPort;
  tools?: RuntimeToolsPort;
  learning?: LearningPort;
  approval?: ApprovalPort;
  events?: EventPort;
  memory?: MemoryPort;
}

export interface AgentReport {
  taskId: string;
  status: AgentStatus;
  requirements?: RequirementAnalysis;
  inspection?: ProjectInspection;
  plan?: AgentPlan;
  evidence: readonly Evidence[];
  artifacts: readonly Artifact[];
  verification?: Verification;
  events: readonly AgentEvent[];
  finalText?: string;
  /** Estado interno para retomada; o servidor não o envia à interface. */
  continuation?: AgentResumeContext;
  error?: string;
}
