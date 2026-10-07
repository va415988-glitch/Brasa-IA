import type {
  AcceptanceCriterion,
  BrainObjective,
  Confidence,
  RequirementAnalysis,
  RequirementConstraint,
  RequirementInterpretation,
} from "./brain-contracts.ts";

const vaguePatterns = [
  /\balgo\b/i,
  /\buma coisa\b/i,
  /\bqualquer coisa\b/i,
  /\bmelhore\b/i,
  /\bfaça um sistema\b/i,
  /\bcrie um aplicativo\b/i,
  /\bcriar um aplicativo\b/i,
  /\bconstrua um sistema\b/i,
];

const buildPatterns = [
  /\bcrie\b/i,
  /\bconstrua\b/i,
  /\bimplemente\b/i,
  /\bdesenvolva\b/i,
  /\bfaça\b/i,
  /\brefatore\b/i,
  /\bdebug\b/i,
  /\bdepure\b/i,
];

const technicalAnchors = [
  /\bpython\b/i,
  /\btypescript\b/i,
  /\bjavascript\b/i,
  /\brust\b/i,
  /\bjava\b/i,
  /\bgo\b/i,
  /\b3\.\d+\b/i,
  /\bapi\b/i,
  /\bsqlite\b/i,
  /\bpostgres(?:ql)?\b/i,
  /\bsem biblioteca\b/i,
  /\boffline\b/i,
  /\bweb\b/i,
  /\bcli\b/i,
];

const domainAnchors = [
  /\bfunção\b/i,
  /\bmódulo\b/i,
  /\bserviço\b/i,
  /\bsite\b/i,
  /\bscript\b/i,
  /\bassistente\b/i,
  /\bmodelo\b/i,
  /\bdataset\b/i,
  /\bprojeto\b/i,
  /\b(?:sistema|aplicativo|aplicação|app|produto|plataforma|interface|tela|página|pagina|site|dashboard|portal)\b/i,
];

const productPurpose = /\b(?:para|pra|que permita|que ajude a)\s+(?:organizar|acompanhar|registrar|gerenciar|controlar|cadastrar|monitorar|planejar|agendar|vender|calcular|comparar|listar|visualizar|administrar|coordenar|rastrear|medir|armazenar|processar|atender|reservar|entregar|conectar|integrar)\b/i;
const productArtifacts = /\b(?:sistema|aplicativo|aplicação|app|produto|plataforma|interface|tela|dashboard|portal|controle|gerenciador|organizador)\b/i;
const productObjects = /\b(?:tarefas?|afazeres|pedidos?|clientes?|pagamentos?|entregas?|estoque|vendas?|chamados?|agendamentos?|reservas?|gastos?|despesas?)\b/i;
const personalProductScope = /\b(?:para|pra)\s+(?:uma pessoa|uso pessoal|uso individual)\b/i;

const constraintPatterns: Array<{pattern: RegExp; label: string}> = [
  {pattern: /\bsem bibliotecas? externas?\b/i, label: "não usar bibliotecas externas"},
  {pattern: /\bsem internet\b|\boffline\b/i, label: "operar sem internet"},
  {pattern: /\bsem gpu\b|\bsem placa de vídeo\b/i, label: "operar sem GPU"},
  {pattern: /\b(?:python|typescript|javascript|rust|java|go|html|css)\b(?:\s+\d+(?:\.\d+)*)?/i, label: "respeitar as tecnologias declaradas e suas restrições"},
  {pattern: /\bnão (?:pode|podemos|quero)\b/i, label: "respeitar a restrição negativa declarada"},
  {pattern: /\bapenas\b|\bsomente\b/i, label: "respeitar o escopo restritivo declarado"},
];

const objectiveLabels: Array<{objective: BrainObjective; pattern: RegExp}> = [
  {objective: "debug", pattern: /\bdebug|depur|corrija o erro|traceback|(?:investigue|diagnostique|analise)\w*.{0,40}\b(?:falha|erro|bug)\b/i},
  {objective: "testing", pattern: /\b(?:faca|faça|rode|roda|rodar|execut\w*|test\w*|verifique\w*|adicione\w*|crie\w*|escreva\w*|passe)\b.{0,60}\b(?:test\w*|pytest|cargo test|npm test|unittest|build|app|aplicativo|projeto)\b|\b(?:pytest|cargo test|npm test|unittest)\b/i},
  {objective: "learn", pattern: /\btrein|dataset|aprend|ensine\b/i},
  {objective: "analyze", pattern: /\b(?:examin|analise|analisa|inspecion|entenda|resuma|explique|descreva|mapeie|busque|procure|identifique|avalie|veja|liste|listar|mostre|mostrar|leia|ler|abra|abrir)\w*\b.{0,80}\b(?:projeto|workspace|repositorio|repo|arquivos?|codigo|gargalos?|bugs?|problemas?|arquitetura|pastas?|diretorios?|[\w.-]+\.(?:py|ts|tsx|js|jsx|rs|cpp|h|md|json|toml|yaml|yml))\b|\b(?:gargalos?|bugs?|problemas?)\b.{0,60}\b(?:projeto|workspace|repositorio|repo|arquivos?|codigo|encontre|identifique|busque|procure)\b|\b(?:o que (?:e|faz|me diz)|sobre o que|conclusao|resumo|finalidade|objetivo|estado(?: atual)?|situacao(?: atual)?|panorama|como esta|como esta o estado)\b.*\b(?:projeto|workspace|repositorio|repo|sistema|aplicativo)\b/i},
  {objective: "research", pattern: /\bpesquis|compare fontes?|investigue|levantamento\b/i},
  {objective: "operate", pattern: /\bexecute|rode|envie|publique|apague|mova\b/i},
  {objective: "build", pattern: /\bcrie|criar|construa|construir|implemente|implementar|desenvolva|desenvolver|refatore\b/i},
];

/** Perguntas factuais voláteis devem consultar fontes atuais mesmo sem o verbo "pesquisar". */
export function asksForCurrentInformation(prompt: string): boolean {
  const looksLikeQuestion = /^\s*(?:qual|quais|quanto|quantos|quantas|quem|quando|onde|como|est[aá]|existe|h[aá]|o que (?:mudou|h[aá] de novo))\b/i.test(prompt)
    || /^\s*(?:(?:me\s+(?:d[eê]|traga|mostre)\s+(?:as\s+)?)?(?:novidades|not[ií]cias|changelog|release notes|lan[cç]amentos?|últimas))\b/i.test(prompt)
    || /\?/.test(prompt);
  const volatileFact = /\b(?:hoje|agora|atual(?:mente)?|mais recente|recentes?|últim[oa]s?|(?:esta|nesta|nessa|na)\s+semana|(?:este|neste|esse|nesse)\s+m[eê]s|(?:este|neste|esse|nesse)\s+ano|últimos?\s+(?:7|30)\s+dias|últimos?\s+12\s+meses|vers[aã]o|pre[cç]o|cust[oa]|cot[aã]ç[aã]o|lançamento|release|presidente|primeiro-ministro|ceo|clima|previs[aã]o do tempo|agenda|resultado|placar|novidades?|not[ií]cias?|lan[cç]ou|lan[cç]ad[oa]s?|changelog|depreciad[oa]|deprecated|descontinuad[oa]|quem (?:ganhou|venceu)|campe[aã]o|elei[cç][aã]o|d[oó]lar|euro|bitcoin|infla[cç][aã]o|selic)\b/i.test(prompt);
  return looksLikeQuestion && volatileFact;
}

// Formatos de texto autoral. Um pedido com estes formatos e sem um artefato
// de software é escrita/criação no chat, não uma tarefa de build no workspace.
const creativeFormats = /\b(?:poemas?|poesias?|sonetos?|haicais?|haikus?|versos?|rimas?|contos?|cr[oô]nicas?|hist[oó]rias?|narrativas?|f[aá]bulas?|roteiros?|storyboards?|letras?\s+de\s+m[uú]sica|can[cç][aã]o|can[cç][oõ]es|jingles?|slogans?|taglines?|bord[aã]o|campanhas?|an[uú]ncios?|propagandas?|copys?|legendas?|posts?|tweets?|manchetes?|nomes?\s+(?:para|de|pra)|naming|personagens?|enredos?|piadas?|trocadilhos?|discursos?|cartas?|e-?mails?|convites?|brindes?|homenagens?|resenhas?|sinopses?|pitch|manifesto|identidade\s+visual|logotipos?|logos?|mascotes?|brainstorm(?:ing)?|ideias?\s+criativas?)\b/;
const creativeVerbs = /\b(?:crie|criar|escreva|escrever|redija|redigir|invente|inventar|componha|compor|elabore|elaborar|fa[cç]a|fazer|gere|gerar|sugira|sugerir|proponha|propor|me\s+d[eê]|d[eê]-me|preciso\s+de|quero|conte|contar|imagine|imaginar|reescreva|reescrever|melhore|melhorar|revise|revisar)\b/;
const creativeIdeation = /\b(?:ideias?|sugest[oõ]es|op[cç][oõ]es|alternativas)\s+(?:de|para|pra)\s+(?:nomes?|t[ií]tulos?|slogans?|campanhas?|posts?|presentes?|festas?|eventos?|hist[oó]rias?|personagens?|marcas?|neg[oó]cios?|conte[uú]dos?|v[ií]deos?)\b/;
const softwareArtifactContext = /\b(?:apps?|aplicativos?|aplica[cç](?:ao|oes)|sites?|landing\s*pages?|p[aá]ginas?\s+(?:web|html)|api|apis|endpoints?|sistemas?|softwares?|c[oó]digos?|scripts?|fun[cç](?:ao|oes)|classes?|m[oó]dulos?|arquivos?|html|css|componentes?|bancos?\s+de\s+dados|programas?|bots?|cli|frontend|backend|workspace|reposit[oó]rio|projeto\s+(?:ativo|atual)|commit|pull\s+request|readme|docstrings?)\b/;
const nonCreativeScripts = /\broteiros?\s+(?:de\s+)?(?:testes?|estudos?|aprendizado|viagem|instala[cç][aã]o|deploy|migra[cç][aã]o)\b|\bplano\s+de\s+testes?\b|\bmensage(?:m|ns)\s+de\s+(?:commit|erro|log)\b|\bhist[oó]rico\b|\ba\s+hist[oó]ria\s+d(?:o|a|os|as)\b/;

/**
 * Pedido de criação textual (poema, roteiro, campanha, e-mail, nomes...) sem
 * artefato de software. Ele deve seguir pelo cérebro criativo no chat e nunca
 * abrir ferramentas de escrita no workspace.
 */
export function isCreativeWritingRequest(prompt: string): boolean {
  const text = normalizedIntent(prompt);
  if (softwareArtifactContext.test(text) || nonCreativeScripts.test(text)) return false;
  if (creativeIdeation.test(text)) return true;
  if (!creativeFormats.test(text)) return false;
  return creativeVerbs.test(text) || /^\s*(?:um|uma|uns|umas)\b/.test(text) || /\bbrainstorm/.test(text);
}

export function isCapabilityQuestion(prompt: string): boolean {
  const capabilityQuestion = /^\s*(?:voc[eê]\s+)?(?:consegue|pode|[ée]\s+capaz\s+de|sabe)\b/i.test(prompt)
    || /^\s*o\s+que\s+(?:voc[eê]\s+)?(?:consegue|pode|sabe)\s+fazer\b/i.test(prompt)
    || /^\s*(?:quais|que)\s+(?:s[aã]o\s+(?:as\s+)?)?(?:suas\s+)?(?:habilidades|ferramentas|capacidades|apis)\b/i.test(prompt);
  const explicitAction = /\b(?:quero\s+que|preciso\s+que|crie\s+para\s+mim|construa\s+para\s+mim|implemente\s+para\s+mim|desenvolva\s+para\s+mim|altere|modifique|edite|refatore|corrija|execute|rode)\b/i.test(prompt)
    || /[?!]\s*(?:por\s+favor[, ]*)?(?:crie|construa|implemente|desenvolva|fa[cç]a|altere|modifique|edite|refatore|corrija|execute|rode)\b/i.test(prompt)
    || hasPositiveRequest(prompt, /\b(?:crie|construa|implemente|desenvolva|escreva|adicione)\b/gi);
  return capabilityQuestion && !explicitAction;
}

const softwareProduct = /\b(?:apps?|aplicativos?|aplicac(?:ao|oes)|software|sistemas?|sites?|plataformas?|porta(?:l|is)|dashboards?|interfaces?|programas?|apis?|produtos?\s+digita(?:l|is))\b/;
const creationVerbs = /\b(?:crie|criar|construa|construir|implemente|implementar|desenvolva|desenvolver|escreva|escrever|refatore)\b/gi;

function normalizedIntent(prompt: string): string {
  return prompt.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

/** A product discussion is a deliverable in the chat, never permission to write. */
export function isProductPlanningRequest(prompt: string): boolean {
  const text = normalizedIntent(prompt);
  if (!softwareProduct.test(text) || refactoringIntent(prompt)) return false;
  // Planning an observed repository still follows its evidence-based analysis path.
  if (/\b(?:inspecione|inspecionar|leia|ler|analise|analisar|examine|examinar|revise|revisar|abra|abrir)\b.{0,100}\b(?:workspace|projeto ativo|repositorio|arquivos?|codigo do projeto)\b/.test(text)) return false;
  const planning = /\b(?:planeje|planejar|planejamento|planejando|plano|projete|projetar|esboce|esbocar|no papel)\b/.test(text)
    || /\b(?:como|por onde|de que forma)\b.{0,100}\b(?:criar|construir|implementar|desenvolver|comecar|iniciar|montar|testar|validar|verificar)\b/.test(text)
    || /\b(?:quais?|que)\b.{0,40}\b(?:telas?|fluxos?|funcionalidades?|funcoes|requisitos?|arquitetura|etapas?|passos?|dados|mvp)\b/.test(text)
    || /\b(?:consegue|pode|capaz)\b.{0,100}\b(?:criar|construir|implementar|desenvolver|comecar|iniciar|criacao)\b/.test(text);
  if (!planning) return false;
  const forbidsMutation = /\b(?:nao|nunca|sem)\s+(?:(?:quero|preciso|deve|pode|comece|comecar|inicie|iniciar|que|voce|a)\s+){0,4}(?:crie|criar|edite|editar|altere|alterar|modifique|modificar|escreva|escrever|implemente|implementar)\b/.test(text);
  // An additional current command can request implementation. A conflicting
  // prohibition remains read-only; merely mentioning an infinitive cannot grant it.
  const requestsImplementation = hasPositiveRequest(prompt,
    /\b(?:crie|construa|implemente|desenvolva|monte|escreva|adicione|inicie|comece)\b/gi);
  return forbidsMutation || !requestsImplementation;
}

/** Latest human anchor only; no assistant statement or older task grants authority. */
export function latestHumanIntent(currentPrompt: string,
  history: readonly {role: string; content: string}[] = []): {prompt: string; objective: BrainObjective} | undefined {
  const clean = (text: string) => text.replace(/\n+Workspace local:[\s\S]*$/i, "")
    .replace(/\n+Arquivo ativo: [^\n]+ · [^\n]+ · \d+ linhas · cursor na linha \d+\.?\s*$/i, "").trim();
  const current = clean(currentPrompt);
  const previous = [...history].reverse().find(message => message.role === "user"
    && clean(message.content) && clean(message.content) !== current);
  if (!previous) return undefined;
  const prompt = clean(previous.content);
  if (/(?:^|[.!?;,\n])\s*(?:(?:ok|certo|agora|ent[aã]o)[,\s]*)?(?:cancele|cancelar|esque[cç]a|mude de assunto|outro assunto|vamos falar de)\b/i.test(prompt)) return undefined;
  return {prompt, objective: classifyObjective(prompt)};
}

/** Scoped structural intent; does not grant permission to mutate on its own. */
export function refactoringIntent(prompt: string): "implement" | "analyze" | undefined {
  const text = prompt.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const structural = /\b(?:refator\w*|refactor\w*)\b/.test(text)
    || /\b(?:extraia|extrair|extraindo|separe|separar|divida|dividir|reorganize|reorganizar|simplifique|simplificar|desacople|desacoplar|modularize|modularizar|renomeie|renomear)\b.{0,100}\b(?:funcao|funcoes|classe|classes|modulo|modulos|codigo|responsabilidades|camadas|componentes|metodo|metodos|projeto)\b/.test(text)
    || /\b(?:remova|remover|elimine|eliminar|reduza|reduzir)\b.{0,60}\b(?:duplicacao|codigo duplicado|acoplamento)\b/.test(text);
  if (!structural) return undefined;
  const readOnly = /\b(?:sem (?:alterar|editar|modificar|escrever)|nao (?:altere|edite|modifique|implemente|refatore)|(?:apenas|somente|so) (?:analise|avalie|explique|proponha|planeje|sugira))\b/.test(text)
    || /^(?:como\b|(?:me )?(?:explique|sugira|proponha|planeje|avalie|analise)\b)/.test(text.trim());
  if (readOnly) return "analyze";
  const requested = /\b(?:refatore|refactor|extraia|separe|divida|reorganize|simplifique|desacople|modularize|renomeie|remova|elimine|reduza)\b/.test(text)
    || /\b(?:quero|preciso|vamos|pode|favor)\b.{0,80}\b(?:refatorar|refatoracao|extrair|separar|reorganizar|simplificar|desacoplar|modularizar|renomear|remover|eliminar|reduzir)\b/.test(text);
  return requested ? "implement" : "analyze";
}

/** A concrete failure in the selected project is an operational debug request. */
export function reportedProjectFailure(prompt: string): boolean {
  const text = prompt.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const failure = /\b(?:erros?|falh\w*|bugs?|quebr\w*|crash\w*|nao funciona|nao carrega|nao responde|nao abre|nao salva|nao envia|travou|trava|indisponivel|traceback|exception|unexpected token|invalid json|404|500)\b/.test(text);
  const target = /\b(?:pagina|interface|tela|botao|formulario|painel|site|web|app|aplicativo|aplicacao|sistema|projeto|workspace|arquivo|codigo|frontend|backend|api|index\.html)\b/.test(text);
  const adviceOnly = /\b(?:como (?:investigar|diagnosticar)|qual hipotese|que hipotese|o que posso concluir|(?:apenas|somente|so) (?:explique|analise)|sem (?:alterar|editar|modificar|corrigir))\b/.test(text);
  return failure && target && !adviceOnly;
}

export function repairRequested(prompt: string): boolean {
  const text = prompt.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  if (/\b(?:nao\s+(?:(?:quero|preciso|deve|pode)\s+(?:que\s+)?)?(?:corrija|conserte|arrume|repare|ajuste|altere|edite|modifique|resolva)|sem\s+(?:corrigir|consertar|alterar|editar|modificar))\b/.test(text)) return false;
  if (explicitCorrectionRequest(prompt)) return true;
  return reportedProjectFailure(prompt)
    && !/\?/.test(prompt)
    && !/^\s*(?:por que|porque|qual|quais|como|explique|analise|investigue|diagnostique|revise)\b/.test(text);
}

export function testCreationRequested(prompt: string): boolean {
  return hasPositiveRequest(prompt, /\b(?:crie|criar|escreva|escrever|adicione|adicionar|implemente|implementar|construa|construir|desenvolva|desenvolver)\b.{0,80}\b(?:testes?|su[ií]te(?:s)?(?:\s+de\s+testes?)?|casos?\s+de\s+teste)\b/gi);
}

function explicitFunctionEvaluation(prompt: string): boolean {
  // Recognize the bounded operation's concrete target and inputs. Literal
  // parsing and AST validation still happen in the computation service.
  return /^(?:execute|rode|avalie|teste)\s+(?:a\s+fun[cç][aã]o\s+)?[A-Za-z_]\w*\s*\([\s\S]*\)\s+em\s+[\w./-]+\.py\s*\.?\s*$/i.test(prompt.trim())
    || /^(?:execute|rode|avalie|teste)\s+(?:a\s+fun[cç][aã]o\s+)?[A-Za-z_]\w*\s+em\s+[\w./-]+\.py\s+com\s+argumentos(?:\s+JSON)?\s*:?\s*(?:\[[\s\S]*\]|\{[\s\S]*\})\s*\.?\s*$/i.test(prompt.trim());
}

export function classifyObjective(prompt: string): BrainObjective {
  if (isProductPlanningRequest(prompt)) return "conversation";
  // Questions about capability should be answered conversationally. Without
  // this guard, infinitives such as "construir" are mistaken for commands.
  if (isCapabilityQuestion(prompt)) return "conversation";
  // Escrita autoral é entregue no chat pelo cérebro criativo; verbos como
  // "crie" ou "escreva" não a transformam em build de workspace.
  if (isCreativeWritingRequest(prompt)) return "conversation";
  if (explicitFunctionEvaluation(prompt)) return "operate";
  // Negated implementation verbs often appear in plan-only requests (for
  // example, "planeje, mas não crie arquivos"). Resolve that explicit
  // read-only intent before the broad build verb patterns below.
  const explicitlyReadOnly = /\b(?:somente|apenas|s[oó])\s+(?:a\s+)?(?:an[aá]lise\s+e\s+)?planej(?:e|ar|amento)\b/i.test(prompt)
    || /\b(?:por enquanto|nesta etapa)\b.{0,80}\b(?:somente|apenas)\s+(?:a\s+)?(?:an[aá]lise|planej(?:e|ar|amento))\b/i.test(prompt);
  const explicitlyForbidsMutation = /\b(?:n[aã]o|nunca|sem)\s+(?:crie|criar|edite|editar|altere|alterar|modifique|modificar|escreva|escrever|exclua|excluir|apague|apagar|remova|remover)\b/i.test(prompt);
  if (explicitlyReadOnly && explicitlyForbidsMutation) return "analyze";
  const refactoring = refactoringIntent(prompt);
  if (refactoring) return refactoring === "implement" ? "build" : "analyze";
  const targetsWorkspace = /\b(?:projeto|workspace|repositorio|repo|sistema|aplicativo)\b/i.test(prompt);
  const asksWorkspaceStatus = targetsWorkspace && (
    /\b(?:estado|situacao|status|panorama)\b/i.test(prompt)
    || /\bcomo est[aá]\b/i.test(prompt)
  );
  if (asksWorkspaceStatus) return "analyze";
  const requestsFunctionalVersion = targetsWorkspace
    && /\b(?:primeir[oa]\s+vers[aã]o|vers[aã]o\s+funcional)\b/i.test(prompt)
    && (
      /\b(?:precisa|necessita|carece)\b.{0,100}\b(?:melhorias?|ajustes?|funcional)\b/i.test(prompt)
      || /\b(?:consegue\s+me\s+ajudar|me\s+ajude|me\s+ajudar|ajude-me)\b/i.test(prompt)
    );
  if (requestsFunctionalVersion) return "build";
  // A controlled evaluation is a testing task unless it explicitly asks us to
  // build a fixture or project as part of the evaluation.
  const controlledTesting = /\b(?:avalia[cç][aã]o|teste|ensaio)\b.{0,60}\b(?:controlad[oa]s?|calculad[oa]s?|planejad[oa]s?)\b|\b(?:roteiro|plano)\s+(?:de\s+)?testes?\b/i.test(prompt);
  const createsArtifact = hasPositiveRequest(prompt, /\b(?:crie|escreva|implemente|construa|desenvolva)\b.{0,100}\b(?:arquivos?|aplica[cç][aã]o|aplicativo|app|projeto|programa|sistema|ferramenta|fun[cç][aã]o|m[oó]dulo|p[aá]gina|site)\b/gi);
  const testOnlyImplementation = /^\s*(?:(?:por favor)\s+)?(?:crie|criar|escreva|escrever|adicione|adicionar|implemente|implementar|construa|construir|desenvolva|desenvolver)\s+(?:(?:os?|as?)\s+)?(?:novos?\s+)?(?:testes?|su[ií]te(?:s)?(?:\s+de\s+testes?)?)\b/i.test(prompt);
  if (/^\s*(?:projete|planeje|esboce|sugira)\b.{0,80}\b(?:testes?|su[ií]te\s+de\s+testes?)\b/i.test(prompt)) return "analyze";
  if (controlledTesting && !createsArtifact) return "testing";
  if (createsArtifact && !testOnlyImplementation) return "build";
  if (/^\s*(?:revise|revisar|audite|avalie)\b.{0,120}\b(?:arquivo|c[oó]digo|projeto|workspace|bugs?|riscos?)\b/i.test(prompt)) return "analyze";
  if (reportedProjectFailure(prompt)) return "debug";
  const asksForDiagnosis = /\b(?:qual hipótese|que hipótese|o que posso concluir|o que seria apenas hipótese|qual experimento|que experimento|qual checagem|que checagem|como investigar|como eu investigaria)\b/i.test(prompt);
  const explicitlyTargetsWorkspace = /\b(?:inspecione|inspecionar|analise|analisar|examine|examinar|leia|ler|abra|abrir|revise|revisar|depure|depurar|debugue|debugar)\b.{0,120}\b(?:projeto|workspace|reposit[oó]rio|arquivos?|c[oó]digo|src\/|[\w.-]+\.(?:py|ts|tsx|js|jsx|rs|cpp|h))\b/i.test(prompt);
  // A pessoa pode pedir orientação sobre uma situação de depuração sem pedir
  // que o agente examine o repositório. Nesse caso, responda com o contexto
  // fornecido, sem exigir que um workspace esteja selecionado.
  if (asksForDiagnosis && !explicitlyTargetsWorkspace) return "conversation";
  // Implementar um programa e depois testá-lo é uma tarefa de build. O termo
  // "testes" não deve desviar o agente para a rota que só executa checks.
  if (objectiveLabels[0]!.pattern.test(prompt)) return "debug";
  // Se o objeto direto da criação são testes, o pedido continua sendo de
  // execução de testes. Caso contrário, um app criado e testado é build.
  const implementationVerb = hasPositiveRequest(prompt, creationVerbs);
  const createsProgram = hasPositiveRequest(prompt, /\b(?:crie|criar|fa[cç]a|construa|construir|desenvolva|desenvolver|implemente|implementar)\b.{0,100}\b(?:interface|front[ -]?end|back[ -]?end|api|tela|programa|cli|conversor|aplicativo|app|sistema|ferramenta|fun[cç][aã]o|m[oó]dulo|biblioteca|arquivos?|p[aá]gina|site)\b/gi);
  if (!testOnlyImplementation && (implementationVerb || createsProgram)) return "build";
  const transformsIntoProduct = /\b(?:(?:quero|preciso|vamos|fa[cç]a|faca)\b.{0,50})?(?:transforme|transformar|converta|converter|evolua|evoluir)\b.{0,100}\b(?:produto|sistema|aplicativo|app|site|plataforma|web)\b/i.test(prompt);
  if (transformsIntoProduct) return "build";
  const requestsSoftware = /\b(?:quero|preciso de|adicione|acrescente)\s+(?:(?:um|uma|o|a)\s+)?(?:interface|front[ -]?end|back[ -]?end|api|tela|aplicativo|site)\b/i.test(prompt);
  if (requestsSoftware && !/\?|\bn[aã]o\s+(?:quero|preciso|adicione|acrescente)\b/i.test(prompt)) return "build";
  const establishedObjective = objectiveLabels.slice(1, 4).find((item) => item.pattern.test(prompt)
    && (item.objective !== "testing" || hasPositiveRequest(prompt,
      /\b(?:faca|faça|rode|roda|rodar|execute|executar|teste|testar|verifique|verificar|adicione|adicionar|crie|criar|escreva|escrever|passe|pytest|cargo test|npm test|unittest)\b/gi)));
  if (establishedObjective) return establishedObjective.objective;
  if (asksForCurrentInformation(prompt)) return "research";
  return objectiveLabels.slice(4).find((item) => item.pattern.test(prompt)
    && (item.objective !== "build" || hasPositiveRequest(prompt, creationVerbs)))?.objective ?? "conversation";
}

function confidence(
  score: number,
  basis: Confidence["basis"],
  ...reasons: string[]
): Confidence {
  return {
    score: Math.max(0, Math.min(1, score)),
    basis,
    reasons,
    calibrated: false,
  };
}

function constraintsFor(prompt: string): RequirementConstraint[] {
  return constraintPatterns
    .filter((item) => item.pattern.test(prompt))
    .map((item, index) => ({
      id: "constraint-" + (index + 1),
      text: item.label + " (trechos do pedido: "
        + [...prompt.matchAll(new RegExp(item.pattern.source, "gi"))].map(match => match[0].trim()).join("; ") + ")",
      source: "user" as const,
      mandatory: true,
      confidence: confidence(0.9, "user_confirmed", "A restrição foi declarada no pedido."),
    }));
}

export function explicitCorrectionRequest(prompt: string): boolean {
  return hasPositiveRequest(prompt, /\b(?:corrija|conserte|arrume|repare|ajuste|implemente|altere|edite|modifique|resolva)\b/gi);
}

function hasPositiveRequest(prompt: string, verbs: RegExp): boolean {
  for (const match of prompt.matchAll(verbs)) {
    const index = match.index ?? 0;
    const clauseStart = Math.max(
      prompt.lastIndexOf(".", index), prompt.lastIndexOf(";", index),
      prompt.lastIndexOf("!", index), prompt.lastIndexOf("?", index),
      prompt.lastIndexOf("\n", index),
    ) + 1;
    const prefix = normalizedIntent(prompt.slice(clauseStart, index));
    const negated = /\b(?:nao|nunca|sem)\s+(?:(?:quero|preciso|deve|pode|comece|comecar|inicie|iniciar|que|voce|a)\s+){0,4}$/.test(prefix);
    const advice = /\b(?:como|explique|descreva|planeje|planejar|sugira|proponha)\b/.test(prefix);
    const separateCommand = /\b(?:e|mas|agora|entao|depois|em seguida)\s*$/.test(prefix);
    if (!negated && (!advice || separateCommand)) return true;
  }
  return false;
}

function acceptanceFor(prompt: string, objective: BrainObjective): AcceptanceCriterion[] {
  const rows: AcceptanceCriterion[] = [
    {
      id: "acceptance-clarity",
      text: "A entrega deve responder ao objetivo declarado sem inventar premissas.",
      verifiable: true,
      source: "derived",
    },
  ];
  const requestsCorrection = repairRequested(prompt);
  if (objective === "build" || (objective === "debug" && requestsCorrection)
      || (objective === "testing" && testCreationRequested(prompt))) {
    rows.push(
      {
        id: "acceptance-executable",
        text: "A alteração deve ser executável ou ter a impossibilidade explicitamente demonstrada.",
        verifiable: true,
        source: "derived",
      },
      {
        id: "acceptance-tests",
        text: "O resultado deve possuir verificação e pelo menos um caso de borda.",
        verifiable: true,
        source: "derived",
      },
    );
  } else if (objective === "debug") {
    rows.push({
      id: "acceptance-diagnostic-evidence",
      text: "O diagnóstico deve se apoiar em evidência observada e separar fatos de hipóteses.",
      verifiable: true,
      source: "derived",
    });
  }
  if (objective === "research") {
    rows.push({
      id: "acceptance-sources",
      text: "Conclusões factuais devem apontar evidências e incertezas.",
      verifiable: true,
      source: "derived",
    });
  }
  if (objective === "testing") {
    rows.push({
      id: "acceptance-test-execution",
      text: "Os checks do projeto devem ser executados e seu resultado observado.",
      verifiable: true,
      source: "derived",
    });
  }
  if (objective === "analyze") {
    rows.push({
      id: "acceptance-project-evidence",
      text: "A explicação do projeto deve citar arquivos lidos e separar fatos observados de inferências.",
      verifiable: true,
      source: "derived",
    });
  }
  if (prompt.length > 500) {
    rows.push({
      id: "acceptance-scope",
      text: "Toda restrição explícita do pedido deve aparecer na entrega ou no diagnóstico.",
      verifiable: true,
      source: "derived",
    });
  }
  return rows;
}

function questionsFor(
  prompt: string,
  objective: BrainObjective,
  missing: readonly string[],
): string[] {
  const questions: string[] = [];
  if (missing.includes("resultado esperado")) {
    questions.push("Qual resultado observável deve estar pronto para considerarmos a tarefa concluída?");
  }
  if (missing.includes("ambiente técnico")) {
    questions.push("Qual linguagem, versão e ambiente devo respeitar?");
  }
  if (missing.includes("escopo ou usuário")) {
    questions.push("Quem usará isso e qual é o menor escopo útil da primeira versão?");
  }
  if (objective === "operate" && !/\b(?:pode|autoriz|confirma|aprov)\b/i.test(prompt)) {
    questions.push("Você autoriza a ação externa ou devo apenas preparar um plano/rascunho?");
  }
  return questions.slice(0, 3);
}

export function analyzeRequirements(
  promptInput: string,
  priorConstraints: readonly RequirementConstraint[] = [],
  objectiveOverride?: BrainObjective,
): RequirementAnalysis {
  const prompt = promptInput.trim();
  if (!prompt) throw new Error("O pedido não pode ser vazio.");
  if (prompt.length > 24000) throw new Error("O pedido excede o limite de 24000 caracteres.");

  const objective = objectiveOverride ?? classifyObjective(prompt);
  const boundedEvaluation = explicitFunctionEvaluation(prompt);
  const proactiveDiagnostic = reportedProjectFailure(prompt)
    || objective === "debug"
    || /^\s*(?:revise|revisar|analise|analisar|inspecione|inspecionar|depure|depurar)\b/i.test(prompt);
  const isBuildLike = !boundedEvaluation && (objective === "build" || objective === "debug" || objective === "operate" || objective === "learn");
  const hasTechnicalAnchor = boundedEvaluation || technicalAnchors.some((pattern) => pattern.test(prompt));
  const hasProductPurpose = productPurpose.test(prompt)
    || (productArtifacts.test(prompt) && productObjects.test(prompt));
  const hasDomainAnchor = boundedEvaluation || hasProductPurpose || domainAnchors.some((pattern) => pattern.test(prompt))
    && (!productArtifacts.test(prompt) || hasProductPurpose
      || personalProductScope.test(prompt)
      || /\b(?:função|módulo|serviço|site|script|assistente|modelo|dataset|projeto)\b/i.test(prompt));
  const hasVagueLanguage = !boundedEvaluation && !hasProductPurpose && vaguePatterns.some((pattern) => pattern.test(prompt));
  const hasOutcome = /\b(?:deve|retorne|entregue|passar testes|inclua testes|resultado)\b/i.test(prompt);
  const missing: string[] = [];

  if (isBuildLike && !proactiveDiagnostic && !hasDomainAnchor) missing.push("escopo ou usuário");
  if (isBuildLike && !proactiveDiagnostic && !hasTechnicalAnchor) missing.push("ambiente técnico");
  if (isBuildLike && !proactiveDiagnostic && prompt.length < 180 && !hasOutcome && !hasProductPurpose) missing.push("resultado esperado");
  if (hasVagueLanguage && !proactiveDiagnostic && !hasDomainAnchor) missing.push("escopo ou usuário");
  if (objective === "research" && !hasDomainAnchor && prompt.length < 80) missing.push("resultado esperado");

  const uniqueMissing = [...new Set(missing)];
  const constraintRows = [...priorConstraints, ...constraintsFor(prompt)];
  const interpretations: RequirementInterpretation[] = [
    {
      id: "interpretation-primary",
      summary: prompt,
      assumptions: uniqueMissing.length === 0 ? [] : uniqueMissing.map((item) => "Ainda não definido: " + item + "."),
      missing: uniqueMissing,
      plausibility: uniqueMissing.length === 0 ? 0.9 : 0.58,
    },
  ];
  if (hasVagueLanguage || (isBuildLike && !hasTechnicalAnchor && !hasProductPurpose)) {
    interpretations.push({
      id: "interpretation-minimal",
      summary: "Produzir apenas um protótipo mínimo, local e reversível.",
      assumptions: ["O usuário prefere validar uma fatia pequena antes de ampliar."],
      missing: uniqueMissing,
      plausibility: 0.42,
    });
  }

  const questions = questionsFor(prompt, objective, uniqueMissing);
  const ambiguityScore = Math.min(
    1,
    (uniqueMissing.length * 0.24)
      + (hasVagueLanguage ? 0.26 : 0)
      + (interpretations.length > 1 ? 0.16 : 0)
      + (isBuildLike && !hasTechnicalAnchor ? 0.18 : 0),
  );
  const inspectBeforeStackChoice = objective === "build" && hasProductPurpose
    && uniqueMissing.every(item => item === "ambiente técnico");
  if (inspectBeforeStackChoice) {
    interpretations[0]!.assumptions = ["Inspecionar o workspace e reutilizar sua stack; se estiver vazio, propor uma primeira versão local e reversível com escolhas explícitas."];
  }
  const requiresClarification = !proactiveDiagnostic && !inspectBeforeStackChoice && uniqueMissing.length > 0 && (
    ambiguityScore >= 0.45
    || (isBuildLike && !hasTechnicalAnchor && !hasDomainAnchor)
  );

  return {
    prompt,
    objective,
    summary: prompt,
    interpretations,
    constraints: constraintRows,
    acceptanceCriteria: acceptanceFor(prompt, objective),
    missingInformation: uniqueMissing,
    questions: requiresClarification ? questions : [],
    ambiguityScore,
    requiresClarification,
    confidence: confidence(
      requiresClarification ? 0.55 : 0.86,
      "derived",
      requiresClarification
        ? "Há premissas técnicas ou de escopo que mudam a solução."
        : "O pedido contém intenção e contexto suficientes para iniciar um plano.",
    ),
  };
}
