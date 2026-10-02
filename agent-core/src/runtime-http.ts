import type {
  AgentPorts,
  ApprovalPort,
  Artifact,
  Evidence,
  ProjectInspection,
  ResearchPort,
  RuntimeToolArguments,
  RuntimeToolName,
  RuntimeToolResponse,
  RuntimeToolsPort,
  Verification,
  WorkspacePort,
} from "./contracts.ts";
import {validateWorkspaceArtifact} from "./scope.ts";
import {runtimeCapabilities} from "./capability-registry.ts";

interface RuntimeResponse {
  ok?: boolean;
  tool?: string;
  data?: unknown;
  error?: string;
}

export interface HttpResponse {
  ok: boolean;
  json(): Promise<unknown>;
  body?: {
    getReader(): {
      read(): Promise<{done: boolean; value?: Uint8Array}>;
    };
  } | null;
}

export type Fetcher = (input: string, init?: {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
  signal?: AbortSignal;
  redirect?: "error";
}) => Promise<HttpResponse>;

export interface RuntimeHttpOptions {
  baseUrl: string;
  workspaceRoot: string;
  fetcher?: Fetcher;
}

function requireData(response: RuntimeResponse): unknown {
  if (response.ok !== true) {
    throw new Error(response.error ?? "O runtime rejeitou a chamada.");
  }
  return response.data;
}

export class RuntimeHttpPorts implements AgentPorts {
  readonly workspace: WorkspacePort;
  readonly tools: RuntimeToolsPort;
  readonly research: ResearchPort;
  readonly approval: ApprovalPort;
  private readonly fetcher: Fetcher;
  private readonly options: RuntimeHttpOptions;
  private requestNumber = 0;

  constructor(options: RuntimeHttpOptions, approval: ApprovalPort) {
    this.options = options;
    this.fetcher = options.fetcher ?? (globalThis.fetch as unknown as Fetcher);
    this.approval = approval;
    this.tools = {
      call: (name, args) => this.callTool(name, args),
      listAvailable: () => this.listAvailable(),
    };
    this.workspace = {
      select: (root) => this.select(root),
      inspect: () => this.inspect(),
      write: (artifact) => this.write(artifact),
      verify: () => this.verify(),
    };
    this.research = {
      research: (query) => this.researchWeb(query),
    };
  }

  private async request(path: string, body: Record<string, unknown>, signal?: AbortSignal): Promise<RuntimeResponse> {
    const response = await this.fetcher(this.options.baseUrl + path, {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({...body, request_id: "agent-core-" + (++this.requestNumber)}),
      signal,
    });
    const payload = await response.json() as RuntimeResponse;
    if (!response.ok) {
      throw new Error(payload.error ?? "O runtime respondeu com erro HTTP.");
    }
    return payload;
  }

  private async tool<Name extends RuntimeToolName>(
    name: Name,
    args: RuntimeToolArguments[Name],
  ): Promise<unknown> {
    return requireData(await this.request("/api/v1/tools/call", {tool: name, arguments: args}));
  }

  private async callTool(
    name: RuntimeToolName,
    args: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<RuntimeToolResponse> {
    const normalized = {...args};
    for (const key of ["path", "output"]) {
      const value = normalized[key];
      if (typeof value === "string" && value.length > 0
          && !(name === "create_workspace" && key === "path")) {
        normalized[key] = validateWorkspaceArtifact({path: value, content: "", language: "text"}).path;
      }
    }
    const response = await this.request("/api/v1/tools/call", {tool: name, arguments: normalized}, signal);
    return {
      ok: response.ok === true,
      tool: name,
      data: response.data,
      error: response.error,
    };
  }

  private async listAvailable(): Promise<RuntimeToolName[]> {
    const response = await this.callTool("list_tools", {});
    if (!response.ok) throw new Error(response.error ?? "Não foi possível consultar as ferramentas do runtime.");
    const data = response.data as {tools?: unknown} | undefined;
    if (!data || !Array.isArray(data.tools)) {
      throw new Error("O runtime retornou um catálogo de ferramentas inválido.");
    }
    const known = new Set(Object.keys(runtimeCapabilities));
    const names = new Set<RuntimeToolName>();
    for (const item of data.tools) {
      if (!item || typeof item !== "object" || typeof (item as {name?: unknown}).name !== "string") continue;
      const name = (item as {name: string}).name;
      if (known.has(name)) names.add(name as RuntimeToolName);
    }
    return [...names];
  }

  private async select(root?: string): Promise<string> {
    const selected = (root ?? this.options.workspaceRoot).trim();
    if (!selected) throw new Error("workspaceRoot é obrigatório para selecionar o workspace.");
    const data = await this.tool("set_workspace", {path: selected}) as {workspace?: string; selected?: boolean};
    if (data.selected !== true) throw new Error("O runtime não confirmou o workspace selecionado.");
    return data.workspace ?? selected;
  }

  private async inspect(): Promise<ProjectInspection> {
    const data = await this.tool("inspect_project", {max_depth: 4}) as Record<string, unknown>;
    const paths = (value: unknown): string[] => Array.isArray(value)
      ? value.flatMap((item) => {
        if (typeof item === "string") return [item];
        if (item && typeof item === "object") {
          const path = (item as Record<string, unknown>).path;
          return typeof path === "string" ? [path] : [];
        }
        return [];
      })
      : [];
    return {
      workspace: String(data.workspace ?? this.options.workspaceRoot),
      files: paths(data.files),
      directories: paths(data.directories),
      truncated: data.truncated === true,
      manifests: paths(data.manifests),
      testFiles: paths(data.test_files),
      entrypoints: paths(data.entrypoints),
    };
  }

  private async write(artifact: Artifact): Promise<Artifact> {
    const scoped = validateWorkspaceArtifact(artifact);
    await this.tool("create_file", {path: scoped.path, content: scoped.content});
    return scoped;
  }

  private async verify(): Promise<Verification> {
    const data = await this.tool("project_checks", {check: "auto"}) as Record<string, unknown>;
    return {
      passed: data.passed === true,
      executed: data.executed === true,
      ...(typeof data.verification_kind === "string" ? {kind: data.verification_kind as Verification["kind"]} : {}),
      ...(typeof data.tests_executed === "number" ? {testsExecuted: data.tests_executed} : {}),
      ...(typeof data.behavior_verification_executed === "boolean" ? {behaviorExecuted: data.behavior_verification_executed} : {}),
      check: String(data.check ?? data.command ?? "auto"),
      stdout: typeof data.stdout === "string" ? data.stdout : undefined,
      stderr: typeof data.stderr === "string" ? data.stderr : undefined,
      summary: String(data.summary ?? data.message ?? "Verificação concluída."),
      evidence: Array.isArray(data.evidence) ? data.evidence.map(String) : [],
    };
  }

  private async researchWeb(query: string): Promise<readonly Evidence[]> {
    const response = await this.request("/api/v1/research", {
      query,
      max_results: 3,
      save_to_corpus: false,
      category: "proactive-learning",
    });
    const data = requireData(response) as {pages?: Array<Record<string, unknown>>};
    return (data.pages ?? []).flatMap((page) => {
      const title = String(page.title ?? "Fonte sem título");
      const url = String(page.url ?? "");
      const excerpt = String(page.text ?? "");
      return url && excerpt ? [{title, url, excerpt, sourceId: String(page.source_id ?? "")}] : [];
    });
  }
}
