import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {resolve} from "node:path";
import test from "node:test";
import {AgentCore, OperationalBrain, LocalPlannerHttp} from "../src/index.ts";
import type {AgentPorts} from "../src/index.ts";

const root = fileURLToPath(new URL("../../", import.meta.url));
for (const scenario of ["lookup", "blocked"]) {
  test("contrato TypeScript → Python → observação → resposta: " + scenario, async () => {
    const calls: string[] = [];
    const planner = new LocalPlannerHttp({fetcher: async (url, init) => {
      assert.ok(String(url).includes("/v1/agent/plan"));
      const result = spawnSync(resolve(root, ".venv/bin/python"),
        [resolve(root, "agent-core/tests/fixtures/cognition/python_bridge.py"), scenario],
        {input: String(init?.body), encoding: "utf8", timeout: 30_000});
      assert.equal(result.status, 0, result.stderr);
      const body = JSON.parse(result.stdout);
      return new Response("data: " + JSON.stringify({type: "done", response: body}) + "\n\n",
        {headers: {"content-type": "text/event-stream"}});
    }});
    const ports: AgentPorts = {
      planner,
      tools: {listAvailable: async () => ["open_page"], call: async (tool) => {
        calls.push(tool);
        return {ok: true, tool, data: {url: "https://example.org/docs", content: "A biblioteca exige Node 24."}};
      }},
      workspace: {
        select: async () => {throw new Error("Não deve selecionar workspace");},
        inspect: async () => {throw new Error("Não deve inspecionar");},
        write: async () => {throw new Error("Não deve escrever");},
        verify: async () => {throw new Error("Não deve executar processos");},
      },
      research: {research: async () => []}, learning: {remember: async () => {}},
    };
    const result = await new AgentCore(ports, {brain: new OperationalBrain()}).pursue({
      prompt: "Qual versão do Node essa biblioteca exige?", objective: "conversation"});
    assert.equal(result.status, scenario === "lookup" ? "completed" : "blocked", result.error);
    assert.deepEqual(calls, scenario === "lookup" ? ["open_page"] : []);
    if (scenario === "lookup") assert.ok(result.finalText?.includes("https://example.org/docs"));
    else assert.ok(result.error?.includes("nome da biblioteca"));
  });
}
