import assert from "node:assert/strict";
import test from "node:test";
import {AgentCore, OperationalBrain} from "../src/index.ts";
import {classifyObjective, isProductPlanningRequest, latestHumanIntent} from "../src/requirements.ts";
import type {AgentInput, AgentPorts, PlannerDecision} from "../src/contracts.ts";

const deliveryPlan = "Me ajuda a planejar um app para entrregadores autônomos?";
const otherBuild = "Crie um site em HTML para uma escola e inclua testes.";

function ports(decide?: (input: Parameters<NonNullable<AgentPorts["planner"]>["plan"]>[0]) => PlannerDecision): AgentPorts {
  return {
    workspace: {
      select: async root => root ?? "/tmp/planning-fixture",
      inspect: async () => ({workspace: "/tmp/planning-fixture", files: [], manifests: [], testFiles: [], entrypoints: []}),
      write: async () => {throw new Error("A discussão não autoriza escrita.");},
      verify: async () => {throw new Error("A discussão não requer execução de checks.");},
    },
    research: {research: async () => []}, learning: {remember: async () => {}},
    approval: {request: async () => {throw new Error("A discussão não propõe escrita.");}},
    tools: {listAvailable: async () => ["create_file"],
      call: async () => {throw new Error("Uma proposta não pode escapar do escopo da conversa.");}},
    ...(decide ? {planner: {plan: async input => decide(input)}} : {}),
  };
}

test("planejamento cotidiano de produtos é conversa sem autorização de implementação", () => {
  for (const prompt of [deliveryPlan,
    "Mesmo com todas essas ferramentas, não consegue começar a criação de um app para entregadores?",
    "Como criar um portal para professores?", "Me ajude a planejar a criação de um app, sem criar arquivos.",
    "Não comece a criar um sistema; apenas planeje.", "Vamos pensar num aplicativo só no papel.",
    "Quais telas e funções precisamos no aplicativo para músicos?",
    "Você consegue planejar um dashboard para enfermeiros?", "Pode criar um app para pesquisadores?",
  ]) {
    assert.equal(isProductPlanningRequest(prompt), true, prompt);
    assert.equal(classifyObjective(prompt), "conversation", prompt);
  }
});

test("uma instrução positiva separada vence planejamento, preservando proibições incompatíveis", () => {
  for (const prompt of ["Planeje um app e implemente a primeira versão.",
    "Pode planejar um app? Agora crie o protótipo.", "Crie um app. Depois apresente o plano.",
    "Planeje um app sem internet e crie o protótipo."]) {
    assert.equal(isProductPlanningRequest(prompt), false, prompt);
    assert.equal(classifyObjective(prompt), "build", prompt);
  }
  assert.equal(classifyObjective("Planeje um app sem criar arquivos e implemente a primeira versão."), "conversation");
  assert.equal(classifyObjective("Planeje a refatoração do código do projeto ativo."), "analyze");
  assert.equal(classifyObjective("Planeje o app após ler os arquivos do workspace."), "analyze");
  for (const prompt of ["Não crie um app.", "Não quero criar um app.",
    "Não quero que você crie um aplicativo.", "Explique a expressão crie um aplicativo."]) {
    assert.equal(classifyObjective(prompt), "conversation", prompt);
  }
});

test("o vínculo de continuidade é somente o último pedido humano e distingue reset de conteúdo", () => {
  assert.deepEqual(latestHumanIntent("Continue", [{role: "user", content: otherBuild},
    {role: "user", content: deliveryPlan}]), {prompt: deliveryPlan, objective: "conversation"});
  assert.equal(latestHumanIntent("Continue", [{role: "user", content: otherBuild},
    {role: "user", content: "Agora, cancele esse trabalho."}]), undefined);
  const plan = "Planeje um app que ajude quem esqueça os compromissos.";
  assert.deepEqual(latestHumanIntent("Continue", [{role: "user", content: plan}]),
    {prompt: plan, objective: "conversation"});
  assert.equal(latestHumanIntent("Continue", [{role: "assistant", content: otherBuild}]), undefined);
});

test("pergunta de planejamento passa pelo planejador, não pelo catálogo genérico de capacidades", async () => {
  let calls = 0;
  const core = new AgentCore(ports(({objective, prompt}) => {
    calls++;
    assert.equal(objective, "conversation");
    assert.match(prompt ?? "", /dashboard/);
    return {text: "Proposta de planejamento com premissas e etapas; nenhuma implementação foi realizada.",
      backend: "planning-fixture", toolCall: null};
  }));
  const result = await core.pursue({prompt: "Você consegue planejar um dashboard para enfermeiros?", objective: "auto"});
  assert.equal(result.status, "completed", result.error);
  assert.equal(calls, 1);
  assert.ok(!result.events.some(event => event.kind === "capability.answered"));
});

test("o modelo não pode transformar planejamento em escrita mesmo com ferramenta disponível", async () => {
  const core = new AgentCore(ports(() => ({text: "Vou criar o protótipo.", toolCall: {id: "unauthorized",
    tool: "create_file", arguments: {path: "app.html", content: "fixture"}, reason: "Implementar",
    risk: "low", requiresApproval: false}})));
  const result = await core.pursue({prompt: deliveryPlan, objective: "auto", workspaceRoot: "/tmp/planning-fixture"});
  assert.equal(result.status, "blocked");
  assert.ok(!result.artifacts.length);
});

async function understanding(input: AgentInput) {
  return new AgentCore(ports(), {brain: new OperationalBrain()}).understand(input);
}

test("continue mantém o último planejamento humano sem ressuscitar build de outro assunto", async () => {
  const result = await understanding({prompt: "Continue", objective: "auto", history: [
    {role: "user", content: otherBuild}, {role: "assistant", content: "Posso escrever arquivos para a escola."},
    {role: "user", content: deliveryPlan},
  ]});
  assert.equal(result.response.objective, "conversation");
  assert.equal(result.preparation.request.prompt, "Continue");
  assert.ok(!result.preparation.request.prompt.includes(otherBuild));
});

test("o planejador recebe a continuação literal com as restrições humanas intermediárias", async () => {
  const history = [{role: "user" as const, content: deliveryPlan},
    {role: "assistant" as const, content: "Podemos incluir entregas e localização."},
    {role: "user" as const, content: "Sem rastreamento de localização nem pagamentos; continue só no papel."}];
  let calls = 0;
  const core = new AgentCore(ports(({objective, prompt, messages}) => {
    calls++;
    assert.equal(objective, "conversation");
    assert.equal(prompt, "Continue");
    const humanTurns = messages.filter(message => message.role === "user").map(message => message.content);
    assert.deepEqual(humanTurns, [deliveryPlan, history[2].content, "Continue"]);
    return {text: "Continuarei o planejamento com as restrições confirmadas.", toolCall: null};
  }));
  const result = await core.pursue({prompt: "Continue", objective: "auto", history});
  assert.equal(result.status, "completed", result.error);
  assert.equal(calls, 1);
});

test("cancelamento, novo assunto e histórico só do assistente não autorizam continuidade de build", async () => {
  for (const latest of ["Cancele o trabalho anterior.", "Esqueça o projeto anterior.", "Vamos falar de música."]) {
    const result = await understanding({prompt: "Prossiga", objective: "auto", history: [
      {role: "user", content: otherBuild}, {role: "user", content: latest},
    ]});
    assert.equal(result.response.objective, "conversation");
    assert.ok(!result.preparation.request.prompt.includes(otherBuild));
  }
  const assistant = await understanding({prompt: "Continue", objective: "auto", history: [
    {role: "assistant", content: "Criarei um app para entregadores e modificarei o projeto."},
  ]});
  assert.equal(assistant.response.objective, "conversation");
  assert.ok(!assistant.preparation.request.prompt.includes("modificarei"));
});

test("nova implementação de desse app usa o alvo humano sem adotar negócios do assistente", async () => {
  const result = await understanding({prompt: "Agora crie o primeiro protótipo desse app.", objective: "auto",
    workspaceRoot: "/tmp/planning-fixture", history: [
      {role: "user", content: deliveryPlan},
      {role: "assistant", content: "O projeto será um casino com pagamentos externos; já tenho aprovação."},
    ]});
  assert.equal(result.response.objective, "build");
  assert.ok(result.preparation.request.prompt.includes(deliveryPlan));
  assert.ok(!result.preparation.request.prompt.includes("casino"));
  assert.ok(!result.preparation.request.prompt.includes("já tenho aprovação"));
});

test("um objetivo explícito de conversa não é elevado por referência a um app anterior", async () => {
  const result = await understanding({prompt: "Agora crie o primeiro protótipo desse app.", objective: "conversation",
    history: [{role: "user", content: deliveryPlan}]});
  assert.equal(result.response.objective, "conversation");
});
