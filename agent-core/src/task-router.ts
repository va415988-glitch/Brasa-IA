import type {BrainObjective} from "./brain-contracts.ts";
import {operationalPolicyFor, runtimeCapabilities, type RuntimeToolName} from "./capability-registry.ts";
import type {PackageEcosystem, ResearchSourceName} from "./contracts.ts";
import {personalityLayersFor, type PersonalityMode} from "./personality.ts";
import {researchQueryFromPrompt, searchFreshnessFromPrompt, type SearchFreshness} from "./research-query.ts";
import {asksForCurrentInformation, classifyObjective, isCreativeWritingRequest} from "./requirements.ts";

/** Cérebro que deve conduzir o pedido; deriva das mesmas regras do AgentCore. */
export type TaskBrain = "conversation" | "creative" | "engineering" | "interface" | "research"
  | "analysis" | "computation" | "learning" | "operations";

export interface TaskRouteInput {
  prompt: string;
  workspaceSelected?: boolean;
}

export interface PackageLookupRoute {
  name: string;
  ecosystems: PackageEcosystem[];
}

export interface ResearchRoute {
  /** required: a tarefa depende de fontes; recommended: o planejador deve consultar; none: não há lacuna externa. */
  mode: "required" | "recommended" | "none";
  reason: string;
  query?: string;
  freshness?: SearchFreshness;
  sources: ResearchSourceName[];
  package?: PackageLookupRoute;
  language: "pt" | "en";
}

export interface CreativeRoute {
  profile: "focused" | "balanced" | "divergent";
  variations: boolean;
  constraints: {forbidden: string[]; required: string[]};
}

export interface TaskRoute {
  schema: "task-route/v1";
  objective: BrainObjective;
  brain: TaskBrain;
  personalityMode: PersonalityMode;
  confidence: number;
  signals: string[];
  research: ResearchRoute;
  creative?: CreativeRoute;
  tools: {
    mode: "conversation" | "read_only" | "mutating" | "operation";
    allowed: RuntimeToolName[];
    maxActions: number;
    requiresWorkspace: boolean;
    writesPossible: boolean;
    approvalRequiredForWrites: true;
  };
  nextStep: string;
}

function fold(value: string): string {
  return value.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

// Linguagens, runtimes e sistemas não são pacotes de registro; a versão deles
// vem de páginas oficiais pela busca web.
const notPackages = new Set(["python", "python3", "node", "node.js", "nodejs", "rust", "java", "go", "golang", "php",
  "ruby", "kotlin", "swift", "c", "c++", "c#", "dotnet", ".net", "linux", "windows", "ubuntu", "debian", "android",
  "ios", "macos", "chrome", "firefox", "docker", "kubernetes", "postgres", "postgresql", "mysql", "sqlite", "o", "a",
  "um", "uma", "seu", "sua", "meu", "minha", "projeto", "sistema", "app", "aplicativo"]);
const knownEcosystems: Record<string, PackageEcosystem[]> = {
  react: ["npm"], "react-dom": ["npm"], vue: ["npm"], next: ["npm"], svelte: ["npm"], vite: ["npm"],
  express: ["npm"], typescript: ["npm"], zod: ["npm"], tailwindcss: ["npm"], "@angular/core": ["npm"],
  webpack: ["npm"], eslint: ["npm"], prettier: ["npm"], jest: ["npm"], vitest: ["npm"], axios: ["npm"],
  django: ["pypi"], flask: ["pypi"], fastapi: ["pypi"], requests: ["pypi"], numpy: ["pypi"], pandas: ["pypi"],
  torch: ["pypi"], pytorch: ["pypi"], transformers: ["pypi"], pydantic: ["pypi"], pytest: ["pypi"],
  "scikit-learn": ["pypi"], sqlalchemy: ["pypi"], safetensors: ["pypi"], pillow: ["pypi"],
  serde: ["crates"], tokio: ["crates"], axum: ["crates"], reqwest: ["crates"], clap: ["crates"],
  actix: ["crates"], "actix-web": ["crates"], sqlx: ["crates"], anyhow: ["crates"], thiserror: ["crates"],
};
const packageAliases: Record<string, string> = {pytorch: "torch", "next.js": "next", nextjs: "next", "vue.js": "vue",
  "react.js": "react", reactjs: "react", angular: "@angular/core", tailwind: "tailwindcss", "express.js": "express"};

/** Pergunta de versão/lançamento sobre um pacote nomeado, com o ecossistema provável. */
export function packageLookupFromPrompt(prompt: string): PackageLookupRoute | undefined {
  const text = fold(prompt);
  const name = "([@a-z0-9][a-z0-9._@/-]*)";
  const owner = "(?:do|da|de|dos|das)\\s+(?:pacote\\s+|biblioteca\\s+|lib\\s+|crate\\s+|framework\\s+|modulo\\s+)?";
  const patterns = [
    new RegExp("\\bvers(?:ao|oes)\\s+(?:mais\\s+(?:nova|recente|atual|estavel)\\s+|atual\\s+|estavel\\s+|recente\\s+|nova\\s+)?" + owner + name),
    new RegExp("\\bultim[ao]s?\\s+(?:vers(?:ao|oes)|release|lancamento)\\s+(?:estavel\\s+)?" + owner + name),
    new RegExp("\\b(?:changelog|release notes|novidades)\\s+" + owner + name),
    new RegExp("\\b" + name + "\\s+(?:latest|ultima\\s+versao|versao\\s+mais\\s+recente)\\b"),
  ];
  let candidate: string | undefined;
  for (const pattern of patterns) {
    const match = pattern.exec(text);
    if (match?.[1]) {
      candidate = match[1].replace(/[.\-/@]+$/, "");
      break;
    }
  }
  if (!candidate || candidate.length < 2 || notPackages.has(candidate) || /^\d/.test(candidate)) return undefined;
  candidate = packageAliases[candidate] ?? candidate;
  const hinted: PackageEcosystem[] = [];
  if (/\b(?:npm|node|javascript|js|typescript|ts|frontend)\b/.test(text)) hinted.push("npm");
  if (/\b(?:pip|pypi|python)\b/.test(text)) hinted.push("pypi");
  if (/\b(?:cargo|crates?|rust)\b/.test(text)) hinted.push("crates");
  const ecosystems = knownEcosystems[candidate] ?? (hinted.length ? hinted : ["npm", "pypi"]);
  return {name: candidate, ecosystems: [...new Set(ecosystems)].slice(0, 3)};
}

const conceptQuestion = /^\s*(?:o\s+que\s+(?:e|sao|significa)|quem\s+(?:e|foi|era|sao|foram)|defina|definicao\s+de|qual\s+(?:e\s+)?a\s+(?:origem|historia)|me\s+fale\s+sobre|explique\s+o\s+que\s+e)\b/;
const repositoryDiscovery = /\b(?:bibliotecas?|libs?|frameworks?|ferramentas?|projetos?\s+open[\s-]?source|repositorios?|alternativas?)\s+(?:para|pra|de|em|ao|a)\b|\bno\s+github\b/;
const explicitWeb = /\b(?:na internet|pela internet|na web|pela web|pesquise|pesquisar|busque online|buscar online|procure na internet|fontes? (?:atuais|recentes|oficiais)|documentacao oficial)\b/;
const arithmetic = /^\s*(?:calcule|avalie a expressao|quanto (?:e|da)|qual o resultado de)\s*:?\s*[-+(\d]/;

function researchRoute(prompt: string, objective: BrainObjective, brain: TaskBrain): ResearchRoute {
  const text = fold(prompt);
  const language: "pt" | "en" = /\b(?:the|what|how|which|latest|is|does)\b/.test(text)
    && !/\b(?:que|qual|quais|como|para|uma?|da|das|dos|nao|voce|sobre|versao|mais|existe)\b/.test(text) ? "en" : "pt";
  const offline = /\b(?:sem internet|offline|sem pesquisa|nao pesquise|sem consultar a web)\b/.test(text);
  if (offline || brain === "creative" || brain === "computation") {
    return {mode: "none", reason: offline ? "o pedido proíbe consulta externa" : "a entrega não depende de fatos externos",
      sources: [], language};
  }
  const packageLookup = packageLookupFromPrompt(prompt);
  const current = asksForCurrentInformation(prompt);
  const concept = conceptQuestion.test(text);
  const discovery = repositoryDiscovery.test(text) && /\?|\b(?:qual|quais|recomend|sugira|indique|compare|existe)\w*/.test(text);
  const sources: ResearchSourceName[] = [];
  if (packageLookup) sources.push("package-registry");
  if (concept && !current) sources.push("wikipedia");
  if (discovery) sources.push("github");
  sources.push("web");
  const query = researchQueryFromPrompt(prompt);
  const freshness = searchFreshnessFromPrompt(prompt);
  const base = {query, ...(freshness ? {freshness} : {}), sources,
    ...(packageLookup ? {package: packageLookup} : {}), language};
  if (objective === "research" || explicitWeb.test(text) || packageLookup || current) {
    const reason = packageLookup ? "versão ou lançamento de pacote: consultar o registro oficial"
      : current ? "fato volátil: exige fonte atual"
        : objective === "research" ? "pedido de pesquisa" : "a pessoa pediu fontes externas";
    return {mode: "required", reason, ...base};
  }
  if ((objective === "conversation" && (concept || discovery)) || objective === "learn") {
    return {mode: "recommended", reason: concept ? "conceito: o acervo local pode ser complementado por fonte enciclopédica"
      : objective === "learn" ? "aprendizado de tema novo" : "descoberta de bibliotecas ou projetos", ...base};
  }
  return {mode: "none", reason: "o pedido pode ser resolvido com contexto local", sources: [], language};
}

function creativeRoute(prompt: string): CreativeRoute {
  const text = fold(prompt);
  const words = new Set(text.match(/[a-z0-9]+/g) ?? []);
  const has = (...items: string[]) => items.some((item) => words.has(item));
  const profile = has("brainstorm", "divergencia", "divergentes", "disruptivas", "radicais") ? "divergent"
    : has("reescreva", "revise", "ajuste", "edite", "preserve") ? "focused" : "balanced";
  const firstWord = (fragment: string) => fold(fragment).match(/[a-z0-9]+/)?.[0];
  const forbidden = [...prompt.matchAll(/\bsem\s+([\p{L}\d-]+)/giu)].map((match) => firstWord(match[1]!)).filter(Boolean) as string[];
  const required = [...prompt.matchAll(/\b(?:com|inclua|incluindo)\s+([\p{L}\d-]+)/giu)].map((match) => firstWord(match[1]!)).filter(Boolean) as string[];
  return {profile, variations: has("ideias", "alternativas", "opcoes", "conceitos", "brainstorm", "variacoes", "sugestoes"),
    constraints: {forbidden, required}};
}

function brainFor(prompt: string, objective: BrainObjective, mode: PersonalityMode): TaskBrain {
  const text = fold(prompt);
  if (objective === "build" || objective === "debug") return mode === "interface" ? "interface" : "engineering";
  if (objective === "testing") return "engineering";
  if (objective === "analyze") return "analysis";
  if (objective === "research") return "research";
  if (objective === "learn") return "learning";
  if (objective === "operate") return /\bem\s+[\w./-]+\.py\b/.test(text) ? "computation" : "operations";
  if (arithmetic.test(text)) return "computation";
  if (mode === "creative" || isCreativeWritingRequest(prompt)) return "creative";
  return "conversation";
}

const nextSteps: Record<TaskBrain, string> = {
  conversation: "Responder diretamente; consultar fontes somente se faltar informação.",
  creative: "Montar o briefing (público, tom, formato, restrições) e gerar a peça no motor criativo.",
  engineering: "Inspecionar o workspace, planejar a menor mudança integrada, aplicar com aprovação e verificar.",
  interface: "Definir direção visual e estados da interface, conectar aos dados reais e verificar.",
  research: "Consultar as fontes indicadas e sintetizar com citações, separando fato e inferência.",
  analysis: "Ler os arquivos centrais e relatar fatos observados, inferências e lacunas.",
  computation: "Executar o cálculo no motor determinístico local e devolver o resultado verificado.",
  learning: "Pesquisar o tema, registrar fontes no acervo local e medir a competência por avaliação.",
  operations: "Preparar a ação e pedir autorização antes de qualquer efeito externo.",
};

/**
 * Roteamento unificado de recursos: um pedido em linguagem natural vira o
 * cérebro responsável, a personalidade, as ferramentas permitidas e as
 * fontes de pesquisa. Não executa nada; o AgentCore continua aplicando
 * política, aprovação e verificação.
 */
export function routeTask(input: TaskRouteInput): TaskRoute {
  const prompt = input.prompt.trim();
  if (!prompt) throw new Error("O pedido não pode ser vazio.");
  if (prompt.length > 24000) throw new Error("O pedido excede o limite de 24000 caracteres.");
  const objective = classifyObjective(prompt);
  const personality = personalityLayersFor(prompt, objective);
  const brain = brainFor(prompt, objective, personality.mode);
  const policy = operationalPolicyFor({prompt, objective,
    ...(input.workspaceSelected ? {workspaceRoot: "workspace-selecionado"} : {})} as Parameters<typeof operationalPolicyFor>[0]);
  const research = researchRoute(prompt, objective, brain);
  const signals = [`objetivo=${objective}`, `personalidade=${personality.mode}`, `cérebro=${brain}`];
  if (research.mode !== "none") signals.push(`pesquisa=${research.mode}: ${research.reason}`);
  if (research.package) signals.push(`pacote=${research.package.name} (${research.package.ecosystems.join(", ")})`);
  const writesPossible = policy.allowedTools.some((tool) => runtimeCapabilities[tool].effect === "workspace_write");
  const requiresWorkspace = ["build", "debug", "analyze", "testing"].includes(objective)
    || (objective === "operate" && brain !== "computation");
  if (requiresWorkspace && !input.workspaceSelected) signals.push("workspace ainda não selecionado");
  const explicit = signals.length > 3 || brain !== "conversation";
  return {
    schema: "task-route/v1",
    objective,
    brain,
    personalityMode: personality.mode,
    confidence: explicit ? 0.86 : 0.7,
    signals,
    research,
    ...(brain === "creative" ? {creative: creativeRoute(prompt)} : {}),
    tools: {
      mode: policy.mode,
      allowed: [...policy.allowedTools],
      maxActions: policy.maxActions,
      requiresWorkspace,
      writesPossible,
      approvalRequiredForWrites: true,
    },
    nextStep: requiresWorkspace && !input.workspaceSelected
      ? "Selecionar um workspace antes de inspecionar ou alterar arquivos."
      : nextSteps[brain],
  };
}

/** Envelope público em snake_case, no mesmo estilo dos demais contratos HTTP. */
export function taskRouteResponse(route: TaskRoute): Record<string, unknown> {
  return {
    schema: route.schema,
    objective: route.objective,
    brain: route.brain,
    personality_mode: route.personalityMode,
    confidence: route.confidence,
    signals: route.signals,
    research: {
      mode: route.research.mode,
      reason: route.research.reason,
      ...(route.research.query ? {query: route.research.query} : {}),
      ...(route.research.freshness ? {freshness: route.research.freshness} : {}),
      sources: route.research.sources,
      ...(route.research.package ? {package: route.research.package} : {}),
      language: route.research.language,
    },
    ...(route.creative ? {creative: route.creative} : {}),
    tools: {
      mode: route.tools.mode,
      allowed: route.tools.allowed,
      max_actions: route.tools.maxActions,
      requires_workspace: route.tools.requiresWorkspace,
      writes_possible: route.tools.writesPossible,
      approval_required_for_writes: route.tools.approvalRequiredForWrites,
    },
    next_step: route.nextStep,
    execution_allowed: false,
  };
}
