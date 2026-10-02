import {z} from "zod";
import {isAbsolute} from "node:path";
import type {
  AgentObjective,
  AgentContextV2,
  AgentPlannerCognition,
  PlannerDecision,
  PlannerMessage,
  PlannerPort,
  ProposedToolCall,
  RuntimeToolName,
} from "./contracts.ts";
import {validateWorkspaceRelativePath} from "./scope.ts";
import type {Fetcher} from "./runtime-http.ts";

const executableChecks = ["auto", "all", "cargo-test", "npm-test", "npm-check", "npm-build", "pytest", "unittest", "node-test", "go-test", "python-syntax", "node-syntax", "cpp-syntax", "c-syntax", "shell-syntax"] as const;
const checkName = z.enum(["list", ...executableChecks]);
const MAX_PLANNER_TEXT_CHARS = 12_000;
const terminalCheck = z.enum(executableChecks);
const safePath = z.string().min(1).max(1024);
const boundedText = z.string().max(131_072).refine(
  (content) => new TextEncoder().encode(content).byteLength <= 128 * 1024,
  "O conteúdo excede o limite de 128 KiB do runtime.",
);
const batchOperation = z.discriminatedUnion("tool", [
  z.object({tool: z.literal("create_file"), arguments: z.object({path: safePath, content: boundedText}).strict()}).strict(),
  z.object({tool: z.literal("edit_file"), arguments: z.object({path: safePath, old_text: z.string().min(1).max(128_000), new_text: z.string().max(128_000)}).strict()}).strict(),
  z.object({tool: z.literal("create_directory"), arguments: z.object({path: safePath}).strict()}).strict(),
]);

type PlannerToolName = Exclude<RuntimeToolName, "set_workspace">;

const argumentSchemas: Record<PlannerToolName, z.ZodType> = {
  create_workspace: z.object({path: z.string().min(1).max(1024)}).strict(),
  inspect_project: z.object({max_depth: z.number().int().min(1).max(6).optional()}).strict(),
  inspect_code: z.object({path: z.string().max(1024).optional()}).strict(),
  calculate: z.object({expression:z.string().min(1).max(8192),variables:z.record(z.string(),z.json()).optional()}).strict(),
  evaluate_function: z.object({path:safePath,function:z.string().min(1).max(120).regex(/^[A-Za-z_]\w*$/),
    args:z.array(z.json()).max(1000).optional(),kwargs:z.record(z.string(),z.json()).optional()}).strict(),
  list_tools: z.object({}).strict(),
  list_files: z.object({
    path: z.string().max(1024).optional(),
    include_hidden: z.boolean().optional(),
    max_entries: z.number().int().min(1).max(1000).optional(),
  }).strict(),
  path_info: z.object({path: z.string().max(1024)}).strict(),
  find_paths: z.object({
    pattern: z.string().min(1).max(120),
    path: z.string().max(1024).optional(),
    include_hidden: z.boolean().optional(),
    max_results: z.number().int().min(1).max(200).optional(),
  }).strict(),
  list_tree: z.object({
    path: z.string().max(1024).optional(),
    max_depth: z.number().int().min(1).max(6).optional(),
    max_entries: z.number().int().min(1).max(500).optional(),
    include_hidden: z.boolean().optional(),
  }).strict(),
  compare_files: z.object({left: safePath, right: safePath}).strict(),
  git_diff: z.object({}).strict(),
  read_file: z.object({
    path: safePath,
    offset: z.number().int().min(0).optional(),
    max_bytes: z.number().int().min(1).max(131_072).optional(),
    start_line: z.number().int().min(1).optional(),
    end_line: z.number().int().min(1).optional(),
  }).strict().refine(
    (args) => args.offset === undefined || (args.start_line === undefined && args.end_line === undefined),
    "offset não pode ser combinado com intervalo de linhas.",
  ),
  extract_document_text: z.object({
    path: safePath,
    max_chars: z.number().int().min(1).max(200_000).optional(),
  }).strict(),
  inspect_media: z.object({path: safePath}).strict(),
  search_files: z.object({
    query: z.string().min(1).max(1000),
    path: z.string().max(1024).optional(),
    max_results: z.number().int().min(1).max(100).optional(),
    context_lines: z.number().int().min(0).max(5).optional(),
  }).strict(),
  search_web: z.object({
    query: z.string().min(1).max(2000),
    source_id: z.string().max(160).optional(),
    freshness: z.enum(["pd", "pw", "pm", "py"]).optional(),
  }).strict(),
  open_page: z.object({
    url: z.string().url().max(2048).refine((value) => value.startsWith("http://") || value.startsWith("https://"),
      "A página precisa usar HTTP ou HTTPS."),
    source_id: z.string().max(160).optional(),
  }).strict(),
  list_sources: z.object({}).strict(),
  cite_sources: z.object({source_ids: z.array(z.string().min(1).max(160)).max(200)}).strict(),
  research_web: z.object({
    query: z.string().min(1).max(2000),
    topic: z.string().max(160).optional(),
    freshness: z.enum(["pd", "pw", "pm", "py"]).optional(),
    max_results: z.number().int().min(1).max(3).optional(),
    save_to_corpus: z.literal(false).optional(),
    category: z.string().max(80).optional(),
  }).strict(),
  project_checks: z.object({
    check: checkName.optional(),
    path: z.string().max(1024).optional(),
  }).strict(),
  diagnose_project: z.object({
    check: z.string().max(160).optional(),
    passed: z.boolean().optional(),
    executed: z.boolean().optional(),
    stdout: z.string().max(65_536).optional(),
    stderr: z.string().max(65_536).optional(),
  }).strict(),
  terminal_run: z.object({
    operation: z.enum(["git_status", "git_diff_stat", "project_check"]),
    check: terminalCheck.optional(),
  }).strict(),
  process_start: z.object({profile: z.literal("auto-dev")}).strict(),
  process_status: z.object({
    process_id: z.string().max(64).optional(),
    stdout_cursor: z.number().int().min(0).optional(),
    stderr_cursor: z.number().int().min(0).optional(),
    wait_ms: z.number().int().min(0).max(1500).optional(),
  }).strict(),
  process_stop: z.object({process_id: z.string().max(64).optional()}).strict(),
  apply_batch: z.object({operations: z.array(batchOperation).min(1).max(32)}).strict(),
  undo_batch: z.object({transaction_id: z.string().regex(/^batch-[a-fA-F0-9]{24}$/).optional()}).strict(),
  create_file: z.object({path: safePath, content: boundedText}).strict(),
  edit_file: z.object({
    path: safePath,
    old_text: z.string().min(1).max(128_000),
    new_text: z.string().max(128_000),
  }).strict(),
  propose_repair: z.object({
    path: safePath,
    old_text: z.string().min(1).max(128_000),
    new_text: z.string().max(128_000),
    reason: z.string().min(1).max(1000),
    check: terminalCheck.optional(),
  }).strict(),
  apply_repair: z.object({
    path: safePath,
    old_text: z.string().min(1).max(128_000),
    new_text: z.string().max(128_000),
    reason: z.string().min(1).max(1000),
    check: terminalCheck.optional(),
  }).strict(),
  create_directory: z.object({path: safePath}).strict(),
  create_web_page: z.object({
    prompt: z.string().min(1).max(12_000),
    path: z.string().max(1024).optional(),
    title: z.string().max(300).optional(),
  }).strict(),
};

const toolNames = Object.keys(argumentSchemas) as [PlannerToolName, ...PlannerToolName[]];

export function plannerToolSchemas() {
  return Object.fromEntries(Object.entries(argumentSchemas)
    .map(([name, schema]) => [name, z.toJSONSchema(schema)]));
}

const toolCallEnvelope = z.object({
  id: z.string().min(1).max(160),
  tool: z.enum(toolNames),
  arguments: z.record(z.string(), z.unknown()),
  reason: z.string().min(1).max(1000),
  requires_approval: z.boolean().optional(),
  risk: z.enum(["none", "low", "medium", "high", "critical", "read", "write"]).optional(),
}).passthrough();

const plannerEnvelope = z.object({
  ok: z.boolean().optional(),
  // Model backends may echo a long implementation summary after tool output.
  // Parse a bounded envelope, then cap the conversational text before it is
  // fed into the next turn (the runtime accepts at most 12k per message).
  text: z.string().max(65_536).optional(),
  tool_call: z.unknown().nullable().optional(),
  tool_calls: z.array(z.unknown()).optional(),
  backend: z.string().max(100).optional(),
  trace_id: z.string().max(160).optional(),
  agent: z.object({stop_reason: z.string().max(160).optional(), retryable: z.boolean().optional()}).passthrough().optional(),
  error: z.string().max(2000).optional(),
}).passthrough();

function mapRisk(tool: RuntimeToolName, claimed: string | undefined): ProposedToolCall["risk"] {
  if (["process_start", "apply_batch", "undo_batch", "create_workspace"].includes(tool)) return "high";
  if (tool === "process_stop") return "medium";
  if (["create_file", "create_web_page", "edit_file", "apply_repair", "create_directory", "project_checks", "terminal_run"].includes(tool)) {
    return "medium";
  }
  if (claimed === "critical" || claimed === "high" || claimed === "medium") return claimed;
  if (claimed === "write") return "medium";
  if (claimed === "none") return "none";
  return "low";
}

function validateProposal(value: unknown): ProposedToolCall {
  const call = toolCallEnvelope.parse(value);
  const parsedArguments = argumentSchemas[call.tool].parse(call.arguments) as Record<string, unknown>;
  if (call.tool === "create_workspace") {
    if (typeof parsedArguments.path !== "string" || !isAbsolute(parsedArguments.path)) {
      throw new Error("create_workspace precisa de um caminho absoluto.");
    }
  } else if (call.tool === "apply_batch") {
    const operations = parsedArguments.operations as Array<{tool: string; arguments: {path: string}}>;
    const paths = new Set<string>();
    for (const operation of operations) {
      const path = validateWorkspaceRelativePath(operation.arguments.path);
      if (paths.has(path)) throw new Error("O lote não pode alterar o mesmo caminho duas vezes.");
      paths.add(path);
      operation.arguments.path = path;
    }
  } else {
    for (const key of ["path", ...(call.tool === "compare_files" ? ["left", "right"] : [])]) {
      const path = parsedArguments[key];
      if (typeof path === "string" && path.length > 0) {
        parsedArguments[key] = validateWorkspaceRelativePath(path);
      }
    }
  }
  const risk = mapRisk(call.tool, call.risk);
  return {
    id: call.id,
    tool: call.tool,
    arguments: parsedArguments,
    reason: call.reason,
    requiresApproval: call.requires_approval === true || risk === "medium" || risk === "high" || risk === "critical",
    risk,
  };
}

/** Adaptador do planejador Python local; só recebe e devolve JSON, nunca executa ferramentas. */
export class LocalPlannerHttp implements PlannerPort {
  private readonly fetcher: Fetcher;
  private readonly baseUrl: string;

  constructor(options: {baseUrl?: string; fetcher?: Fetcher} = {}) {
    const configuredUrl = new URL(options.baseUrl ?? "http://127.0.0.1:3101");
    if (configuredUrl.protocol !== "http:"
        || !["127.0.0.1", "localhost", "[::1]"].includes(configuredUrl.hostname)
        || configuredUrl.username || configuredUrl.password) {
      throw new Error("O planejador TypeScript só pode se conectar a um serviço HTTP loopback local.");
    }
    this.baseUrl = configuredUrl.origin;
    this.fetcher = options.fetcher ?? (globalThis.fetch as unknown as Fetcher);
  }

  async plan(input: {
    messages: readonly PlannerMessage[];
    prompt: string;
    requestId: string;
    objective?: AgentObjective;
    workflowGuidance?: readonly string[];
    cognition?: AgentPlannerCognition;
    context?: AgentContextV2;
    onDelta?: (fragment: string) => void;
  }): Promise<PlannerDecision> {
    const typedPlan = Boolean(input.cognition || input.context);
    const endpoint = typedPlan
      ? (input.onDelta ? "/v1/agent/plan/stream" : "/v1/agent/plan")
      : (input.onDelta ? "/generate/stream" : "/generate");
    const response = await this.fetcher(this.baseUrl + endpoint, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({
        ...(typedPlan ? {schema: "agent-plan-request/v1"} : {}),
        messages: input.messages,
        request_id: input.requestId,
        ...(input.objective ? {objective: input.objective} : {}),
        ...(input.workflowGuidance?.length ? {workflow_guidance: input.workflowGuidance.slice(0, 3)} : {}),
        ...(input.cognition ? {cognition: {
          schema: input.cognition.schema,
          task_id: input.cognition.taskId,
          interpretation: input.cognition.interpretation,
          personality: input.cognition.personality,
          assumptions: input.cognition.assumptions,
          constraints: input.cognition.constraints,
          acceptance_criteria: input.cognition.acceptanceCriteria,
          available_tools: input.cognition.availableTools,
        }} : {}),
        ...(input.context ? {context: {
          schema: input.context.schema,
          status: input.context.status,
          query: input.context.query,
          intent: input.context.intent,
          topic: input.context.topic,
          personality_ref: input.context.personalityRef,
          history: input.context.history,
          conversation_memory: input.context.conversationMemory,
          session_memory: input.context.sessionMemory.map((item) => ({kind: item.key, text: item.value,
            source: item.source, confidence: item.confidence})),
          relevant_skills: input.context.relevantSkills,
          evidence: {schema: "agent-evidence/v1", status: input.context.evidence.length ? "found" : "no_evidence",
            items: input.context.evidence},
          limits: input.context.limits,
          instruction: input.context.instruction,
        }} : {}),
      }),
      signal: AbortSignal.timeout(120_000),
      redirect: "error",
    });
    let rawValue: unknown;
    if (input.onDelta) {
      if (!response.ok) {
        const error = await response.json() as {error?: string};
        throw new Error(error.error ?? "O planejador local respondeu com erro HTTP.");
      }
      const reader = response.body?.getReader();
      if (!reader) throw new Error("O planejador não disponibilizou o fluxo de resposta.");
      const decoder = new TextDecoder();
      let buffer = "";
      let streamedResponse: unknown;
      const dispatch = (block: string): void => {
        const data = block.split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (!data) return;
        const event = JSON.parse(data) as {type?: string; text?: string; response?: unknown; error?: string};
        if (event.type === "delta" && typeof event.text === "string") input.onDelta?.(event.text);
        else if (event.type === "done") streamedResponse = event.response;
        else if (event.type === "error") throw new Error(event.error ?? "A geração local não foi concluída.");
      };
      while (true) {
        const {value, done} = await reader.read();
        buffer += decoder.decode(value ?? new Uint8Array(), {stream: !done});
        let boundary: number;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          dispatch(buffer.slice(0, boundary));
          buffer = buffer.slice(boundary + 2);
        }
        if (done) break;
      }
      if (buffer.trim()) dispatch(buffer);
      if (streamedResponse === undefined) throw new Error("O fluxo do planejador terminou sem uma resposta final.");
      rawValue = streamedResponse;
    } else {
      rawValue = await response.json();
    }
    const raw = plannerEnvelope.parse(rawValue);
    if (!response.ok || raw.ok === false) {
      throw new Error(raw.error ?? "O planejador local respondeu com erro.");
    }
    const proposedCalls = raw.tool_calls ?? (raw.tool_call == null ? [] : [raw.tool_call]);
    const first = proposedCalls[0] == null ? null : validateProposal(proposedCalls[0]);
    const batchable = new Set<RuntimeToolName>(["create_file", "edit_file", "create_directory"]);
    const leadingWrites: ProposedToolCall[] = [];
    if (first && batchable.has(first.tool)) {
      for (const proposed of proposedCalls.slice(0, 32)) {
        const call = validateProposal(proposed);
        if (!batchable.has(call.tool)) break;
        leadingWrites.push(call);
      }
    }
    // O planejador pode propor vários arquivos. O runtime aplica esse prefixo
    // como um lote aprovado, enquanto checks posteriores são reavaliados pelo
    // núcleo após observar o resultado e executar sua verificação final.
    const toolCall = leadingWrites.length > 1
      ? validateProposal({
          id: (leadingWrites[0].id + "-batch").slice(0, 160),
          tool: "apply_batch",
          arguments: {operations: leadingWrites.map((call) => ({tool: call.tool, arguments: call.arguments}))},
          reason: `Aplicar ${leadingWrites.length} alterações propostas como lote revisável.`,
        })
      : first;
    return {
      text: (raw.text ?? "").slice(0, MAX_PLANNER_TEXT_CHARS),
      toolCall,
      backend: raw.backend,
      traceId: raw.trace_id,
      stopReason: raw.agent?.stop_reason,
      retryable: raw.agent?.retryable,
    };
  }
}
