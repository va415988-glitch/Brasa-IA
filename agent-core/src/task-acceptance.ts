import type {BrainObjective} from "./brain-contracts.ts";
import type {Evidence, RuntimeToolName, Verification} from "./contracts.ts";
import {testCreationRequested} from "./requirements.ts";

export interface TaskAcceptanceInput {
  objective: BrainObjective;
  prompt?: string;
  finalText?: string;
  successfulTools: readonly RuntimeToolName[];
  readPaths?: readonly string[];
  requiredReadPaths?: readonly string[];
  researchEvidence?: readonly Evidence[];
  hasChanges?: boolean;
  correctionRequested?: boolean;
  toolCallProposed?: boolean;
  verification?: Verification;
}

export interface TaskAcceptanceCheck {
  id: string;
  passed: boolean;
  detail: string;
}

export interface TaskAcceptanceReport {
  passed: boolean;
  checks: readonly TaskAcceptanceCheck[];
  pending: readonly TaskAcceptanceCheck[];
}

function hasUsableResearchEvidence(evidence: readonly Evidence[]): boolean {
  return evidence.some((item) => {
    try {
      const url = new URL(item.url);
      return (url.protocol === "https:" || url.protocol === "http:") && item.excerpt.trim().length > 0;
    } catch {
      return false;
    }
  });
}

/**
 * Única porta determinística para o núcleo declarar que um ciclo operacional
 * satisfez os resultados observáveis mínimos do objetivo.
 */
export function evaluateTaskAcceptance(input: TaskAcceptanceInput): TaskAcceptanceReport {
  const checks: TaskAcceptanceCheck[] = [];
  const add = (id: string, passed: boolean, detail: string): void => {
    checks.push({id, passed, detail});
  };
  const finalText = input.finalText?.trim() ?? "";
  const genericFiller = /^(?:estou acompanhando\. pode me contar um pouco mais\?|a tarefa n[aã]o foi conclu[ií]da\.?|conclu[ií] a etapa\b)/i.test(finalText);
  const hasResponse = Boolean(finalText) && !genericFiller;
  const hasToolResult = input.successfulTools.length > 0;
  const verifiedChange = Boolean(input.verification?.executed && input.verification.passed);
  const onlyStructuralCheck = input.verification?.behaviorExecuted === false
    || ["syntax", "build", "typecheck"].includes(input.verification?.kind ?? "");
  if (onlyStructuralCheck && (input.objective === "testing" || testCreationRequested(input.prompt ?? ""))) {
    add("verification.behavior", false,
      "A verificação observada cobre estrutura ou executou zero casos; o pedido exige testes de comportamento.");
  }

  if (input.objective !== "conversation") {
    add("delivery.summary", hasResponse,
      "A tarefa precisa de uma síntese final baseada nos resultados observados.");
  }

  switch (input.objective) {
    case "conversation":
      add("conversation.response", hasResponse, "A resposta ao turno precisa conter texto.");
      add("conversation.no_pending_action", input.toolCallProposed !== true,
        "Uma chamada proposta precisa ser resolvida antes da resposta final.");
      break;
    case "analyze": {
      const readPaths = new Set(input.readPaths ?? []);
      const requiredReadPaths = [...new Set(input.requiredReadPaths ?? [])];
      const missingReadPaths = requiredReadPaths.filter((path) => !readPaths.has(path));
      const refusal = /\b(?:não consegui iniciar|não consegui produzir|não consegui extrair|não encontrei evidência suficiente|não vou inferir|indique um arquivo|a tarefa permanece pendente)\b/i.test(input.finalText ?? "");
      const proceduralOnly = /^\s*(?:concluí|conclui|finalizei|terminei)\s+(?:a\s+)?(?:etapa|inspeção|inspecao)\b/i.test(input.finalText ?? "");
      const referencesReadEvidence = [...readPaths].some((path) => (input.finalText ?? "").includes(path));
      const requestsReview = /\b(?:avalie|revise|audite|melhor\w*|aperfeiço\w*|pontos?\s+(?:fracos?|de\s+melhora|de\s+melhoria)|riscos?|bugs?|problemas?|gargalos?)\b/i.test(input.prompt ?? "");
      const deliversReview = /\b(?:melhor\w*|priori\w*|recomend\w*|riscos?|bugs?|problemas?|defeitos?|corrig\w*|investiga\w*)\b/i.test(input.finalText ?? "");
      add("analysis.answer", hasResponse && !refusal && !proceduralOnly,
        refusal ? "A conclusão ainda contém um bloqueio genérico; responda com as evidências já lidas ou explique a lacuna concreta."
          : proceduralOnly ? "A resposta descreve apenas uma etapa executada; entregue a conclusão solicitada com evidências."
          : "A análise precisa entregar uma conclusão, não apenas repetir que está pendente.");
      add("analysis.read", readPaths.size > 0,
        "A análise precisa ler pelo menos um arquivo pertinente do workspace.");
      add("analysis.references", referencesReadEvidence,
        "A síntese da análise precisa citar ao menos um dos arquivos cujo conteúdo foi lido.");
      if (requestsReview) {
        add("analysis.review_delivery", deliversReview,
          "Um pedido de revisão precisa entregar melhorias, riscos, problemas ou recomendações priorizadas — não apenas descrever o projeto.");
      }
      add("analysis.coverage", missingReadPaths.length === 0,
        missingReadPaths.length
          ? "Ainda faltam leituras dos arquivos centrais selecionados: " + missingReadPaths.join(", ")
          : "Todos os arquivos centrais selecionados foram lidos.");
      break;
    }
    case "research":
      add("research.sources", hasUsableResearchEvidence(input.researchEvidence ?? []),
        "A pesquisa precisa conter ao menos uma fonte HTTP(S) com trecho de evidência.");
      break;
    case "build":
      add("build.change", input.hasChanges === true,
        "A implementação precisa confirmar um efeito de escrita em arquivo, não apenas criar uma pasta.");
      add("build.verification", verifiedChange,
        input.verification?.executed && !input.verification.passed
          ? "A alteração foi verificada, mas a verificação falhou: " + input.verification.summary
          : "A implementação precisa passar por uma verificação executada e aprovada.");
      break;
    case "debug":
      if (input.correctionRequested || input.hasChanges === true) {
        add("debug.change", input.hasChanges === true,
          "O pedido de correção precisa confirmar um efeito de escrita em arquivo, não apenas criar uma pasta.");
        add("debug.verification", input.hasChanges === true && verifiedChange,
          input.verification?.executed && !input.verification.passed
            ? "A correção foi verificada, mas a verificação falhou: " + input.verification.summary
            : "A verificação precisa ser executada e aprovada depois da alteração no arquivo.");
      } else {
        const readPaths = [...new Set(input.readPaths ?? [])];
        const citedReadPath = readPaths.some((path) => (input.finalText ?? "").includes(path));
        const explainsCause = /\b(?:causa|porque|por que|indica|significa|acontece|prov[aá]vel|provavelmente|evid[eê]ncia|diagn[oó]stico)\b/i.test(input.finalText ?? "");
        const givesNextStep = /\b(?:abra|inicie|execute|rode|use|aponte|configure|corrija|altere|verifique|confira|pr[oó]ximo passo|recomendo|recomendação|deve)\b/i.test(input.finalText ?? "");
        add("debug.evidence", hasToolResult && readPaths.length > 0 && citedReadPath,
          "O diagnóstico precisa citar um arquivo cujo conteúdo foi lido e uma observação da ferramenta.");
        add("debug.actionable", hasResponse && explainsCause && givesNextStep,
          "O diagnóstico precisa explicar a causa provável e indicar uma ação concreta baseada nas evidências.");
      }
      break;
    case "testing":
      if (testCreationRequested(input.prompt ?? "")) {
        add("testing.change", input.hasChanges === true,
          "A criação de testes precisa confirmar uma escrita em arquivo.");
        add("testing.verification", verifiedChange,
          "Os testes criados precisam passar por uma verificação executada após a escrita.");
      } else {
        // Uma suíte vermelha é um resultado válido do pedido de execução.
        add("testing.execution", input.verification?.executed === true,
          input.verification?.executed && !input.verification.passed
            ? "O check foi executado; a suíte reportou falhas: " + input.verification.summary
            : "É necessário executar um check e observar seu resultado.");
      }
      break;
    case "learn":
      add("learning.evidence", hasToolResult || hasUsableResearchEvidence(input.researchEvidence ?? []),
        "O aprendizado precisa consultar uma fonte local ou externa verificável.");
      break;
    case "operate":
      add("operation.result", hasToolResult,
        "A operação precisa produzir ao menos um resultado observado.");
      break;
  }

  const pending = checks.filter((check) => !check.passed);
  return {passed: pending.length === 0, checks, pending};
}
