import type {AgentInput, AgentAttachment} from "./contracts.ts";

export interface AgentServerInput extends AgentInput {
  approved?: boolean;
  operationId: string;
  schema?: "agent-request/v2";
}

export type AgentResumeInput =
  | {schema: "agent-resume/v1"; requestId: string; kind: "continue"}
  | {schema: "agent-resume/v1"; requestId: string; kind: "clarification"; answers: string[]}
  | {schema: "agent-resume/v1"; requestId: string; kind: "approval"; actionId: string; decision: "approve" | "reject"};

export function parseAgentResumeInput(value: unknown): AgentResumeInput {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("A retomada deve ser um objeto.");
  const row = value as Record<string, unknown>;
  const requestId = String(row.request_id ?? "").trim();
  if (row.schema !== "agent-resume/v1") throw new Error("Contrato de retomada inválido; use agent-resume/v1.");
  if (!requestId || requestId.length > 160 || !/^[A-Za-z0-9._:-]+$/.test(requestId)
    || ["__proto__", "constructor", "prototype"].includes(requestId)) throw new Error("Identificador de retomada inválido.");
  if (row.kind === "continue") return {schema: "agent-resume/v1", requestId, kind: "continue"};
  if (row.kind === "clarification") {
    if (!Array.isArray(row.answers) || row.answers.length < 1 || row.answers.length > 3
        || row.answers.some((answer) => typeof answer !== "string" || !answer.trim() || answer.length > 2000)) {
      throw new Error("A clarificação exige de uma a três respostas de até 2000 caracteres.");
    }
    return {schema: "agent-resume/v1", requestId, kind: "clarification", answers: row.answers.map((answer) => (answer as string).trim())};
  }
  if (row.kind === "approval") {
    const actionId = String(row.action_id ?? "").trim();
    const decision = row.decision;
    if (!actionId || actionId.length > 160 || !/^[A-Za-z0-9._:-]+$/.test(actionId)) throw new Error("Identificador da ação inválido.");
    if (decision !== "approve" && decision !== "reject") throw new Error("A decisão precisa ser approve ou reject.");
    return {schema: "agent-resume/v1", requestId, kind: "approval", actionId,
      decision: decision as "approve" | "reject"};
  }
  throw new Error("Tipo de retomada inválido.");
}

export function parseAgentInput(value: unknown, options: {allowMissingWorkspace?: boolean} = {}): AgentServerInput {
  if (!value || typeof value !== "object") throw new Error("O pedido do agente deve ser um objeto.");
  const payload = value as Record<string, unknown>;
  if (payload.schema !== undefined && payload.schema !== "agent-request/v2") {
    throw new Error("Contrato de pedido inválido; use agent-request/v2.");
  }
  const prompt = String(payload.prompt ?? "").trim();
  const objective = String(payload.objective ?? (payload.schema === "agent-request/v2" ? "auto" : "build"));
  const workspaceRoot = String(payload.workspace_root ?? payload.workspaceRoot ?? "").trim();
  const operationId = String(payload.operation_id ?? payload.operationId ?? `agent-core-${Date.now()}`).trim();
  const requestId = String(payload.request_id ?? payload.requestId
    ?? (payload.schema === "agent-request/v2" ? "req-" + crypto.randomUUID() : "")).trim();
  const conversationId = String(payload.conversation_id ?? payload.conversationId ?? "").trim();
  const preparationId = String(payload.preparation_id ?? payload.preparationId ?? "").trim();
  if (!prompt || prompt.length > 24000) throw new Error("O objetivo deve ter entre 1 e 24000 caracteres.");
  if (!/^agent-core-[A-Za-z0-9._-]{1,100}$/.test(operationId)) throw new Error("Identificador de execução inválido.");
  if (requestId && (requestId.length > 160 || !/^[A-Za-z0-9._:-]+$/.test(requestId))) throw new Error("Identificador de pedido inválido.");
  if (conversationId && (conversationId.length > 160 || !/^[A-Za-z0-9._:-]+$/.test(conversationId))) throw new Error("Identificador de conversa inválido.");
  if (preparationId && !/^prep-[A-Za-z0-9-]{1,120}$/.test(preparationId)) throw new Error("Identificador de preparação inválido.");
  if (!( ["auto", "build", "research", "analyze", "conversation", "debug", "testing", "learn", "operate"] as string[]).includes(objective)) {
    throw new Error("Objetivo inválido; use auto, build, research, analyze, conversation, debug, testing, learn ou operate.");
  }
  if (objective !== "conversation" && workspaceRoot && !workspaceRoot.startsWith("/")) {
    throw new Error("Selecione um workspace absoluto antes de iniciar o agente.");
  }
  const workspaceRequired = !["conversation", "research", "learn"].includes(objective);
  if (!options.allowMissingWorkspace && workspaceRequired && objective !== "auto" && !workspaceRoot) {
    throw new Error("Selecione um workspace absoluto antes de iniciar o agente.");
  }
  const rawHistory = Array.isArray(payload.history) ? payload.history : [];
  if (rawHistory.length > 60) throw new Error("O histórico contém mais de 60 mensagens.");
  const history = rawHistory.map((item) => {
    if (!item || typeof item !== "object") throw new Error("Mensagem de histórico inválida.");
    const row = item as Record<string, unknown>;
    if ((row.role !== "user" && row.role !== "assistant") || typeof row.content !== "string") {
      throw new Error("O histórico aceita apenas mensagens de usuário e assistente.");
    }
    if (row.content.length > 12000) throw new Error("Uma mensagem do histórico excede 12000 caracteres.");
    return {role: row.role as "user" | "assistant", content: row.content};
  });
  const rawAttachments = Array.isArray(payload.attachments) ? payload.attachments : [];
  if (rawAttachments.length > 8) throw new Error("O pedido contém mais de 8 anexos.");
  const attachments: AgentAttachment[] = rawAttachments.map((item) => {
    if (!item || typeof item !== "object") throw new Error("Anexo inválido.");
    const row = item as Record<string, unknown>;
    const path = String(row.path ?? "").trim();
    const content = String(row.content ?? "");
    if (!path || path.length > 1024 || path.startsWith("/") || path.includes("..")) {
      throw new Error("Caminho de anexo inválido.");
    }
    if (content.length > 120000) throw new Error("Anexo acima do limite de 120000 caracteres.");
    const mediaType = row.media_type ?? row.mediaType;
    const assetId = row.asset_id ?? row.assetId;
    if (assetId !== undefined && (typeof assetId !== "string" || !/^[a-f0-9]{64}$/.test(assetId))) {
      throw new Error("Identificador de anexo inválido.");
    }
    const kind = row.kind;
    if (kind !== undefined && !["image", "audio", "video", "document", "text"].includes(String(kind))) {
      throw new Error("Tipo de anexo inválido.");
    }
    if (row.observation !== undefined && (!row.observation || typeof row.observation !== "object"
      || Array.isArray(row.observation) || JSON.stringify(row.observation).length > 24000)) {
      throw new Error("Observação de anexo inválida ou acima do limite.");
    }
    return {path, content, mediaType: typeof mediaType === "string" ? mediaType.slice(0, 120) : undefined,
      ...(typeof assetId === "string" ? {assetId} : {}),
      ...(kind ? {kind: kind as AgentAttachment["kind"]} : {}),
      ...(row.observation ? {observation: row.observation as Record<string, unknown>} : {}),
      ...(typeof row.analysisStatus === "string" ? {analysisStatus: row.analysisStatus.slice(0, 40)} : {}),
      ...(Array.isArray(row.warnings) ? {warnings: row.warnings.filter((item): item is string => typeof item === "string")
        .slice(0, 12).map(item => item.slice(0, 500))} : {})};
  });
  const rawPreferences = payload.preferences;
  let preferences: AgentInput["preferences"];
  if (rawPreferences !== undefined) {
    if (!rawPreferences || typeof rawPreferences !== "object" || Array.isArray(rawPreferences)) {
      throw new Error("Preferências inválidas.");
    }
    const row = rawPreferences as Record<string, unknown>;
    if (Object.keys(row).some((key) => !["language", "stack"].includes(key))) throw new Error("Preferência não reconhecida.");
    const language = row.language === undefined ? undefined : String(row.language).trim();
    const stack = row.stack === null || row.stack === undefined ? row.stack : String(row.stack).trim();
    if (language && (language.length > 40 || !/^[A-Za-z0-9-]+$/.test(language))) throw new Error("Idioma de preferência inválido.");
    if (typeof stack === "string" && (stack.length > 120 || !stack)) throw new Error("Stack de preferência inválida.");
    preferences = { ...(language ? {language} : {}), ...(stack !== undefined ? {stack} : {}) };
  }
  return {
    prompt,
    objective: objective as AgentInput["objective"],
    workspaceRoot: workspaceRoot || undefined,
    history,
    ...(attachments.length ? {attachments} : {}),
    ...(requestId ? {requestId} : {}),
    ...(conversationId ? {conversationId} : {}),
    ...(preparationId ? {preparationId} : {}),
    ...(preferences ? {preferences} : {}),
    approved: payload.schema !== "agent-request/v2" && payload.approved === true,
    operationId,
    ...(payload.schema === "agent-request/v2" ? {schema: "agent-request/v2" as const} : {}),
  };
}

export interface TaskRouteRequest {
  schema: "task-route-request/v1";
  prompt: string;
  workspaceSelected: boolean;
}

/** Pedido de roteamento ou de extração de requisitos; nunca executa ferramentas. */
export function parseTaskRouteRequest(value: unknown): TaskRouteRequest {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("O pedido de roteamento deve ser um objeto.");
  const row = value as Record<string, unknown>;
  if (row.schema !== undefined && row.schema !== "task-route-request/v1") {
    throw new Error("Contrato de roteamento inválido; use task-route-request/v1.");
  }
  if (typeof row.prompt !== "string" || !row.prompt.trim() || row.prompt.length > 24000) {
    throw new Error("O pedido deve ter entre 1 e 24000 caracteres.");
  }
  if (row.workspace_selected !== undefined && typeof row.workspace_selected !== "boolean") {
    throw new Error("workspace_selected deve ser booleano.");
  }
  const unknown = Object.keys(row).filter((key) => !["schema", "prompt", "workspace_selected"].includes(key));
  if (unknown.length) throw new Error("Campos inválidos no roteamento: " + unknown.join(", ") + ".");
  return {schema: "task-route-request/v1", prompt: row.prompt.trim(), workspaceSelected: row.workspace_selected === true};
}
