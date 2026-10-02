import {LocalPlannerHttp, plannerToolSchemas} from "./planner.ts";

/** Remote inference only; local planner validation and execution policy still apply. */
export function createColabPlanner(url: string, token: string): LocalPlannerHttp {
  const endpoint = new URL(url);
  if (endpoint.protocol !== "https:" || endpoint.username || endpoint.password
      || endpoint.pathname !== "/" || endpoint.search || endpoint.hash) {
    throw new Error("BRASA_COLAB_URL deve ser uma origem HTTPS sem caminho ou credenciais.");
  }
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(token)) {
    throw new Error("Configure BRASA_COLAB_TOKEN com a chave gerada no notebook.");
  }
  const request = async (path: string, init: RequestInit, signal: AbortSignal) => {
    let response: Response;
    try {
      response = await fetch(endpoint.origin + path, {...init, signal, redirect: "error",
        headers: {"content-type": "application/json", authorization: "Bearer " + token}});
    } catch {
      throw new Error("Colab inacessível ou tempo de geração esgotado. Confira a sessão e a URL do túnel.");
    }
    if (!response.ok) throw new Error(response.status === 401 ? "Chave do Colab recusada."
      : response.status === 409 ? "A GPU do Colab está ocupada. Aguarde a geração atual."
      : "Colab respondeu HTTP " + response.status + ". Confira a sessão do notebook.");
    return response.json() as Promise<Record<string, unknown>>;
  };
  return new LocalPlannerHttp({fetcher: async (path, init) => {
    const payload = JSON.parse(String(init?.body ?? "{}"));
    const signal = AbortSignal.timeout(600_000);
    const job = await request("/jobs", {method: "POST", body: JSON.stringify({
      ...payload, tools: Object.fromEntries(Object.entries(plannerToolSchemas()).filter(([name]) =>
        !Array.isArray(payload.cognition?.available_tools) || payload.cognition.available_tools.includes(name))),
    })}, signal);
    if (typeof job.id !== "string" || !/^[a-f0-9]{32}$/.test(job.id)) throw new Error("Colab retornou um job inválido.");
    let result: Record<string, unknown>;
    do {
      await new Promise(resolve => setTimeout(resolve, 1000));
      result = await request("/jobs/" + job.id, {method: "GET"}, signal);
      if (result.status === "failed") throw new Error("O Qwen não concluiu a proposta: " + String(result.error ?? "erro de geração").slice(0, 300));
      if (!["running", "done"].includes(String(result.status))) throw new Error("Estado de geração inválido no Colab.");
    } while (result.status === "running");
    if (!result.response || typeof result.response !== "object") throw new Error("Resposta vazia do Colab.");
    const body = String(path).endsWith("/stream")
      ? "data: " + JSON.stringify({type: "done", response: result.response}) + "\n\n"
      : JSON.stringify(result.response);
    return new Response(body, {headers: {"content-type": String(path).endsWith("/stream")
      ? "text/event-stream" : "application/json"}});
  }});
}
