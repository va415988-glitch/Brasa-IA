import type {
  BrainClock,
  BrainEvent,
  BrainObjective,
  BrainSnapshot,
  BrainState,
  BrainStateKind,
} from "./brain-contracts.ts";

const transitions: Record<BrainStateKind, readonly BrainStateKind[]> = {
  observing: ["clarifying", "planning", "abstaining"],
  clarifying: ["observing", "planning", "abstaining"],
  planning: ["clarifying", "awaiting_approval", "executing", "verifying", "recovering", "delivering", "abstaining"],
  awaiting_approval: ["executing", "planning", "abstaining"],
  executing: ["verifying", "recovering", "abstaining"],
  verifying: ["planning", "delivering", "recovering", "abstaining"],
  recovering: ["planning", "executing", "clarifying", "abstaining"],
  delivering: ["completed", "observing"],
  abstaining: ["completed", "observing", "clarifying"],
  completed: ["observing"],
};

const defaultClock: BrainClock = {
  now: () => new Date().toISOString(),
};

function cloneState(state: BrainState): BrainState {
  return {
    ...state,
    pendingQuestions: [...state.pendingQuestions],
    confidence: state.confidence
      ? {...state.confidence, reasons: [...state.confidence.reasons]}
      : undefined,
  };
}

function cloneEvent(event: BrainEvent): BrainEvent {
  return {
    ...event,
    payload: event.payload ? {...event.payload} : undefined,
  };
}

export class CognitiveStateMachine {
  private state: BrainState;
  private readonly eventRows: BrainEvent[];
  private readonly clock: BrainClock;

  constructor(
    taskId: string,
    objective: BrainObjective,
    clock: BrainClock = defaultClock,
    initial?: BrainState,
  ) {
    if (!taskId.trim()) throw new Error("taskId é obrigatório.");
    this.clock = clock;
    const now = clock.now();
    this.state = initial
      ? cloneState(initial)
      : {
        version: 1,
        taskId,
        objective,
        kind: "observing",
        startedAt: now,
        updatedAt: now,
        pendingQuestions: [],
      };
    if (this.state.taskId !== taskId) {
      throw new Error("O snapshot pertence a outro taskId.");
    }
    this.eventRows = [];
  }

  get current(): BrainState {
    return cloneState(this.state);
  }

  get events(): readonly BrainEvent[] {
    return this.eventRows.map(cloneEvent);
  }

  canTransition(to: BrainStateKind): boolean {
    return transitions[this.state.kind].includes(to);
  }

  transition(
    to: BrainStateKind,
    reason: string,
    payload?: Readonly<Record<string, unknown>>,
  ): BrainEvent {
    const normalizedReason = reason.trim();
    if (!normalizedReason) throw new Error("Toda transição precisa de uma razão.");
    if (!this.canTransition(to)) {
      throw new Error(
        "Transição cognitiva inválida: " + this.state.kind + " -> " + to + ".",
      );
    }
    const from = this.state.kind;
    const at = this.clock.now();
    this.state = {
      ...this.state,
      kind: to,
      updatedAt: at,
      activeActionId: typeof payload?.actionId === "string"
        ? payload.actionId
        : this.state.activeActionId,
      activePlanId: typeof payload?.planId === "string"
        ? payload.planId
        : this.state.activePlanId,
      lastError: typeof payload?.error === "string"
        ? payload.error
        : this.state.lastError,
    };
    return this.recordEvent({
      at,
      type: "state.transition",
      from,
      to,
      reason: normalizedReason,
      payload,
    });
  }

  setQuestions(questions: readonly string[]): void {
    if (questions.length > 3) throw new Error("O cérebro pode fazer no máximo três perguntas por ciclo.");
    const normalized = questions.map((question) => question.trim()).filter(Boolean);
    this.state = {
      ...this.state,
      pendingQuestions: normalized,
      updatedAt: this.clock.now(),
    };
  }

  setConfidence(confidence: BrainState["confidence"]): void {
    if (confidence && (confidence.score < 0 || confidence.score > 1)) {
      throw new Error("A confiança do estado deve estar entre 0 e 1.");
    }
    this.state = {...this.state, confidence, updatedAt: this.clock.now()};
  }

  record(
    type: string,
    reason: string,
    payload?: Readonly<Record<string, unknown>>,
  ): BrainEvent {
    return this.recordEvent({
      at: this.clock.now(),
      type,
      from: this.state.kind,
      to: this.state.kind,
      reason: reason.trim(),
      payload,
    });
  }

  snapshot(): BrainSnapshot {
    return {
      version: 1,
      state: cloneState(this.state),
      events: this.events,
    };
  }

  restore(snapshot: BrainSnapshot): void {
    if (snapshot.version !== 1) throw new Error("Versão de snapshot cognitivo não suportada.");
    if (snapshot.state.taskId !== this.state.taskId) {
      throw new Error("O snapshot cognitivo pertence a outro taskId.");
    }
    if (!Array.isArray(snapshot.events)) {
      throw new Error("Snapshot cognitivo sem log de eventos.");
    }
    this.state = cloneState(snapshot.state);
    this.eventRows.length = 0;
    this.eventRows.push(...snapshot.events.map(cloneEvent));
  }

  private recordEvent(input: Omit<BrainEvent, "version" | "id" | "seq" | "taskId">): BrainEvent {
    const event: BrainEvent = {
      version: 1,
      id: this.state.taskId + "-event-" + (this.eventRows.length + 1),
      seq: this.eventRows.length + 1,
      taskId: this.state.taskId,
      ...input,
    };
    this.eventRows.push(event);
    return cloneEvent(event);
  }
}

export function allowedBrainTransitions(from: BrainStateKind): readonly BrainStateKind[] {
  return [...transitions[from]];
}
