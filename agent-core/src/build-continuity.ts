import type {AgentInput} from "./contracts.ts";
import {classifyObjective, isCapabilityQuestion} from "./requirements.ts";

const surface = /\b(?:interface|front[ -]?end|back[ -]?end|api|tela|layout)\b/i;
const interfaceElement = /\b(?:interface|front[ -]?end|tela|layout|bot[oõ]es?|navega[cç][aã]o|menus?|cores?|contraste|tipografia|formul[aá]rio|campos?|filtros?|busca)\b/i;
const technology = /\b(?:rust|typescript|javascript|python|html|css|react|vue|svelte)\b/i;
const reset = /\b(?:outro projeto|novo projeto|do zero|mude de assunto|esque[cç]a|cancele)\b/i;

function clean(text: string): string {
  return text.replace(/\n+Workspace local:[\s\S]*$/i, "")
    .replace(/\n+Arquivo ativo: [^\n]+ · [^\n]+ · \d+ linhas · cursor na linha \d+\.?\s*$/i, "").trim();
}

function refinement(text: string): boolean {
  if (isCapabilityQuestion(text) || /\?/.test(text) || reset.test(text)
  || /\b(?:n[aã]o|sem)\s+(?:quero\s+)?(?:trabalh\w*|continu\w*|prossig\w*|retom\w*|edite|editar|ajuste|ajustar|mude|mudar|melhore|melhorar)\b/i.test(text)
  || /\b(?:n[aã]o|sem)\s+(?:quero|preciso|crie|implemente|altere|interface)\b/i.test(text)) return false;
  if (["debug", "analyze", "research", "operate", "learn"].includes(classifyObjective(text))) return false;
  const interfaceRequest = surface.test(text)
    && /\b(?:agora|isso|esse|essa|adicione|acrescente|precisa|preciso|quero|crie|implemente)\b/i.test(text);
  const interfaceAdjustment = interfaceElement.test(text)
    && /\b(?:ajuste|ajustar|altere|alterar|mude|mudar|modifique|modificar|deixe|deixar|torne|tornar|melhore|melhorar|otimize|otimizar|troque|trocar|adicione|adicionar|remova|remover|acrescente|acrescentar|aumente|aumentar|reduza|reduzir|diminua|diminuir|destaque|destacar)\b/i.test(text);
  const stackChoice = technology.test(text)
    && /\b(?:use|usando|utilize|prefiro|vamos usar|parecem boas op[cç][oõ]es|escolho)\b/i.test(text);
  const projectReference = /\b(?:nela|nele|nisso)\b/i.test(text);
  const explicitProjectWork = projectReference
    && /\b(?:quero|preciso|vamos|trabalh\w*|continu\w*|prossig\w*|retom\w*|edite|editar|ajuste|ajustar|mude|mudar|melhore|melhorar)\b/i.test(text);
  // A self-contained new product request starts its own scope.
  const newProduct = /\b(?:para|de)\s+(?:(?:um|uma|o|a)\s+)?(?:ambiente|sistema|aplicativo|editor|loja|jogos?|blog)\b/i.test(text);
  return (interfaceRequest || interfaceAdjustment || stackChoice || explicitProjectWork) && !newProduct;
}

/** Resolve only a contiguous user-request chain; assistant output is not a requirement. */
export function contextualBuildPrompt(input: AgentInput): string | undefined {
  const current = clean(input.prompt);
  if (input.objective !== "auto" || !refinement(current)) return undefined;
  const users = (input.history ?? []).filter(message => message.role === "user")
    .map(message => clean(message.content)).filter(Boolean);
  if (users.at(-1) === current) users.pop();
  const refinements: string[] = [];
  for (const previous of users.slice(-12).reverse()) {
    if (refinement(previous)) {
      refinements.unshift(previous);
      continue;
    }
    if (classifyObjective(previous) !== "build") return undefined;
    const context = [previous, ...refinements].join("\n\n");
    if (context.length > 10000 || context.length + current.length > 23000) return undefined;
    return "Objetivo e complementos anteriores do usuário:\n" + context
      + "\n\nPedido atual (prevalece em caso de conflito):\n" + current
      + "\n\nMantenha o produto solicitado e respeite a escolha atual de tecnologias. "
      + "Inspecione o projeto antes de alterar arquivos; arquivos existentes não redefinem o produto. "
      + "Integre frontend e backend quando solicitados e explique como iniciar a entrega.";
  }
  return undefined;
}
