import {appendFile, mkdir, readFile} from "node:fs/promises";
import {dirname} from "node:path";
import type {BrainEvent, BrainEventLog} from "./brain-contracts.ts";

function isBrainEvent(value: unknown): value is BrainEvent {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  return row.version === 1
    && typeof row.id === "string"
    && typeof row.seq === "number"
    && typeof row.taskId === "string"
    && typeof row.at === "string"
    && typeof row.type === "string"
    && typeof row.from === "string"
    && typeof row.to === "string"
    && typeof row.reason === "string";
}

export class InMemoryBrainEventLog implements BrainEventLog {
  private readonly rows: BrainEvent[] = [];

  async append(event: BrainEvent): Promise<void> {
    if (!isBrainEvent(event)) throw new Error("Evento cognitivo inválido.");
    if (this.rows.some((row) => row.id === event.id)) {
      throw new Error("Evento cognitivo duplicado: " + event.id);
    }
    this.rows.push({...event, payload: event.payload ? {...event.payload} : undefined});
  }

  async read(taskId?: string): Promise<readonly BrainEvent[]> {
    return this.rows
      .filter((event) => !taskId || event.taskId === taskId)
      .map((event) => ({...event, payload: event.payload ? {...event.payload} : undefined}));
  }
}

export class JsonlBrainEventLog implements BrainEventLog {
  private readonly path: string;

  constructor(path: string) {
    this.path = path;
    if (!path.trim()) throw new Error("O caminho do log cognitivo é obrigatório.");
  }

  async append(event: BrainEvent): Promise<void> {
    if (!isBrainEvent(event)) throw new Error("Evento cognitivo inválido.");
    await mkdir(dirname(this.path), {recursive: true});
    await appendFile(this.path, JSON.stringify(event) + "\n", "utf8");
  }

  async read(taskId?: string): Promise<readonly BrainEvent[]> {
    let content = "";
    try {
      content = await readFile(this.path, "utf8");
    } catch (error) {
      const code = error && typeof error === "object" ? (error as {code?: string}).code : undefined;
      if (code === "ENOENT") return [];
      throw error;
    }
    const rows: BrainEvent[] = [];
    for (const [index, line] of content.split("\n").entries()) {
      if (!line.trim()) continue;
      let parsed: unknown;
      try {
        parsed = JSON.parse(line);
      } catch {
        throw new Error("Linha inválida no log cognitivo: " + (index + 1));
      }
      if (!isBrainEvent(parsed)) {
        throw new Error("Evento inválido no log cognitivo: linha " + (index + 1));
      }
      if (!taskId || parsed.taskId === taskId) rows.push(parsed);
    }
    return rows;
  }
}
