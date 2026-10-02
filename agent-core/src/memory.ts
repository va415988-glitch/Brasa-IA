import {mkdirSync, readFileSync, renameSync, writeFileSync} from "node:fs";
import {dirname} from "node:path";
import type {MemoryEntry, MemoryInput, MemoryKind, MemoryPort} from "./contracts.ts";

export interface MemorySnapshot {
  version: 1;
  entries: readonly MemoryEntry[];
}

type Clock = () => string;

const secretPatterns = [
  /-----BEGIN [^-]*PRIVATE KEY-----/i,
  /\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret)\s*[:=]\s*\S+/i,
  /\bsk-[A-Za-z0-9]{20,}\b/,
];

function normalize(value: string): string {
  return value.normalize("NFKC").toLowerCase().trim();
}

function tokens(value: string): string[] {
  return normalize(value).split(/[^\p{L}\p{N}_-]+/u).filter((token) => token.length > 1);
}

export function validateMemoryInput(input: MemoryInput): MemoryInput {
  if (!["goal", "workspace", "observation", "evidence", "decision"].includes(input.kind)) {
    throw new Error("O tipo da memória não é suportado.");
  }
  const key = String(input.key ?? "").trim();
  const value = String(input.value ?? "").trim();
  if (!key) throw new Error("A memória precisa de uma chave.");
  if (!value) throw new Error("A memória precisa de conteúdo.");
  if (key.length > 512) throw new Error("A chave da memória excede o limite.");
  if (value.length > 20000) throw new Error("O conteúdo da memória excede o limite.");
  if (input.confidence !== undefined
      && (!Number.isFinite(input.confidence) || input.confidence < 0 || input.confidence > 1)) {
    throw new Error("A confiança da memória deve estar entre 0 e 1.");
  }
  if (secretPatterns.some((pattern) => pattern.test(value))) {
    throw new Error("A memória rejeitou um possível segredo ou credencial.");
  }
  const source = input.source?.trim() || undefined;
  if (source && source.length > 2048) throw new Error("A origem da memória excede o limite.");
  return {...input, key, value, source};
}

export class LocalTaskMemory implements MemoryPort {
  private readonly entries = new Map<string, MemoryEntry>();
  private readonly limit: number;
  private readonly clock: Clock;
  private nextId = 1;

  constructor(limit = 1000, clock: Clock = () => new Date().toISOString()) {
    if (limit < 1) throw new Error("O limite da memória deve ser positivo.");
    this.limit = limit;
    this.clock = clock;
  }

  async remember(input: MemoryInput): Promise<MemoryEntry> {
    const validated = validateMemoryInput(input);
    const key = validated.kind + ":" + normalize(validated.key);
    const now = this.clock();
    const previous = this.entries.get(key);
    const entry: MemoryEntry = {
      id: previous?.id ?? "mem-" + this.nextId++,
      kind: validated.kind,
      key: validated.key,
      value: validated.value,
      source: validated.source,
      confidence: validated.confidence,
      taskId: validated.taskId,
      createdAt: previous?.createdAt ?? now,
      updatedAt: now,
    };
    this.entries.set(key, entry);
    this.trim();
    return entry;
  }

  async search(query: string, limit = 8): Promise<readonly MemoryEntry[]> {
    const requested = Math.max(1, Math.min(limit, 50));
    const queryTokens = tokens(query);
    const ranked = [...this.entries.values()].map((entry) => {
      const haystack = tokens(entry.kind + " " + entry.key + " " + entry.value + " " + (entry.source ?? ""));
      const score = queryTokens.length === 0
        ? 1
        : queryTokens.reduce((total, token) => total + (haystack.includes(token) ? 1 : 0), 0);
      return {entry, score};
    }).filter((item) => item.score > 0);
    ranked.sort((left, right) => right.score - left.score || right.entry.updatedAt.localeCompare(left.entry.updatedAt));
    return ranked.slice(0, requested).map((item) => item.entry);
  }

  snapshot(): MemorySnapshot {
    return {version: 1, entries: [...this.entries.values()]};
  }

  restore(snapshot: MemorySnapshot): void {
    if (snapshot.version !== 1) throw new Error("Versão de memória não suportada.");
    const validatedEntries = snapshot.entries.map((entry) => {
      const validated = validateMemoryInput(entry);
      if (!entry.id || !entry.createdAt || !entry.updatedAt) {
        throw new Error("Snapshot de memória inválido: metadados ausentes.");
      }
      return {entry: {...entry, ...validated}, key: validated.kind + ":" + normalize(validated.key)};
    });
    this.entries.clear();
    for (const {entry, key} of validatedEntries) {
      this.entries.set(key, entry);
      const suffix = Number(entry.id.replace("mem-", ""));
      if (Number.isFinite(suffix)) this.nextId = Math.max(this.nextId, suffix + 1);
    }
    this.trim();
  }

  byKind(kind: MemoryKind): readonly MemoryEntry[] {
    return [...this.entries.values()].filter((entry) => entry.kind === kind);
  }

  private trim(): void {
    if (this.entries.size <= this.limit) return;
    const oldest = [...this.entries.entries()]
      .sort((left, right) => left[1].updatedAt.localeCompare(right[1].updatedAt))
      .slice(0, this.entries.size - this.limit);
    for (const [key] of oldest) this.entries.delete(key);
  }
}

/** Memória local validada e persistida atomicamente em um arquivo JSON. */
export class FileTaskMemory extends LocalTaskMemory {
  private readonly filePath: string;

  constructor(filePath: string, limit = 1000, clock: Clock = () => new Date().toISOString()) {
    super(limit, clock);
    this.filePath = filePath;
    this.load();
  }

  override async remember(input: MemoryInput): Promise<MemoryEntry> {
    const entry = await super.remember(input);
    this.persist();
    return entry;
  }

  private load(): void {
    try {
      const raw = readFileSync(this.filePath, "utf8");
      this.restore(JSON.parse(raw) as MemorySnapshot);
    } catch (error) {
      if ((error as {code?: string}).code !== "ENOENT") {
        console.error("Memória persistente ignorada; iniciando uma memória vazia:", error);
      }
    }
  }

  private persist(): void {
    try {
      mkdirSync(dirname(this.filePath), {recursive: true, mode: 0o700});
      const temporaryPath = this.filePath + ".tmp-" + process.pid;
      writeFileSync(temporaryPath, JSON.stringify(this.snapshot(), null, 2), {
        encoding: "utf8",
        mode: 0o600,
      });
      renameSync(temporaryPath, this.filePath);
    } catch (error) {
      console.error("Não foi possível persistir a memória local:", error);
    }
  }
}
