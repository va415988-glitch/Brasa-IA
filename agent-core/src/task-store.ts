import {appendFile, mkdir, readFile, readdir, rename, rm, writeFile} from "node:fs/promises";
import {basename, dirname, join} from "node:path";
import type {AgentEvent, AgentInput, AgentReport, TaskCheckpoint} from "./contracts.ts";

export interface PersistedTaskRequest {
  prompt: string;
  objective: AgentInput["objective"];
  workspaceRoot?: string;
  operationId: string;
  history?: AgentInput["history"];
  requestId?: string;
  conversationId?: string;
  preferences?: AgentInput["preferences"];
  attachments?: AgentInput["attachments"];
}

export interface TaskIndex {
  schema: "agent-task/v1";
  taskId: string;
  status: "running" | "clarifying" | "interrupted" | "awaiting_approval" | "blocked" | "completed" | "failed";
  createdAt: string;
  updatedAt: string;
  request: PersistedTaskRequest;
  operationIds?: readonly string[];
  error?: string;
}

function assertTaskId(taskId: string): void {
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(taskId)) throw new Error("Identificador de tarefa inválido para persistência.");
}

function errorCode(error: unknown): string | undefined {
  return error && typeof error === "object" ? (error as {code?: string}).code : undefined;
}

function sectionPath(root: string, taskId: string, section: string, file: string): string {
  return join(root, taskId, "sections", section, file);
}

/** Armazenamento local recuperável do percurso e dos artefatos de cada tarefa. */
export class FileTaskRunStore {
  private readonly root: string;
  private readonly eventSequences = new Map<string, number>();

  constructor(root: string) {
    if (!root.trim()) throw new Error("O diretório de tarefas é obrigatório.");
    this.root = root;
  }

  get directory(): string {
    return this.root;
  }

  async list(operationId?: string): Promise<TaskIndex[]> {
    let entries;
    try {
      entries = await readdir(this.root, {withFileTypes: true});
    } catch (error) {
      if (errorCode(error) === "ENOENT") return [];
      throw error;
    }
    const tasks: TaskIndex[] = [];
    for (const entry of entries) {
      if (!entry.isDirectory() || !/^[A-Za-z0-9_-]{1,100}$/.test(entry.name)) continue;
      try {
        const index = JSON.parse(await readFile(join(this.root, entry.name, "task.json"), "utf8")) as TaskIndex;
        if (index.schema === "agent-task/v1" && index.taskId === entry.name
            && (!operationId || index.request?.operationId === operationId || index.operationIds?.includes(operationId))) tasks.push(index);
      } catch (error) {
        if (errorCode(error) !== "ENOENT") throw error;
      }
    }
    return tasks.sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
  }

  async read(taskId: string): Promise<{task: TaskIndex; events: AgentEvent[]; report: unknown | null; clarification: unknown | null; checkpoint: TaskCheckpoint | null}> {
    assertTaskId(taskId);
    let task: TaskIndex;
    try {
      task = JSON.parse(await readFile(join(this.root, taskId, "task.json"), "utf8")) as TaskIndex;
    } catch (error) {
      if (errorCode(error) === "ENOENT") throw new Error("Tarefa persistida não encontrada.");
      throw error;
    }
    if (task.schema !== "agent-task/v1" || task.taskId !== taskId) throw new Error("Índice de tarefa inválido.");
    let report: unknown = null;
    try {
      report = JSON.parse(await readFile(sectionPath(this.root, taskId, "delivery", "report.json"), "utf8"));
    } catch (error) {
      if (errorCode(error) !== "ENOENT") throw error;
    }
    let clarification: unknown = null;
    try {
      clarification = JSON.parse(await readFile(sectionPath(this.root, taskId, "requirements", "clarification.json"), "utf8"));
    } catch (error) {
      if (errorCode(error) !== "ENOENT") throw error;
    }
    return {task, events: await this.readEvents(taskId), report, clarification, checkpoint: await this.loadCheckpoint(taskId)};
  }

  async saveCheckpoint(checkpoint: TaskCheckpoint): Promise<void> {
    assertTaskId(checkpoint.taskId);
    if (checkpoint.schema !== "agent-checkpoint/v1" || checkpoint.resume.taskId !== checkpoint.taskId) {
      throw new Error("Checkpoint incompatível com a tarefa.");
    }
    await this.writeJsonAtomic(sectionPath(this.root, checkpoint.taskId, "state", "checkpoint.json"), checkpoint);
  }

  async loadCheckpoint(taskId: string): Promise<TaskCheckpoint | null> {
    assertTaskId(taskId);
    let value: TaskCheckpoint;
    try {
      value = JSON.parse(await readFile(sectionPath(this.root, taskId, "state", "checkpoint.json"), "utf8")) as TaskCheckpoint;
    } catch (error) {
      if (errorCode(error) === "ENOENT") return null;
      throw error;
    }
    if (value.schema !== "agent-checkpoint/v1" || value.taskId !== taskId || value.resume?.taskId !== taskId
        || !value.request?.prompt || !Array.isArray(value.resume.plannerMessages)
        || !["planning", "executing", "observed"].includes(value.phase)) throw new Error("Checkpoint persistido inválido.");
    return value;
  }

  async resumable(conversationId: string, workspaceRoot?: string): Promise<TaskIndex | undefined> {
    if (!conversationId) return undefined;
    const tasks = await this.list();
    // A new task supersedes old work in this conversation. Never resurrect an
    // older blocked task behind a completed/newer one, or borrow another chat.
    const latest = tasks.find(task => task.request.conversationId === conversationId);
    if (!latest || !["blocked", "interrupted"].includes(latest.status)) return undefined;
    if (latest.request.workspaceRoot && (!workspaceRoot || latest.request.workspaceRoot !== workspaceRoot)) return undefined;
    return await this.loadCheckpoint(latest.taskId) ? latest : undefined;
  }

  async markInterruptedOnStartup(): Promise<number> {
    let count = 0;
    for (const task of await this.list()) {
      if (task.status !== "running") continue;
      await this.writeJsonAtomic(join(this.root, task.taskId, "task.json"), {
        ...task, status: "interrupted", updatedAt: new Date().toISOString(),
        error: "Servidor reiniciado durante a tarefa; confira os efeitos antes de tentar outra execução.",
      } satisfies TaskIndex);
      count += 1;
    }
    return count;
  }

  async begin(taskId: string, request: PersistedTaskRequest, initialStatus: "running" | "clarifying" = "running"): Promise<void> {
    assertTaskId(taskId);
    const indexPath = join(this.root, taskId, "task.json");
    await mkdir(join(this.root, taskId), {recursive: true, mode: 0o700});
    let existing: TaskIndex | undefined;
    try {
      existing = JSON.parse(await readFile(indexPath, "utf8")) as TaskIndex;
    } catch (error) {
      if (errorCode(error) !== "ENOENT") throw error;
    }
    const now = new Date().toISOString();
    const index: TaskIndex = existing?.schema === "agent-task/v1" && existing.taskId === taskId
      ? {...existing, request, status: initialStatus, updatedAt: now, error: undefined,
          operationIds: [...new Set([...(existing.operationIds ?? []), existing.request.operationId, request.operationId])]}
      : {schema: "agent-task/v1", taskId, status: initialStatus, createdAt: now, updatedAt: now, request};
    await this.writeJsonAtomic(indexPath, index);
    await this.writeJsonAtomic(sectionPath(this.root, taskId, "request", "request.json"), request);
    if (initialStatus === "running") await this.saveClarificationState(taskId, null);
  }

  async saveClarificationState(taskId: string, clarification: unknown | null): Promise<void> {
    assertTaskId(taskId);
    const path = sectionPath(this.root, taskId, "requirements", "clarification.json");
    if (clarification === null) {
      await rm(path, {force: true});
      return;
    }
    await this.writeJsonAtomic(path, clarification);
  }

  async appendEvent(event: AgentEvent): Promise<void> {
    assertTaskId(event.taskId);
    const taskDirectory = join(this.root, event.taskId);
    await mkdir(taskDirectory, {recursive: true, mode: 0o700});
    let sequence = this.eventSequences.get(event.taskId);
    if (sequence === undefined) {
      try {
        const rows = (await readFile(join(taskDirectory, "events.jsonl"), "utf8"))
          .split("\n").filter((line) => line.trim());
        sequence = rows.length;
      } catch (error) {
        if (errorCode(error) !== "ENOENT") throw error;
        sequence = 0;
      }
    }
    const nextSequence = typeof sequence === "number" ? sequence + 1 : 1;
    this.eventSequences.set(event.taskId, nextSequence);
    const stored = {schema: "agent-event/v1", storedSequence: nextSequence, event};
    await appendFile(join(taskDirectory, "events.jsonl"), JSON.stringify(stored) + "\n", {encoding: "utf8", mode: 0o600});
  }

  async saveReport(report: AgentReport, awaitingApproval = false): Promise<void> {
    const taskId = report.taskId;
    assertTaskId(taskId);
    const taskDirectory = join(this.root, taskId);
    const storedStatus: TaskIndex["status"] = report.status === "completed" ? "completed"
      : awaitingApproval ? "awaiting_approval" : "blocked";
    const indexPath = join(taskDirectory, "task.json");
    let index: TaskIndex;
    try {
      index = JSON.parse(await readFile(indexPath, "utf8")) as TaskIndex;
      if (index.schema !== "agent-task/v1" || index.taskId !== taskId) throw new Error("Índice de tarefa inválido.");
      index = {...index, status: storedStatus, updatedAt: new Date().toISOString(), error: report.error};
    } catch (error) {
      if (errorCode(error) !== "ENOENT") throw error;
      throw new Error("A tarefa precisa ser inicializada antes de persistir seu relatório.");
    }
    await this.writeJsonAtomic(sectionPath(this.root, taskId, "context", "inspection.json"), report.inspection ?? null);
    await this.writeJsonAtomic(sectionPath(this.root, taskId, "requirements", "requirements.json"), report.requirements ?? null);
    await this.writeJsonAtomic(sectionPath(this.root, taskId, "plan", "plan.json"), report.plan ?? null);
    await this.writeJsonAtomic(sectionPath(this.root, taskId, "evidence", "sources.json"), report.evidence);
    const storedEvents = await this.readEvents(taskId);
    await this.writeJsonAtomic(sectionPath(this.root, taskId, "actions", "activity.json"),
      storedEvents.filter((event) => event.phase === "act" || event.phase === "verify"));
    await this.writeArtifacts(taskId, report);
    await this.writeJsonAtomic(sectionPath(this.root, taskId, "verification", "verification.json"), report.verification ?? null);
    await this.writeJsonAtomic(sectionPath(this.root, taskId, "state", "continuation.json"), report.continuation ?? null);
    await this.writeTextAtomic(sectionPath(this.root, taskId, "delivery", "response.md"), report.finalText ?? "");
    await this.writeJsonAtomic(sectionPath(this.root, taskId, "delivery", "report.json"), {
      ...report,
      continuation: undefined,
      events: undefined,
      artifacts: report.artifacts.map(({path, language}) => ({path, language})),
      eventCount: storedEvents.length,
    });
    await this.writeJsonAtomic(indexPath, index);
  }

  private async readEvents(taskId: string): Promise<AgentEvent[]> {
    let content: string;
    try {
      content = await readFile(join(this.root, taskId, "events.jsonl"), "utf8");
    } catch (error) {
      if (errorCode(error) === "ENOENT") return [];
      throw error;
    }
    const events: AgentEvent[] = [];
    for (const [index, line] of content.split("\n").entries()) {
      if (!line.trim()) continue;
      let parsed: unknown;
      try {
        parsed = JSON.parse(line);
      } catch {
        throw new Error("Evento inválido no armazenamento da tarefa, linha " + (index + 1) + ".");
      }
      if (!parsed || typeof parsed !== "object" || (parsed as Record<string, unknown>).schema !== "agent-event/v1") {
        throw new Error("Envelope de evento inválido no armazenamento da tarefa, linha " + (index + 1) + ".");
      }
      const event = (parsed as {event?: unknown}).event;
      if (!event || typeof event !== "object" || (event as Record<string, unknown>).taskId !== taskId) {
        throw new Error("Evento de outra tarefa no armazenamento, linha " + (index + 1) + ".");
      }
      events.push(event as AgentEvent);
    }
    return events;
  }

  async savePendingApproval(value: unknown | null): Promise<void> {
    const path = join(this.root, "control", "pending-approval.json");
    if (value === null) {
      await rm(path, {force: true});
      return;
    }
    await this.writeJsonAtomic(path, value);
  }

  async loadPendingApproval(): Promise<unknown | undefined> {
    try {
      return JSON.parse(await readFile(join(this.root, "control", "pending-approval.json"), "utf8")) as unknown;
    } catch (error) {
      if (errorCode(error) === "ENOENT") return undefined;
      throw error;
    }
  }

  async savePendingTaskControl(taskId: string, value: unknown | null): Promise<void> {
    assertTaskId(taskId);
    const path = join(this.root, "control", "pending", taskId + ".json");
    if (value === null) {
      await rm(path, {force: true});
      return;
    }
    await this.writeJsonAtomic(path, value);
  }

  async loadPendingTaskControl(taskId: string): Promise<unknown | undefined> {
    assertTaskId(taskId);
    try {
      return JSON.parse(await readFile(join(this.root, "control", "pending", taskId + ".json"), "utf8")) as unknown;
    } catch (error) {
      if (errorCode(error) === "ENOENT") return undefined;
      throw error;
    }
  }

  async loadResumeResult(taskId: string, requestId: string): Promise<unknown | undefined> {
    assertTaskId(taskId);
    try {
      const rows = JSON.parse(await readFile(join(this.root, "control", "resume-results", taskId + ".json"), "utf8")) as Record<string, unknown>;
      return rows[requestId];
    } catch (error) {
      if (errorCode(error) === "ENOENT") return undefined;
      throw error;
    }
  }

  async saveResumeResult(taskId: string, requestId: string, result: unknown): Promise<void> {
    assertTaskId(taskId);
    if (!/^[A-Za-z0-9._:-]{1,160}$/.test(requestId)) throw new Error("Identificador de retomada inválido.");
    const path = join(this.root, "control", "resume-results", taskId + ".json");
    let rows: Record<string, unknown> = {};
    try {
      rows = JSON.parse(await readFile(path, "utf8")) as Record<string, unknown>;
    } catch (error) {
      if (errorCode(error) !== "ENOENT") throw error;
    }
    rows[requestId] = result;
    const keys = Object.keys(rows);
    for (const key of keys.slice(0, Math.max(0, keys.length - 20))) delete rows[key];
    await this.writeJsonAtomic(path, rows);
  }

  async markRejected(taskId: string, actionId: string): Promise<void> {
    assertTaskId(taskId);
    const indexPath = join(this.root, taskId, "task.json");
    let index: TaskIndex;
    try {
      index = JSON.parse(await readFile(indexPath, "utf8")) as TaskIndex;
    } catch (error) {
      if (errorCode(error) === "ENOENT") throw new Error("Tarefa persistida não encontrada.");
      throw error;
    }
    if (index.schema !== "agent-task/v1" || index.taskId !== taskId) throw new Error("Índice de tarefa inválido.");
    const error = "Ação " + actionId + " rejeitada pelo usuário; a ferramenta não foi executada.";
    await this.writeJsonAtomic(indexPath, {...index, status: "blocked", updatedAt: new Date().toISOString(), error});
    await this.writeJsonAtomic(sectionPath(this.root, taskId, "state", "continuation.json"), null);
    await this.appendEvent({seq: 0, taskId, phase: "complete", status: "blocked", kind: "approval.rejected",
      title: "Ação rejeitada", detail: error, payload: {actionId}});
  }

  private async writeArtifacts(taskId: string, report: AgentReport): Promise<void> {
    const directory = join(this.root, taskId, "sections", "artifacts");
    await mkdir(directory, {recursive: true, mode: 0o700});
    const index: Array<{path: string; language: string; storedFile?: string; bytes: number}> = [];
    for (const [position, artifact] of report.artifacts.entries()) {
      const name = basename(artifact.path.replaceAll("\\", "/"))
        .replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 100) || "artifact";
      const storedFile = String(position + 1).padStart(3, "0") + "-" + name;
      const content = artifact.content ?? "";
      await this.writeTextAtomic(join(directory, storedFile), content);
      index.push({path: artifact.path, language: artifact.language, storedFile,
        bytes: Buffer.byteLength(content, "utf8")});
    }
    await this.writeJsonAtomic(join(directory, "index.json"), index);
  }

  private async writeJsonAtomic(path: string, value: unknown): Promise<void> {
    await mkdir(dirname(path), {recursive: true, mode: 0o700});
    const temporary = path + "." + crypto.randomUUID() + ".tmp";
    await writeFile(temporary, JSON.stringify(value, null, 2) + "\n", {encoding: "utf8", mode: 0o600});
    await rename(temporary, path);
  }

  private async writeTextAtomic(path: string, value: string): Promise<void> {
    await mkdir(dirname(path), {recursive: true, mode: 0o700});
    const temporary = path + "." + crypto.randomUUID() + ".tmp";
    await writeFile(temporary, value, {encoding: "utf8", mode: 0o600});
    await rename(temporary, path);
  }
}
