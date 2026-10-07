import type {BrainObjective, BrainRequest, RiskLevel} from "./brain-contracts.ts";
import {repairRequested, testCreationRequested} from "./requirements.ts";

export type CapabilityGroup = "context" | "read" | "compute" | "research" | "checks" | "process" | "mutation";
export type CapabilityEffect = "none" | "network_read" | "workspace_write" | "process_control";
export type RetryStrategy = "safe_read" | "inspect_before_retry" | "never_automatically";

interface CapabilityDefinition {
  name: string;
  group: CapabilityGroup;
  description: string;
  risk: RiskLevel;
  reversible: boolean;
  effect: CapabilityEffect;
  retry: RetryStrategy;
}

const capabilityDefinitions = [
  {name: "set_workspace", group: "context", description: "Selecionar a raiz autorizada do workspace.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "create_workspace", group: "mutation", description: "Criar e selecionar um novo diretório de projeto.", risk: "high", reversible: false, effect: "workspace_write", retry: "inspect_before_retry"},
  {name: "inspect_project", group: "read", description: "Inventariar estrutura, manifestos, entradas e testes do projeto.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "inspect_code", group: "read", description: "Inspecionar definições de código e imports no workspace.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "code_references", group: "read", description: "Encontrar definição, imports, chamadas e testes que citam um símbolo.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "change_impact", group: "read", description: "Estimar dependentes, testes afetados e risco antes de alterar um arquivo ou símbolo.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "discover_tests", group: "read", description: "Descobrir frameworks, arquivos de teste, checks e fontes sem teste.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "security_scan", group: "read", description: "Revisar segurança por análise estática, com segredos mascarados.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "dependency_audit", group: "read", description: "Auditar manifests, fixação de versões, lockfiles e origens das dependências.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "calculate", group: "compute", description: "Calcular expressão nova com variáveis JSON em um interpretador limitado.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "evaluate_function", group: "read", description: "Avaliar função Python pura com entradas novas sem importar seu módulo.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "list_tools", group: "read", description: "Listar ferramentas que o runtime local disponibiliza.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "list_files", group: "read", description: "Listar arquivos em um caminho do workspace.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "path_info", group: "read", description: "Consultar existência, tipo e tamanho de um caminho.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "find_paths", group: "read", description: "Encontrar caminhos por padrão de nome dentro do workspace.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "list_tree", group: "read", description: "Listar uma árvore local com limites de profundidade e tamanho.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "compare_files", group: "read", description: "Comparar dois arquivos textuais do workspace.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "git_diff", group: "read", description: "Ler o diff Git não preparado do workspace.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "read_file", group: "read", description: "Ler conteúdo de um arquivo do workspace.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "extract_document_text", group: "read", description: "Extrair texto de um documento local.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "inspect_media", group: "read", description: "Extrair metadados, texto e OCR local disponível, informando limites de áudio e vídeo.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "search_files", group: "read", description: "Pesquisar termos ou símbolos nos arquivos do workspace.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "diagnose_project", group: "read", description: "Classificar a saída de uma verificação e sugerir próximos passos.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "search_web", group: "research", description: "Pesquisar informações atuais na internet.", risk: "low", reversible: true, effect: "network_read", retry: "safe_read"},
  {name: "open_page", group: "research", description: "Abrir uma página HTTP(S) e extrair seu texto.", risk: "low", reversible: true, effect: "network_read", retry: "safe_read"},
  {name: "list_sources", group: "research", description: "Listar fontes coletadas na sessão atual.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "cite_sources", group: "research", description: "Gerar citações para fontes coletadas na sessão atual.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "research_web", group: "research", description: "Consultar fontes públicas na web.", risk: "low", reversible: true, effect: "network_read", retry: "safe_read"},
  {name: "project_checks", group: "checks", description: "Descobrir ou executar verificações reconhecidas do projeto.", risk: "medium", reversible: false, effect: "process_control", retry: "inspect_before_retry"},
  {name: "terminal_run", group: "checks", description: "Executar uma operação de terminal permitida pelo runtime.", risk: "medium", reversible: false, effect: "process_control", retry: "inspect_before_retry"},
  {name: "process_start", group: "process", description: "Iniciar o perfil de processo local permitido.", risk: "high", reversible: false, effect: "process_control", retry: "inspect_before_retry"},
  {name: "process_status", group: "process", description: "Observar o estado e as saídas de um processo local.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "process_stop", group: "process", description: "Parar um processo local identificado.", risk: "medium", reversible: false, effect: "process_control", retry: "inspect_before_retry"},
  {name: "apply_batch", group: "mutation", description: "Aplicar um lote de operações locais no workspace.", risk: "high", reversible: false, effect: "workspace_write", retry: "inspect_before_retry"},
  {name: "undo_batch", group: "mutation", description: "Reverter uma transação local identificada.", risk: "high", reversible: false, effect: "workspace_write", retry: "inspect_before_retry"},
  {name: "create_file", group: "mutation", description: "Criar um arquivo no workspace.", risk: "medium", reversible: false, effect: "workspace_write", retry: "inspect_before_retry"},
  {name: "create_web_page", group: "mutation", description: "Criar uma página HTML local a partir de um briefing.", risk: "medium", reversible: false, effect: "workspace_write", retry: "inspect_before_retry"},
  {name: "edit_file", group: "mutation", description: "Alterar um trecho correspondente em arquivo do workspace.", risk: "medium", reversible: false, effect: "workspace_write", retry: "inspect_before_retry"},
  {name: "propose_repair", group: "mutation", description: "Validar e propor uma alteração localizada antes de aplicá-la.", risk: "low", reversible: true, effect: "none", retry: "safe_read"},
  {name: "apply_repair", group: "mutation", description: "Aplicar uma correção localizada previamente validada.", risk: "medium", reversible: false, effect: "workspace_write", retry: "inspect_before_retry"},
  {name: "create_directory", group: "mutation", description: "Criar um diretório no workspace.", risk: "medium", reversible: false, effect: "workspace_write", retry: "inspect_before_retry"},
] as const satisfies readonly CapabilityDefinition[];

export type RuntimeToolName = typeof capabilityDefinitions[number]["name"];

export interface RuntimeCapability extends CapabilityDefinition {
  name: RuntimeToolName;
}

const capabilityMap = Object.fromEntries(capabilityDefinitions.map((item) => [item.name, item])) as Record<RuntimeToolName, RuntimeCapability>;

/**
 * Registro canônico do AgentCore. O nome da ferramenta, a política de objetivo,
 * o risco do executor e os efeitos derivam da mesma definição. O schema de
 * argumentos é validado na fronteira do runtime.
 */
export const runtimeCapabilities: Readonly<Record<RuntimeToolName, RuntimeCapability>> = capabilityMap;

export interface OperationalPolicy {
  mode: "conversation" | "read_only" | "mutating" | "operation";
  allowedTools: readonly RuntimeToolName[];
  acceptance: readonly string[];
  maxActions: number;
}

export function capabilityFor(tool: RuntimeToolName): RuntimeCapability {
  if (!Object.hasOwn(runtimeCapabilities, tool)) {
    throw new Error("Ferramenta sem contrato no registro canônico: " + String(tool));
  }
  return runtimeCapabilities[tool];
}

function toolsIn(groups: readonly CapabilityGroup[]): RuntimeToolName[] {
  return (Object.values(runtimeCapabilities) as RuntimeCapability[])
    .filter((capability) => groups.includes(capability.group) && capability.group !== "context")
    .map((capability) => capability.name);
}

export function operationalPolicyFor(request: BrainRequest): OperationalPolicy {
  const mutationRequested = request.objective === "build"
    || (request.objective === "debug" && repairRequested(request.prompt))
    || (request.objective === "testing" && testCreationRequested(request.prompt));
  const read = toolsIn(["read"]);
  const compute = toolsIn(["compute"]);
  const checks = toolsIn(["checks"]);
  const research = toolsIn(["research"]);
  const process = toolsIn(["process"]);
  const mutation = toolsIn(["mutation"]);
  const commonExecution = [...read, ...compute, ...research, ...checks, ...process, ...mutation];

  let allowedTools: RuntimeToolName[];
  switch (request.objective) {
    case "conversation":
      allowedTools = [...(request.workspaceRoot ? read : []), ...compute, ...research];
      break;
    case "analyze":
      allowedTools = [...read, ...compute];
      break;
    case "research":
      allowedTools = [...(request.workspaceRoot ? read : []), ...compute, ...research];
      break;
    case "testing":
      allowedTools = mutationRequested ? [...read, ...compute, ...checks, ...research, ...mutation] : [...read, ...compute, ...checks];
      break;
    case "learn":
      allowedTools = [...(request.workspaceRoot ? read : []), ...compute, ...research];
      break;
    case "operate":
      allowedTools = [...read, ...compute, ...checks, ...process];
      break;
    case "debug":
      allowedTools = mutationRequested ? commonExecution : [...read, ...compute, ...checks, ...research];
      break;
    case "build":
      allowedTools = commonExecution;
      break;
  }

  const acceptance: Record<BrainObjective, readonly string[]> = {
    conversation: ["Responder ao turno atual com evidências quando necessário; distinguir fatos e incertezas."],
    research: ["Reunir ao menos uma fonte com URL e trecho de evidência."],
    analyze: ["Ler ao menos um arquivo selecionado e citar o caminho na síntese."],
    build: ["Realizar alteração observável e executar uma verificação aprovada."],
    debug: mutationRequested
      ? ["Realizar correção observável e executar uma verificação aprovada."]
      : ["Observar evidência diagnóstica e explicar fatos e incertezas."],
    testing: mutationRequested
      ? ["Criar ou alterar testes pertinentes e executar uma verificação da versão atual."]
      : ["Executar um check do projeto e observar seu resultado."],
    learn: ["Usar evidência local ou fonte verificável; não declarar competência dominada."],
    operate: ["Executar e observar uma ação operacional permitida."],
  };

  return {
    mode: request.objective === "conversation" ? "conversation"
      : request.objective === "operate" ? "operation"
        : mutationRequested ? "mutating" : "read_only",
    allowedTools,
    acceptance: acceptance[request.objective],
    maxActions: request.objective === "conversation" ? 6 : 12,
  };
}

/** Alternative routes use registered tools and retain the task's authority boundary. */
export function recoveryRoutes(allowedTools: readonly string[]) {
  const allowed = new Set(allowedTools);
  const routes: Array<{need: string; tools: RuntimeToolName[]; guidance: string}> = [
    {need: "Localizar contexto", tools: ["find_paths", "search_files", "path_info", "read_file"],
      guidance: "Encontre o caminho ou símbolo antes de inferir que o conteúdo não existe."},
    {need: "Entender arquitetura", tools: ["inspect_project", "inspect_code", "read_file"],
      guidance: "Combine estrutura, definições e conteúdo para identificar uma mudança pequena."},
    {need: "Calcular e avaliar código puro", tools: ["calculate", "evaluate_function"],
      guidance: "Calcule expressões ou avalie uma função Python do projeto com argumentos concretos. O motor AST suporta um subconjunto de Python e uma entrada; isso não substitui a execução do módulo nem a suíte de testes."},
    {need: "Implementar sem gerador especializado", tools: ["create_file", "edit_file", "apply_batch"],
      guidance: "Se conseguir formular conteúdo concreto fundamentado nas leituras, use as ferramentas de arquivo. Não invente conteúdo nem sobrescreva arquivos existentes."},
    {need: "Corrigir falhas", tools: ["diagnose_project", "search_files", "read_file", "propose_repair", "apply_repair"],
      guidance: "Use a primeira falha observada para localizar a causa e propor uma correção localizada."},
    {need: "Verificar resultado", tools: ["project_checks", "git_diff", "compare_files"],
      guidance: "Descubra checks disponíveis. Diffs comprovam alterações, mas não substituem testes executados."},
  ];
  return routes.map(route => ({...route, tools: route.tools.filter(tool => allowed.has(tool))}))
    .filter(route => route.tools.length > 0);
}
