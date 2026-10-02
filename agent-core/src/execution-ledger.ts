import {capabilityFor, type RuntimeToolName} from "./capability-registry.ts";
import {callIdentity} from "./task-continuity.ts";

type ObservedCall = {tool: RuntimeToolName; arguments: Record<string, unknown>; ok: boolean};

/** Task-local progress for stable observations; polling remains live. */
export class ExecutionLedger {
  private readonly observations = new Map<string, {attempts: number; succeeded: boolean}>();

  constructor(history: readonly ObservedCall[] = []) {
    for (const call of history) {
      // A past mutation can invalidate earlier reads. Its full response may
      // no longer be in the compact history, so invalidate conservatively.
      if (capabilityFor(call.tool).effect === "workspace_write") this.invalidate();
      else this.observe(call, call.ok);
    }
  }

  private stable(tool: RuntimeToolName): boolean {
    const capability = capabilityFor(tool);
    return capability.retry === "safe_read" && capability.group !== "context"
      && tool !== "process_status" && tool !== "list_tools"
      && tool !== "list_sources" && tool !== "cite_sources";
  }

  wouldRepeat(call: Pick<ObservedCall, "tool" | "arguments">): boolean {
    if (!this.stable(call.tool)) return false;
    const previous = this.observations.get(callIdentity(call.tool, call.arguments));
    return previous !== undefined && (previous.succeeded || previous.attempts >= 2);
  }

  observe(call: Pick<ObservedCall, "tool" | "arguments">, succeeded: boolean): void {
    if (!this.stable(call.tool)) return;
    const key = callIdentity(call.tool, call.arguments);
    const previous = this.observations.get(key);
    this.observations.set(key, {attempts: (previous?.attempts ?? 0) + 1,
      succeeded: succeeded || previous?.succeeded === true});
  }

  invalidate(): void { this.observations.clear(); }
}
