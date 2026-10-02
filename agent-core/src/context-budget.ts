/**
 * Orçamento de contexto do cérebro.
 *
 * A janela padrão acompanha a configuração persistente de 32k tokens, mas isso só é útil se o núcleo
 * reservar espaço para a resposta e escolher o histórico por prioridade.
 * Esta camada não tokeniza nem chama modelo: produz uma janela determinística
 * que pode ser validada pelo adaptador Python ou pelo runtime Rust.
 */

export const MIN_PRODUCTION_CONTEXT_TOKENS = 8_192;
export const TARGET_PRODUCTION_CONTEXT_TOKENS = 32_768;
export const TARGET_GENERATION_TOKENS = 4_096;

export type ContextRole = "system" | "user" | "assistant" | "tool";

export interface ContextMessage {
  role: ContextRole;
  content: string;
  priority?: "critical" | "normal" | "discardable";
}

export interface ContextWindowOptions {
  maxContextTokens?: number;
  reserveGenerationTokens?: number;
}

export interface ContextWindow {
  messages: readonly ContextMessage[];
  maxContextTokens: number;
  reservedGenerationTokens: number;
  inputBudgetTokens: number;
  estimatedInputTokens: number;
  droppedMessages: number;
  truncatedMessages: number;
  warnings: readonly string[];
}

/** Estimativa conservadora para português, código e JSON misturados. */
export function estimateTokens(content: string): number {
  const normalized = content.trim();
  return normalized ? Math.max(1, Math.ceil(normalized.length / 4)) : 1;
}

function cost(message: ContextMessage): number {
  return estimateTokens(message.content) + 4;
}

function truncateToTokens(message: ContextMessage, tokenBudget: number): ContextMessage {
  const characterBudget = Math.max(1, (tokenBudget - 4) * 4);
  return {...message, content: message.content.slice(-characterBudget)};
}

/**
 * Mantém instruções do sistema, a mensagem atual e o histórico mais recente.
 * Mensagens antigas são descartadas de modo explícito, nunca silencioso.
 */
export function fitContextWindow(
  messages: readonly ContextMessage[],
  options: ContextWindowOptions = {},
): ContextWindow {
  const maxContextTokens = Math.max(
    1,
    Math.floor(options.maxContextTokens ?? TARGET_PRODUCTION_CONTEXT_TOKENS),
  );
  const reservedGenerationTokens = Math.min(
    Math.max(1, Math.floor(options.reserveGenerationTokens ?? TARGET_GENERATION_TOKENS)),
    Math.max(1, maxContextTokens - 1),
  );
  const inputBudgetTokens = maxContextTokens - reservedGenerationTokens;
  const source = messages.map((message) => ({
    ...message,
    content: message.content.trim(),
  })).filter((message) => message.content.length > 0);
  if (source.length === 0) {
    return {
      messages: [],
      maxContextTokens,
      reservedGenerationTokens,
      inputBudgetTokens,
      estimatedInputTokens: 0,
      droppedMessages: 0,
      truncatedMessages: 0,
      warnings: [],
    };
  }

  const selected = new Set<number>();
  const firstSystem = source.findIndex((message) => message.role === "system");
  const latestUser = [...source].map((message, index) => ({message, index}))
    .reverse().find((item) => item.message.role === "user")?.index ?? -1;
  if (firstSystem >= 0) selected.add(firstSystem);
  if (latestUser >= 0) selected.add(latestUser);

  let used = [...selected].reduce((sum, index) => sum + cost(source[index]!), 0);
  const candidates = source.map((message, index) => ({message, index}))
    .filter((item) => !selected.has(item.index))
    .sort((left, right) => {
      const rank = (priority: ContextMessage["priority"]): number =>
        priority === "critical" ? 0 : priority === "discardable" ? 2 : 1;
      return rank(left.message.priority) - rank(right.message.priority) || right.index - left.index;
    });
  for (const candidate of candidates) {
    const candidateCost = cost(candidate.message);
    if (used + candidateCost <= inputBudgetTokens) {
      selected.add(candidate.index);
      used += candidateCost;
    }
  }

  let truncatedMessages = 0;
  const ordered = [...selected].sort((left, right) => left - right)
    .map((index) => source[index]!);
  let estimatedInputTokens = ordered.reduce((sum, message) => sum + cost(message), 0);
  if (estimatedInputTokens > inputBudgetTokens) {
    const latestIndex = ordered.length - 1;
    if (latestIndex >= 0) {
      const available = inputBudgetTokens - (estimatedInputTokens - cost(ordered[latestIndex]!));
      ordered[latestIndex] = truncateToTokens(ordered[latestIndex]!, Math.max(1, available));
      truncatedMessages = 1;
      estimatedInputTokens = ordered.reduce((sum, message) => sum + cost(message), 0);
    }
  }
  const droppedMessages = source.length - ordered.length;
  const warnings: string[] = [];
  if (droppedMessages > 0) warnings.push(droppedMessages + " mensagem(ns) antiga(s) foram removidas do orçamento.");
  if (truncatedMessages > 0) warnings.push("A mensagem mais recente foi truncada para preservar espaço de resposta.");
  return {
    messages: ordered,
    maxContextTokens,
    reservedGenerationTokens,
    inputBudgetTokens,
    estimatedInputTokens,
    droppedMessages,
    truncatedMessages,
    warnings,
  };
}
