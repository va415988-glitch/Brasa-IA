import type {
  BrainEvent,
  BrainEventLog,
  BrainMemory,
  BrainPreparation,
  BrainRequest,
  BrainThinkingSummary,
  RequirementAnalysis,
  RequirementConstraint,
} from "./brain-contracts.ts";
import {InMemoryBrainEventLog} from "./event-log.ts";
import {CognitiveStateMachine} from "./brain-state.ts";
import {analyzeRequirements} from "./requirements.ts";
import {personalityLayersFor} from "./personality.ts";


function thinkingFor(analysis: RequirementAnalysis): BrainThinkingSummary {
  const personality = personalityLayersFor(analysis.prompt, analysis.objective);
  const routeByObjective: Record<RequirementAnalysis["objective"], BrainThinkingSummary["route"]> = {
    conversation: "conversation",
    research: "web_research",
    analyze: "workspace_read",
    build: "workspace_write",
    debug: "workspace_write",
    testing: "workspace_read",
    learn: "learning",
    operate: "external_action",
  };
  const route = routeByObjective[analysis.objective];
  const nextStepByRoute: Record<BrainThinkingSummary["route"], string> = {
    conversation: "Responder diretamente se houver informação suficiente; consultar fontes permitidas para esclarecer lacunas.",
    workspace_read: "Inspecionar o workspace e apoiar a resposta em evidências observadas.",
    workspace_write: "Inspecionar o workspace, preparar uma proposta de alteração e validar o resultado.",
    web_research: "Consultar fontes externas relevantes e separar evidências de inferências.",
    learning: "Examinar as fontes de aprendizado disponíveis antes de atualizar conhecimento local.",
    external_action: "Verificar escopo, risco e autorização antes de executar a ação externa.",
  };
  const rationale = analysis.objective === "conversation"
    ? "O pedido é conversacional; consultas podem apoiar a resposta sem autorizar alterações ou processos."
    : "A rota foi escolhida pelo objetivo classificado e seguirá as restrições e lacunas detectadas no pedido.";
  return {
    route,
    personalityMode: personality.mode,
    personalityGuidance: personality.guidance,
    interpretation: analysis.summary.slice(0, 500),
    rationale,
    uncertainties: [...analysis.missingInformation],
    nextStep: nextStepByRoute[route],
    confidence: {...analysis.confidence, reasons: [...analysis.confidence.reasons]},
  };
}

export interface CognitiveBrainOptions {
  eventLog?: BrainEventLog;
  memory?: BrainMemory;
  clock?: {now(): string};
}

/**
 * Primeira camada executável do cérebro: transforma linguagem em estado,
 * perguntas, restrições e eventos auditáveis antes de qualquer ferramenta.
 */
export class CognitiveBrain {
  private readonly eventLog: BrainEventLog;
  private readonly memory?: BrainMemory;
  private readonly clock?: {now(): string};

  constructor(options: CognitiveBrainOptions = {}) {
    this.eventLog = options.eventLog ?? new InMemoryBrainEventLog();
    this.memory = options.memory;
    this.clock = options.clock;
  }

  async persistEvents(events: readonly BrainEvent[]): Promise<void> {
    for (const event of events) await this.eventLog.append(event);
  }

  async prepare(
    request: BrainRequest,
    priorConstraints: readonly RequirementConstraint[] = request.priorConstraints ?? [],
  ): Promise<BrainPreparation> {
    const analysis = analyzeRequirements(request.prompt, priorConstraints, request.objective);
    const controller = new CognitiveStateMachine(
      request.taskId,
      request.objective,
      this.clock,
    );
    controller.setQuestions(analysis.questions);
    controller.setConfidence(analysis.confidence);
    const thinking = thinkingFor(analysis);
    const decisionContext = {
      thinking: {
        route: thinking.route,
        personalityMode: thinking.personalityMode,
        personalityGuidance: thinking.personalityGuidance,
        interpretation: thinking.interpretation,
        rationale: thinking.rationale,
        uncertainties: thinking.uncertainties,
        nextStep: thinking.nextStep,
        confidence: thinking.confidence,
      },
    };
    const decisionEvent = analysis.requiresClarification
      ? controller.transition(
        "clarifying",
        "O pedido possui ambiguidade ou premissas decisivas ausentes.",
        {questions: analysis.questions, ambiguityScore: analysis.ambiguityScore, ...decisionContext},
      )
      : controller.transition(
        "planning",
        "O pedido possui informação suficiente para iniciar um plano.",
        {ambiguityScore: analysis.ambiguityScore, ...decisionContext},
      );
    const events = [decisionEvent];
    if (request.persist !== false) {
      for (const event of events) await this.eventLog.append(event);
    }
    if (request.persist !== false && this.memory) {
      try {
        await this.memory.remember({
          kind: "working",
          key: "requirements-" + request.taskId,
          value: JSON.stringify(analysis),
          source: "cognitive-brain.requirements",
          confidence: analysis.confidence,
          taskId: request.taskId,
        });
      } catch {
        // Memória é persistência auxiliar; uma política local de segredos ou
        // falha de disco não deve impedir a tarefa atual.
      }
    }
    return {
      request,
      analysis,
      thinking,
      state: controller.current,
      events: controller.events,
    };
  }

  async recordClarification(
    preparation: BrainPreparation,
    answers: readonly string[],
    persistEvents = true,
  ): Promise<BrainPreparation> {
    if (answers.length === 0 || answers.length > 3) {
      throw new Error("A clarificação precisa ter de uma a três respostas.");
    }
    const controller = new CognitiveStateMachine(
      preparation.request.taskId,
      preparation.request.objective,
      this.clock,
      preparation.state,
    );
    controller.restore({version: 1, state: preparation.state, events: preparation.events});
    controller.transition("observing", "Respostas de clarificação recebidas.", {
      answers: [...answers],
    });
    const clarifiedPrompt = [
      preparation.request.prompt,
      "Respostas confirmadas: " + answers.join(" | "),
    ].join("\n");
    const next = analyzeRequirements(
      clarifiedPrompt,
      preparation.analysis.constraints,
      preparation.request.objective,
    );
    controller.setQuestions(next.questions);
    controller.setConfidence(next.confidence);
    const thinking = thinkingFor(next);
    const target = next.requiresClarification ? "clarifying" : "planning";
    controller.transition(
      target,
      target === "planning"
        ? "As respostas reduziram a ambiguidade o suficiente para planejar."
        : "Ainda existe uma lacuna decisiva após a clarificação.",
      {
        questions: next.questions,
        ambiguityScore: next.ambiguityScore,
        thinking: {
          route: thinking.route,
          personalityMode: thinking.personalityMode,
          personalityGuidance: thinking.personalityGuidance,
          interpretation: thinking.interpretation,
          rationale: thinking.rationale,
          uncertainties: thinking.uncertainties,
          nextStep: thinking.nextStep,
          confidence: thinking.confidence,
        },
      },
    );
    if (persistEvents) {
      for (const event of controller.events.slice(preparation.events.length)) {
        await this.eventLog.append(event);
      }
    }
    return {
      request: {...preparation.request, prompt: clarifiedPrompt},
      analysis: next,
      thinking,
      state: controller.current,
      events: [...preparation.events, ...controller.events],
    };
  }
}
