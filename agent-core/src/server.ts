import {taskWorkflow} from "./workflow.ts";
import {createServer, type IncomingMessage, type ServerResponse} from "node:http";
import {join} from "node:path";
import {
  AgentCore,
  FileTaskMemory,
  JsonlBrainEventLog,
  RuntimeHttpPorts,
  LocalPlannerHttp,
  OperationalBrain,
  TaskMemoryBrainAdapter,
} from "./index.ts";
import type {AgentContextV2, AgentEvent, AgentPorts, AgentResumeContext, Evidence, ProjectInspection} from "./contracts.ts";
import {checkpointResume, continuationRequested, compatibleTaskContinuation} from "./task-continuity.ts";
import type {BrainPreparation} from "./brain-contracts.ts";
import {parseAgentInput, parseAgentResumeInput, parseTaskRouteRequest, type AgentServerInput} from "./server-contract.ts";
import {routeTask, taskRouteResponse} from "./task-router.ts";
import {FileTaskRunStore, type PersistedTaskRequest} from "./task-store.ts";
import {capabilityFor} from "./capability-registry.ts";
import type {RuntimeToolName} from "./contracts.ts";
import {LocalContextHttp} from "./context.ts";
import {analyzeRequirements, reportedProjectFailure} from "./requirements.ts";

const runtimeBaseUrl = process.env.IA_AGENT_RUNTIME_URL ?? "http://127.0.0.1:3000";
const defaultPort = Number(process.env.IA_AGENT_PORT ?? 3200);
const maxBodyBytes = 1024 * 1024;
const memoryPath = process.env.IA_AGENT_MEMORY_FILE ?? join(process.cwd(), ".agent-state", "memory.json");
const brainEventPath = process.env.IA_AGENT_BRAIN_EVENTS ?? join(process.cwd(), ".agent-state", "brain-events.jsonl");
const taskRunStore = new FileTaskRunStore(process.env.IA_AGENT_TASKS_DIR ?? join(process.cwd(), ".agent-state", "tasks"));
const memory = new FileTaskMemory(memoryPath, 2000);
const brain = new OperationalBrain({
  eventLog: new JsonlBrainEventLog(brainEventPath),
  memory: new TaskMemoryBrainAdapter(memory),
});
const approvalState = {approved: false};
type PendingApproval = {
  prompt: string;
  objective: string;
  workspaceRoot: string;
  inspection: ProjectInspection;
  evidence: readonly Evidence[];
  continuation: AgentResumeContext;
  input?: AgentServerInput;
};
type PendingClarification = {taskId: string; input: AgentServerInput; preparation: BrainPreparation};
type PreparedRequest = {signature: string; input: AgentServerInput; preparation: BrainPreparation; context?: AgentContextV2; inspection?: ProjectInspection; understanding: Record<string, unknown>; expiresAt: number};

/**
 * A concrete failure report is already an actionable specification. It must
 * enter inspection/debugging even when an older client or cached preparation
 * still marks generic fields as missing.
 */
function isProactiveDiagnosticRequest(input: AgentServerInput, prepared: PreparedRequest): boolean {
  return prepared.preparation.analysis.objective === "debug"
    || reportedProjectFailure(input.prompt)
    || /^\s*(?:revise|revisar|analise|analisar|inspecione|inspecionar|depure|depurar)\b/i.test(input.prompt);
}
function isPendingApproval(value: unknown): value is PendingApproval {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  const inspection = row.inspection && typeof row.inspection === "object"
    ? row.inspection as Record<string, unknown> : undefined;
  const continuation = row.continuation && typeof row.continuation === "object"
    ? row.continuation as Record<string, unknown> : undefined;
  const pendingCall = continuation?.pendingToolCall && typeof continuation.pendingToolCall === "object"
    ? continuation.pendingToolCall as Record<string, unknown> : undefined;
  let pendingCapabilityIsValid = false;
  if (pendingCall && typeof pendingCall.tool === "string") {
    try {
      pendingCapabilityIsValid = capabilityFor(pendingCall.tool as RuntimeToolName).risk === pendingCall.risk;
    } catch {
      pendingCapabilityIsValid = false;
    }
  }
  return typeof row.prompt === "string" && typeof row.objective === "string"
    && typeof row.workspaceRoot === "string" && Array.isArray(row.evidence)
    && Boolean(inspection && typeof inspection.workspace === "string"
      && Array.isArray(inspection.files) && Array.isArray(inspection.manifests)
      && Array.isArray(inspection.testFiles) && Array.isArray(inspection.entrypoints))
    && Boolean(continuation && typeof continuation.taskId === "string"
      && pendingCall && typeof pendingCall.id === "string" && Boolean(pendingCall.id.trim())
      && pendingCapabilityIsValid && typeof pendingCall.reason === "string"
      && typeof pendingCall.requiresApproval === "boolean"
      && pendingCall.arguments && typeof pendingCall.arguments === "object"
      && !Array.isArray(pendingCall.arguments))
    && (row.input === undefined || (() => {
      try { parseAgentInput(row.input); return true; } catch { return false; }
    })());
}
let pendingApproval: PendingApproval | null = null;
const pendingClarifications = new Map<string, PendingClarification>();
const preparations = new Map<string, PreparedRequest>();
let activeResumeContext: PendingApproval | null = null;
let activeOperationId = "";
let eventPublishQueue: Promise<void> = Promise.resolve();
let taskStoreQueue: Promise<void> = Promise.resolve();
let activeStoreRequest: PersistedTaskRequest | null = null;
let taskStoreError = "";
let eventFeedAvailable = true;
const adapter = new RuntimeHttpPorts({
  baseUrl: runtimeBaseUrl,
  workspaceRoot: process.cwd(),
}, {
  request: async () => {
    const approved = approvalState.approved;
    approvalState.approved = false;
    return approved;
  },
});
const workspace = {
  select: adapter.workspace.select,
  inspect: async () => activeResumeContext?.inspection ?? adapter.workspace.inspect(),
  write: adapter.workspace.write,
  verify: adapter.workspace.verify,
};
const research = {
  research: async (query: string) => activeResumeContext?.evidence ?? adapter.research.research(query),
};
const ports: AgentPorts = {
  workspace,
  research,
  planner: new LocalPlannerHttp({
    baseUrl: process.env.IA_LOCAL_PLANNER_URL ?? "http://127.0.0.1:3101",
  }),
  context: new LocalContextHttp({baseUrl: process.env.IA_LOCAL_CONTEXT_URL ?? "http://127.0.0.1:3101"}),
  tools: adapter.tools,
  checkpoints: {save: async (checkpoint) => {
    await taskStoreQueue;
    if (taskStoreError) throw new Error("Não consegui preservar a tarefa antes da ação: " + taskStoreError);
    // Core sees attachments in its planning prompt. Store the client prompt
    // separately so resuming does not append those attachments a second time.
    await taskRunStore.saveCheckpoint({...checkpoint, request: {...checkpoint.request,
      prompt: activeStoreRequest?.prompt ?? checkpoint.request.prompt}});
  }},
  approval: adapter.approval,
  memory,
  events: {
    publish(event: AgentEvent) {
      const operation = activeOperationId;
      if (!event.transient) {
        taskStoreQueue = taskStoreQueue.then(async () => {
          if (event.kind === "task.started") {
            const request = activeStoreRequest ?? {
              prompt: "", objective: "auto", operationId: operation || "agent-core-recovered",
            };
            await taskRunStore.begin(event.taskId, request);
          }
          await taskRunStore.appendEvent(event);
        }).catch((error) => {
          taskStoreError = error instanceof Error ? error.message : String(error);
          console.error("Não foi possível persistir evento da tarefa:", error);
        });
      }
      if (!operation || !eventFeedAvailable) return;
      // Delta is an encoded payload: cutting it can corrupt escapes and lose text.
      const detail = event.kind === "task.started" ? "" : event.kind === "assistant.stream.delta"
        ? (event.detail ?? "") : (event.detail ?? "").slice(0, 500);
      const message = detail ? `${event.title} · ${detail}` : event.title;
      const phase = event.phase;
      const status = event.status;
      eventPublishQueue = eventPublishQueue.then(async () => {
        const response = await fetch(new URL("/api/agent-events", runtimeBaseUrl), {
          method: "POST",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({operation, phase, status, message}),
          signal: AbortSignal.timeout(750),
        });
        if (!response.ok) throw new Error(`runtime respondeu HTTP ${response.status}`);
      }).catch((error) => {
        eventFeedAvailable = false;
        console.error("Não foi possível publicar evento do AgentCore:", error);
      });
    },
  },
};
const core = new AgentCore(ports, {brain, enforceRequirementsGate: false});
let activeTask = false;
let activePreparation = false;

async function readJson(request: IncomingMessage): Promise<unknown> {
  let size = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    const buffer = Buffer.from(chunk);
    size += buffer.length;
    if (size > maxBodyBytes) throw new Error("Pedido do agente acima de 1 MiB.");
    chunks.push(buffer);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new Error("JSON inválido.");
  }
}

function send(response: ServerResponse, status: number, payload: unknown): void {
  const body = JSON.stringify(payload);
  response.statusCode = status;
  response.setHeader("content-type", "application/json; charset=utf-8");
  response.setHeader("cache-control", "no-store");
  response.end(body);
}

function parsePendingClarification(value: unknown, taskId: string): PendingClarification | undefined {
  if (!value || typeof value !== "object") return undefined;
  const envelope = value as Record<string, unknown>;
  if (envelope.schema !== "agent-pending-control/v1" || envelope.kind !== "clarification"
      || !envelope.value || typeof envelope.value !== "object") return undefined;
  const row = envelope.value as Record<string, unknown>;
  const preparation = row.preparation as BrainPreparation | undefined;
  if (row.taskId !== taskId || !preparation || typeof preparation !== "object"
      || preparation.request?.taskId !== taskId || preparation.state?.kind !== "clarifying"
      || !Array.isArray(preparation.analysis?.questions) || preparation.analysis.questions.length < 1
      || preparation.analysis.questions.length > 3 || !preparation.analysis.requiresClarification) return undefined;
  try {
    const input = parseAgentInput(row.input, {allowMissingWorkspace: true});
    return {taskId, input, preparation};
  } catch {
    return undefined;
  }
}

function parsePendingApprovalControl(value: unknown, taskId: string): PendingApproval | undefined {
  if (!value || typeof value !== "object") return undefined;
  const envelope = value as Record<string, unknown>;
  if (envelope.schema !== "agent-pending-control/v1" || envelope.kind !== "approval"
      || !isPendingApproval(envelope.value)) return undefined;
  const pending = envelope.value;
  return pending.continuation.taskId === taskId && pending.input ? pending : undefined;
}

async function inputForApproval(pending: PendingApproval, taskId: string): Promise<AgentServerInput> {
  if (pending.input) return parseAgentInput(pending.input);
  const saved = await taskRunStore.read(taskId);
  return parseAgentInput({prompt: pending.prompt, objective: pending.objective,
    workspace_root: pending.workspaceRoot, operation_id: saved.task.request.operationId,
    history: saved.task.request.history ?? []});
}

function requestSignature(input: AgentServerInput): string {
  return JSON.stringify({prompt: input.prompt, objective: input.objective, workspaceRoot: input.workspaceRoot ?? "",
    conversationId: input.conversationId ?? "", history: input.history ?? [], attachments: input.attachments ?? [],
    preferences: input.preferences ?? {}});
}

function rememberClarification(pending: PendingClarification): void {
  pendingClarifications.set(pending.taskId, pending);
  while (pendingClarifications.size > 32) pendingClarifications.delete(pendingClarifications.keys().next().value!);
}

function agentInputWithAttachments(input: AgentServerInput): AgentServerInput {
  // Attachments enter plannerHistory as observations, preserving the original
  // user intent instead of letting their text become part of the command.
  return input;
}

async function resolveAttachmentEvidence(input: AgentServerInput): Promise<AgentServerInput> {
  const attachments = [];
  for (const item of input.attachments ?? []) {
    if (!item.assetId) { attachments.push(item); continue; }
    const response = await fetch(runtimeBaseUrl + "/api/v1/attachments/" + item.assetId + "/evidence",
      {signal: AbortSignal.timeout(5000)});
    const payload = await response.json() as {ok?: boolean; evidence?: unknown; error?: string};
    if (!response.ok || !payload.ok || !payload.evidence) {
      throw new Error("Não consegui recuperar o conteúdo observado do anexo: " + (payload.error ?? item.path));
    }
    const verified = parseAgentInput({...input, attachments: [payload.evidence]}).attachments?.[0];
    if (!verified || verified.assetId !== item.assetId) throw new Error("Origem do anexo inconsistente.");
    attachments.push(verified);
  }
  return {...input, ...(attachments.length ? {attachments} : {})};
}

async function prepare(input: AgentServerInput): Promise<PreparedRequest> {
  for (const [id, row] of preparations) if (row.expiresAt <= Date.now()) preparations.delete(id);
  const result = await core.understand(agentInputWithAttachments(input));
  const understanding = result.response;
  const preparationId = String(understanding.preparation_id);
  const entry: PreparedRequest = {
    signature: requestSignature(input), input, preparation: result.preparation, context: result.context,
    inspection: result.inspection,
    understanding, expiresAt: Date.now() + 15 * 60_000,
  };
  preparations.set(preparationId, entry);
  while (preparations.size > 32) preparations.delete(preparations.keys().next().value!);
  return entry;
}

async function guardedPrepare(input: AgentServerInput): Promise<PreparedRequest> {
  if (activeTask || activePreparation) {
    const error = new Error("já existe uma tarefa ou preparação do AgentCore em execução; aguarde a conclusão");
    error.name = "ConflictError";
    throw error;
  }
  activePreparation = true;
  try { return await prepare(input); }
  finally { activePreparation = false; }
}

async function guardedUnderstand(input: AgentServerInput, preparation: BrainPreparation) {
  if (activeTask || activePreparation) {
    const error = new Error("já existe uma tarefa ou preparação do AgentCore em execução; aguarde a conclusão");
    error.name = "ConflictError";
    throw error;
  }
  activePreparation = true;
  try { return await core.understand(agentInputWithAttachments(input), preparation); }
  finally { activePreparation = false; }
}

async function persistClarification(taskId: string, input: AgentServerInput, understanding: Record<string, unknown>): Promise<void> {
  await taskRunStore.begin(taskId, {
    prompt: input.prompt,
    objective: input.objective,
    workspaceRoot: input.workspaceRoot,
    operationId: input.operationId,
    history: input.history,
    requestId: input.requestId,
    conversationId: input.conversationId,
    preferences: input.preferences,
    attachments: input.attachments,
  }, "clarifying");
  await taskRunStore.saveClarificationState(taskId, understanding);
  const questions = Array.isArray(understanding.questions)
    ? understanding.questions.filter((question): question is string => typeof question === "string") : [];
  await taskRunStore.appendEvent({seq: 0, taskId, phase: "observe", status: "running",
    kind: "requirements.clarification.required", title: "Aguardando esclarecimento",
    detail: questions.join(" ").slice(0, 1000), payload: {questions}});
}

async function saveClarification(input: AgentServerInput, prepared: PreparedRequest): Promise<void> {
  const taskId = String(prepared.understanding.task_id);
  const pending: PendingClarification = {taskId, input, preparation: prepared.preparation};
  rememberClarification(pending);
  await persistClarification(taskId, input, prepared.understanding);
  await taskRunStore.savePendingTaskControl(taskId, {schema: "agent-pending-control/v1", kind: "clarification", value: pending});
}

async function understand(value: unknown): Promise<unknown> {
  const input = await resolveAttachmentEvidence(parseAgentInput(value, {allowMissingWorkspace: true}));
  const prepared = await guardedPrepare(input);
  if (prepared.understanding.status === "clarifying" && !isProactiveDiagnosticRequest(input, prepared)) {
    const taskId = String(prepared.understanding.task_id);
    rememberClarification({taskId, input, preparation: prepared.preparation});
  }
  return {ok: true, understanding: prepared.understanding};
}

async function pursue(value: unknown): Promise<unknown> {
  const input = await resolveAttachmentEvidence(parseAgentInput(value));
  if (input.objective === "auto" && continuationRequested(input.prompt) && input.conversationId) {
    const task = await taskRunStore.resumable(input.conversationId, input.workspaceRoot);
    if (task && compatibleTaskContinuation(input, task.request)) {
      return continueStoredTask(task.taskId, input.requestId ?? "continue-" + crypto.randomUUID(), input);
    }
  }
  if (activePreparation) {
    const error = new Error("já existe uma preparação do AgentCore em execução; aguarde a conclusão");
    error.name = "ConflictError";
    throw error;
  }
  if (input.approved === true) {
    if (!pendingApproval || pendingApproval.prompt !== input.prompt
        || pendingApproval.objective !== input.objective
        || pendingApproval.workspaceRoot !== (input.workspaceRoot ?? "")) {
      throw new Error("Não há uma aprovação pendente compatível para retomar.");
    }
    return executePursuit(input, pendingApproval);
  }
  let prepared: PreparedRequest | undefined;
  const candidate = input.preparationId ? preparations.get(input.preparationId) : undefined;
  if (candidate && candidate.expiresAt > Date.now() && candidate.signature === requestSignature(input)) {
    prepared = candidate;
    preparations.delete(input.preparationId!);
  } else {
    prepared = await guardedPrepare(input);
  }
  if (prepared.understanding.status === "clarifying" && !isProactiveDiagnosticRequest(input, prepared)) {
    await saveClarification(input, prepared);
    return {ok: true, awaitingClarification: true, understanding: prepared.understanding,
      preparation: {id: prepared.understanding.preparation_id, reused: Boolean(candidate && candidate === prepared)}};
  }
  return executePursuit(input, null, prepared.preparation, prepared.context, prepared.inspection, prepared.understanding,
    Boolean(candidate && candidate === prepared));
}

async function executePursuit(
  input: AgentServerInput,
  resumeApproval: PendingApproval | null,
  preparation?: BrainPreparation,
  preparedContext?: AgentContextV2,
  preparedInspection?: ProjectInspection,
  cognition?: Record<string, unknown>,
  preparationReused = false,
  checkpointContext?: AgentResumeContext,
): Promise<unknown> {
  const resuming = Boolean(resumeApproval);
  if (activeTask || activePreparation) {
    const error = new Error("já existe uma tarefa do AgentCore em execução; aguarde a conclusão");
    error.name = "ConflictError";
    throw error;
  }
  activeTask = true;
  activeOperationId = input.operationId;
  eventPublishQueue = Promise.resolve();
  taskStoreQueue = Promise.resolve();
  taskStoreError = "";
  eventFeedAvailable = true;
  approvalState.approved = false;
  activeResumeContext = null;
  activeStoreRequest = {
    prompt: input.prompt,
    objective: input.objective,
    workspaceRoot: input.workspaceRoot,
    operationId: input.operationId,
    history: input.history,
    requestId: input.requestId,
    conversationId: input.conversationId,
    preferences: input.preferences,
    attachments: input.attachments,
  };
  try {
    const agentInput = agentInputWithAttachments(input);
    // Consome o sinal persistido antes de qualquer ferramenta poder agir. Se o
    // processo cair no meio da retomada, a mesma aprovação não será reaplicada.
    await taskRunStore.savePendingApproval(null);
    pendingApproval = null;
    if (resumeApproval) await taskRunStore.savePendingTaskControl(resumeApproval.continuation.taskId ?? "", null);
    approvalState.approved = resuming;
    activeResumeContext = resumeApproval;
    const report = await core.pursue(agentInput, activeResumeContext?.continuation ?? checkpointContext,
      preparation, preparedContext, preparedInspection);
    await taskStoreQueue;
    await eventPublishQueue;
    const waitingForApproval = report.status === "blocked"
      && report.events.some((event) => event.kind === "approval.required")
      && report.inspection !== undefined
      && report.continuation !== undefined;
    if (waitingForApproval && report.inspection && report.continuation) {
      pendingApproval = {
        prompt: input.prompt,
        objective: input.objective,
        workspaceRoot: input.workspaceRoot ?? "",
        inspection: report.inspection,
        evidence: report.evidence,
        continuation: report.continuation,
        input,
      };
    }

    let taskStorage: {saved: boolean; directory: string; error?: string} = {
      saved: !taskStoreError,
      directory: taskRunStore.directory,
      ...(taskStoreError ? {error: taskStoreError} : {}),
    };
    try {
      await taskRunStore.savePendingApproval(pendingApproval
        ? {schema: "agent-pending-approval/v1", value: pendingApproval}
        : null);
      if (pendingApproval) {
        await taskRunStore.savePendingTaskControl(report.taskId,
          {schema: "agent-pending-control/v1", kind: "approval", value: pendingApproval});
      }
      await taskRunStore.saveReport(report, Boolean(waitingForApproval));
      if (taskStoreError) taskStorage = {saved: false, directory: taskRunStore.directory, error: taskStoreError};
    } catch (error) {
      taskStorage = {
        saved: false,
        directory: taskRunStore.directory,
        error: error instanceof Error ? error.message : String(error),
      };
      console.error("Não foi possível persistir as seções da tarefa:", error);
    }
    const {continuation: _continuation, ...publicReport} = report;
    return {ok: true, report: {schema: "agent-report/v2", ...publicReport}, cognition, taskStorage,
      preparation: preparation ? {id: cognition?.preparation_id, reused: preparationReused,
        ...(input.preparationId && !preparationReused ? {reason: "expired_or_request_mismatch"} : {})} : undefined,
      awaitingApproval: Boolean(waitingForApproval)};
  } finally {
    await eventPublishQueue;
    await taskStoreQueue;
    activeOperationId = "";
    activeStoreRequest = null;
    approvalState.approved = false;
    activeResumeContext = null;
    activeTask = false;
  }
}

async function resumeTask(taskId: string, value: unknown): Promise<unknown> {
  const continuation = parseAgentResumeInput(value);
  const fingerprint = JSON.stringify(continuation);
  const cached = await taskRunStore.loadResumeResult(taskId, continuation.requestId);
  if (cached !== undefined) {
    if (cached && typeof cached === "object" && (cached as Record<string, unknown>).schema === "agent-resume-idempotency/v1") {
      const stored = cached as {fingerprint?: unknown; response?: unknown};
      if (stored.fingerprint !== fingerprint) {
        const error = new Error("request_id já foi usado com outra decisão de retomada.");
        error.name = "ConflictError";
        throw error;
      }
      return stored.response;
    }
    return cached;
  }
  if (activeTask || activePreparation) {
    const error = new Error("já existe uma tarefa do AgentCore em execução; aguarde a conclusão");
    error.name = "ConflictError";
    throw error;
  }
  if (continuation.kind === "continue") {
    const response = await continueStoredTask(taskId, continuation.requestId);
    await taskRunStore.saveResumeResult(taskId, continuation.requestId,
      {schema: "agent-resume-idempotency/v1", fingerprint, response});
    return response;
  }
  const stored = await taskRunStore.loadPendingTaskControl(taskId);
  if (continuation.kind === "approval") {
    const pending = parsePendingApprovalControl(stored, taskId)
      ?? (pendingApproval?.continuation.taskId === taskId ? pendingApproval : undefined);
    const actionId = pending?.continuation.pendingToolCall?.id;
    if (!pending || actionId !== continuation.actionId) throw new Error("Ação de aprovação pendente não encontrada para esta tarefa.");
    const task = await taskRunStore.read(taskId);
    if (task.task.status !== "awaiting_approval") {
      const error = new Error("A tarefa não está mais aguardando esta aprovação.");
      error.name = "ConflictError";
      throw error;
    }
    if (continuation.decision === "reject") {
      await taskRunStore.markRejected(taskId, continuation.actionId);
      await taskRunStore.savePendingTaskControl(taskId, null);
      if (pendingApproval?.continuation.taskId === taskId) {
        pendingApproval = null;
        await taskRunStore.savePendingApproval(null);
      }
      const result = {ok: true, schema: "agent-resume-response/v1", task_id: taskId,
        request_id: continuation.requestId, status: "rejected", action_executed: false};
      await taskRunStore.saveResumeResult(taskId, continuation.requestId,
        {schema: "agent-resume-idempotency/v1", fingerprint, response: result});
      return result;
    }
    const input = await inputForApproval(pending, taskId);
    const prepared = pending.continuation.brainPreparation;
    const cognitive = prepared ? await guardedUnderstand(input, prepared) : undefined;
    const result = await executePursuit(input, pending, prepared, cognitive?.context, cognitive?.inspection, cognitive?.response);
    const response = {...(result as Record<string, unknown>), resume: {schema: "agent-resume-response/v1",
      task_id: taskId, request_id: continuation.requestId, decision: "approve"}};
    await taskRunStore.saveResumeResult(taskId, continuation.requestId,
      {schema: "agent-resume-idempotency/v1", fingerprint, response});
    return response;
  }
  const pending = pendingClarifications.get(taskId) ?? parsePendingClarification(stored, taskId);
  if (!pending) throw new Error("Clarificação pendente não encontrada para esta tarefa.");
  const updatedPreparation = await brain.recordClarification(pending.preparation, continuation.answers, false);
  const answerBlock = "\n\nRespostas confirmadas pelo usuário:\n" + continuation.answers.map((answer, index) => `${index + 1}. ${answer}`).join("\n");
  const updatedInput: AgentServerInput = {...pending.input, prompt: (pending.input.prompt + answerBlock).slice(0, 24_000),
    preparationId: undefined};
  const prepared = await guardedUnderstand(updatedInput, updatedPreparation);
  if (prepared.response.status === "clarifying") {
    const next: PendingClarification = {taskId, input: updatedInput, preparation: updatedPreparation};
    rememberClarification(next);
    await persistClarification(taskId, updatedInput, prepared.response);
    await taskRunStore.savePendingTaskControl(taskId, {schema: "agent-pending-control/v1", kind: "clarification", value: next});
    const result = {ok: true, schema: "agent-resume-response/v1", task_id: taskId,
      request_id: continuation.requestId, status: "clarifying", understanding: prepared.response};
    await taskRunStore.saveResumeResult(taskId, continuation.requestId,
      {schema: "agent-resume-idempotency/v1", fingerprint, response: result});
    return result;
  }
  pendingClarifications.delete(taskId);
  await taskRunStore.savePendingTaskControl(taskId, null);
  const result = await executePursuit(updatedInput, null, updatedPreparation, prepared.context, prepared.inspection, prepared.response);
  const response = {...(result as Record<string, unknown>), resume: {schema: "agent-resume-response/v1",
    task_id: taskId, request_id: continuation.requestId, decision: "clarification"}};
  await taskRunStore.saveResumeResult(taskId, continuation.requestId,
    {schema: "agent-resume-idempotency/v1", fingerprint, response});
  return response;
}

async function continueStoredTask(taskId: string, requestId: string, current?: AgentServerInput): Promise<unknown> {
  const {task, checkpoint} = await taskRunStore.read(taskId);
  if (!["blocked", "interrupted"].includes(task.status)) {
    throw new Error("Esta tarefa não aguarda continuação; confira sua entrega, esclarecimento ou aprovação pendente.");
  }
  if (!checkpoint) throw new Error("Esta tarefa não tem um checkpoint de execução disponível.");
  if (current?.conversationId && current.conversationId !== checkpoint.request.conversationId) {
    throw new Error("A tarefa pertence a outra conversa.");
  }
  if (checkpoint.request.workspaceRoot && !current?.workspaceRoot) {
    throw new Error("Selecione o workspace da tarefa antes de retomar a execução.");
  }
  const context = checkpointResume(checkpoint, current?.workspaceRoot);
  // A passing check describes the old files. Recheck after a resumed mutation
  // workflow rather than claiming that a stored verification is still current.
  if (context.hasChanges) context.verification = undefined;
  const input = parseAgentInput({...checkpoint.request, schema: "agent-request/v2",
    request_id: requestId, operation_id: current?.operationId ?? "agent-core-continue-" + crypto.randomUUID(), approved: false});
  return executePursuit(input, null, context.brainPreparation, undefined, undefined,
    {resumed_task_id: taskId, checkpoint_at: checkpoint.updatedAt}, false, context);
}

const server = createServer(async (request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    send(response, 200, {ok: true, service: "ia-local-agent-core", runtime: runtimeBaseUrl,
      inference: "checkpoint-local",
      model: process.env.IA_LOCAL_CHECKPOINT ?? "active-checkpoint",
      inferenceConnected: null});
    return;
  }
  const url = new URL(request.url ?? "/", "http://127.0.0.1");
  if (request.method === "GET" && url.pathname === "/tasks") {
    try {
      const operationId = url.searchParams.get("operation_id") ?? undefined;
      if (operationId && !/^agent-core-[A-Za-z0-9._-]{1,100}$/.test(operationId)) {
        throw new Error("Identificador de execução inválido.");
      }
      send(response, 200, {ok: true, tasks: (await taskRunStore.list(operationId)).slice(0, 100)});
    } catch (error) {
      send(response, 400, {ok: false, error: error instanceof Error ? error.message : String(error)});
    }
    return;
  }
  const workflowRoute = /^\/tasks\/([A-Za-z0-9_-]{1,100})\/workflow$/.exec(url.pathname);
  if (request.method === "GET" && workflowRoute) {
    try {
      const {task, events} = await taskRunStore.read(workflowRoute[1]!);
      send(response, 200, {ok: true, workflow: taskWorkflow(task, events)});
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      send(response, message.includes("não encontrada") ? 404 : 400, {ok: false, error: message});
    }
    return;
  }
  if (request.method === "GET" && url.pathname.startsWith("/tasks/")) {
    try {
      send(response, 200, {ok: true, ...(await taskRunStore.read(url.pathname.slice("/tasks/".length)))});
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      send(response, message.includes("não encontrada") ? 404 : 400, {ok: false, error: message});
    }
    return;
  }
  // Roteamento e requisitos são puros: não leem o workspace nem gravam estado.
  if (request.method === "POST" && (url.pathname === "/route" || url.pathname === "/requirements")) {
    try {
      const input = parseTaskRouteRequest(await readJson(request));
      if (url.pathname === "/route") {
        send(response, 200, {ok: true, ...taskRouteResponse(routeTask(input))});
      } else {
        const analysis = analyzeRequirements(input.prompt);
        send(response, 200, {ok: true, schema: "agent-requirements/v1", objective: analysis.objective,
          summary: analysis.summary, constraints: analysis.constraints.map(({id, text, source, mandatory}) => ({id, text, source, mandatory})),
          acceptance_criteria: analysis.acceptanceCriteria.map(({id, text, verifiable}) => ({id, text, verifiable})),
          missing_information: analysis.missingInformation, questions: analysis.questions,
          ambiguity_score: Number(analysis.ambiguityScore.toFixed(3)), requires_clarification: analysis.requiresClarification,
          interpretations: analysis.interpretations.map(({id, summary, assumptions, plausibility}) => ({id, summary, assumptions, plausibility})),
          execution_allowed: false});
      }
    } catch (error) {
      send(response, 400, {ok: false, error: error instanceof Error ? error.message : String(error)});
    }
    return;
  }
  if (request.method === "POST" && url.pathname === "/understand") {
    try {
      send(response, 200, await understand(await readJson(request)));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const status = error instanceof Error && error.name === "ConflictError" ? 409
        : message.includes("inválid") || message.includes("Contrato") || message.includes("JSON") ? 400 : 500;
      send(response, status, {ok: false, error: message});
    }
    return;
  }
  const resumeRoute = /^\/tasks\/([A-Za-z0-9_-]{1,100})\/resume$/.exec(url.pathname);
  if (request.method === "POST" && resumeRoute) {
    try {
      send(response, 200, await resumeTask(resumeRoute[1]!, await readJson(request)));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const status = error instanceof Error && error.name === "ConflictError" ? 409
        : message.includes("não encontrada") || message.includes("não encontrado") ? 404
          : message.includes("pendente") || message.includes("retomada") ? 409
            : message.includes("inválid") || message.includes("Contrato") || message.includes("JSON") ? 400 : 500;
      send(response, status, {ok: false, error: message});
    }
    return;
  }
  if (request.method !== "POST" || request.url !== "/pursue") {
    send(response, 404, {ok: false, error: "rota não encontrada"});
    return;
  }
  try {
    send(response, 200, await pursue(await readJson(request)));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const status = error instanceof Error && error.name === "ConflictError"
      ? 409
      : message.includes("JSON") || message.includes("workspace") || message.includes("objetivo") || message.includes("aprovação") ? 400 : 500;
    send(response, status, {
      ok: false,
      error: message,
    });
  }
});

async function restorePendingApproval(): Promise<void> {
  const saved = await taskRunStore.loadPendingApproval();
  if (saved === undefined) return;
  if (saved && typeof saved === "object") {
    const row = saved as Record<string, unknown>;
    if (row.schema === "agent-pending-approval/v1" && isPendingApproval(row.value)) {
      pendingApproval = row.value;
      console.error("Aprovação pendente restaurada para a tarefa " + pendingApproval.continuation.taskId + ".");
      return;
    }
  }
  await taskRunStore.savePendingApproval(null);
  console.error("Estado de aprovação persistido inválido; foi descartado sem executar ferramentas.");
}

async function startServer(): Promise<void> {
  try {
    const interrupted = await taskRunStore.markInterruptedOnStartup();
    if (interrupted) console.error(`${interrupted} tarefa(s) interrompida(s) pelo reinício; efeitos não serão repetidos.`);
    await restorePendingApproval();
  } catch (error) {
    console.error("Não foi possível restaurar estado de aprovação:", error);
  }
  server.listen(defaultPort, "127.0.0.1", () => {
    console.error(`AgentCore TypeScript em http://127.0.0.1:${defaultPort}`);
  });
}

void startServer();
