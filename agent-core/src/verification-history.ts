import type {PlannerMessage} from "./contracts.ts";

/** Deliver the actual first and last checks, including an observed failure. */
export function verificationHistoryText(messages: readonly PlannerMessage[]): string {
  const runs: Array<Record<string, unknown>> = [];
  for (const message of messages) {
    if (message.role !== "tool") continue;
    try {
      const result = JSON.parse(message.content);
      const data = result?.data;
      if (result?.ok === true && result.tool === "project_checks" && data?.executed === true
          && typeof data.passed === "boolean") runs.push(data);
    } catch { /* Only structured runtime observations are evidence. */ }
  }
  if (runs.length < 2 || runs[0].passed !== false || runs.at(-1)?.passed !== true) return "";
  return "\n\nExecuções observadas:\n" + [runs[0], runs.at(-1)!].map((run, index) => {
    const count = typeof run.tests_executed === "number" ? ` · ${run.tests_executed} teste(s)` : "";
    const command = typeof run.command === "string" ? run.command : String(run.check ?? "project_checks");
    const output = [run.stdout, run.stderr].filter(value => typeof value === "string" && value.trim()).join("\n");
    const safeOutput = (output.length > 3500 ? output.slice(0, 1200) + "\n[saída limitada]\n" + output.slice(-2300) : output)
      .replaceAll("```", "` ` `");
    return `${index === 0 ? "Primeira execução" : "Última execução"}: ${run.passed ? "aprovada" : "falhou"}${count}.\n`
      + "```text\n" + command.replaceAll("```", "` ` `") + (safeOutput ? "\n" + safeOutput : "") + "\n```";
  }).join("\n\n");
}
