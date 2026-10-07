import assert from "node:assert/strict";
import {mkdtempSync, readFileSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";
import {
  CognitiveBrain,
  OperationalBrain,
  CognitiveStateMachine,
  InMemoryBrainEventLog,
  JsonlBrainEventLog,
  PlanExecutor,
  allowedBrainTransitions,
  analyzeRequirements,
  classifyObjective,
  operationalPolicyFor,
  runtimeCapabilities,
} from "../src/index.ts";
import {reportedProjectFailure} from "../src/requirements.ts";

test("perguntas sobre habilidades não executam as ações mencionadas", () => {
  assert.equal(classifyObjective("Quais habilidades você pode usar para pesquisar e testar um projeto?"), "conversation");
  assert.equal(classifyObjective("Quais ferramentas estão disponíveis? Execute os testes do projeto."), "testing");
});

test("avaliar uma função com arquivo e entradas concretas não pede briefing de produto", () => {
  for (const prompt of ["Execute weighted([[2,5],[7,8]], bonus=3) em logic.py",
    "Teste merge_segments([]) em logic.py",
    "Avalie a função weighted em logic.py com argumentos JSON [[2,5]]"]) {
    assert.equal(classifyObjective(prompt), "operate");
    const analysis = analyzeRequirements(prompt);
    assert.equal(analysis.requiresClarification, false);
    assert.deepEqual(analysis.missingInformation, []);
  }
  assert.equal(analyzeRequirements("Execute alguma coisa.").requiresClarification, true);
});
import type {
  BrainApprovalPort,
  BrainClock,
  BrainPlan,
  ToolDefinition,
  RuntimeToolName,
} from "../src/index.ts";

function fixedClock(): BrainClock {
  let number = 0;
  return {now: () => "2026-09-22T00:00:0" + number++ + "Z"};
}

test("o núcleo registra as 36 ferramentas do runtime e aplica políticas por grupo", () => {
  const tools = Object.keys(runtimeCapabilities);
  assert.equal(tools.length, 36);
  for (const tool of ["inspect_code", "find_paths", "list_tree", "compare_files", "git_diff", "inspect_media", "calculate", "evaluate_function"]) {
    assert.ok(tools.includes(tool), `ferramenta ausente: ${tool}`);
    assert.ok(operationalPolicyFor({taskId: "tool-policy", prompt: "analisar", objective: "analyze"})
      .allowedTools.includes(tool as RuntimeToolName));
  }
  for (const tool of ["search_web", "open_page", "list_sources", "cite_sources"]) {
    assert.ok(operationalPolicyFor({taskId: "research-policy", prompt: "pesquisar", objective: "research"})
      .allowedTools.includes(tool as RuntimeToolName));
  }
  assert.equal(runtimeCapabilities.create_workspace.risk, "high");
  assert.equal(runtimeCapabilities.create_workspace.effect, "workspace_write");
  assert.equal(runtimeCapabilities.create_web_page.risk, "medium");
  assert.equal(operationalPolicyFor({taskId: "conversation-policy", prompt: "responder", objective: "conversation"})
    .allowedTools.includes("terminal_run"), false);
});

test("OperationalBrain não envia orientação procedural sem fonte validada", async () => {
  const preparation = await new OperationalBrain().prepare({
    taskId: "task-workflow-contract",
    prompt: "Inspecione o workspace e descreva a estrutura.",
    objective: "analyze",
    workspaceRoot: "/tmp/assistente",
  });
  assert.deepEqual(preparation.operational?.workflowGuidance, []);
  assert.deepEqual(preparation.operational?.dataset.matchedRecords, []);
});

test("analisa ambiguidade e nunca produz mais de três perguntas", () => {
  const analysis = analyzeRequirements("Quero criar um aplicativo.");
  assert.equal(analysis.requiresClarification, true);
  assert.ok(analysis.questions.length > 0);
  assert.ok(analysis.questions.length <= 3);
  assert.ok(analysis.missingInformation.includes("ambiente técnico"));
  assert.ok(analysis.acceptanceCriteria.some((row) => row.id === "acceptance-executable"));
});

test("permite planejar quando o pedido tem domínio, stack e resultado", () => {
  const analysis = analyzeRequirements(
    "Implemente em TypeScript uma função que divide uma lista em lotes de tamanho fixo. "
      + "Inclua testes para lista vazia, tamanho zero e caso normal.",
  );
  assert.equal(analysis.requiresClarification, false);
  assert.equal(analysis.objective, "build");
  assert.equal(analysis.questions.length, 0);
  assert.ok(analysis.acceptanceCriteria.length >= 3);
});

test("entende uma aplicação de tarefas para uma pessoa sem pedir escopo já informado", () => {
  const prompt = "Crie neste workspace uma aplicação web local de tarefas para uma pessoa, sem login nem serviços externos. "
    + "Deve permitir cadastrar tarefas, listar, concluir e excluir. Persista os dados em SQLite pelo backend, "
    + "valide títulos vazios e inclua testes para as operações e para a persistência. Se já houver uma stack "
    + "no workspace, use-a; se estiver vazio, escolha uma stack local simples e explique a escolha. Antes de "
    + "escrever arquivos, mostre um plano curto com os principais arquivos e testes e peça minha aprovação. "
    + "Depois implemente, execute os testes, corrija falhas com base nas saídas reais e rode os testes novamente. "
    + "No fim, informe como iniciar a aplicação e quais verificações passaram. Não diga que executou algo se não executou.";
  const analysis = analyzeRequirements(prompt);
  assert.equal(classifyObjective(prompt), "build");
  assert.equal(analysis.requiresClarification, false);
  assert.deepEqual(analysis.questions, []);
  assert.deepEqual(analysis.missingInformation, []);
  assert.ok(analysis.acceptanceCriteria.some((criterion) => criterion.id === "acceptance-tests"));
});

test("um objetivo de produto libera defaults seguros sem bloquear por stack ausente", () => {
  const prompt = "Quero criar um sistema para organizar as entregas da minha equipe.";
  const analysis = analyzeRequirements(prompt, [], "build");
  assert.equal(classifyObjective("Quero transformar isso em um produto web completo."), "build");
  assert.equal(analysis.requiresClarification, false);
  assert.ok(analysis.missingInformation.includes("ambiente técnico"));
  assert.equal(analysis.questions.length, 0);
});

test("pergunta de diagnóstico geral não exige workspace, mas inspeção explícita ainda é operacional", () => {
  assert.equal(classifyObjective(
    "Estou depurando um app que inicia, mas às vezes a janela não aparece. Não altere arquivos. Qual hipótese você investigaria primeiro e qual checagem segura faria?",
  ), "conversation");
  assert.equal(classifyObjective("Depure este projeto e leia os arquivos relacionados ao erro."), "debug");
});

test("relato concreto de erro web não abre questionário de escopo", () => {
  const prompt = 'A página retornou o seguinte erro: Unexpected token \'<\', "<!DOCTYPE "... is not valid JSON';
  const analysis = analyzeRequirements(prompt);
  assert.equal(reportedProjectFailure(prompt), true);
  assert.equal(classifyObjective(prompt), "debug");
  assert.equal(analysis.requiresClarification, false);
  assert.deepEqual(analysis.questions, []);
  assert.deepEqual(analysis.missingInformation, []);
  const terse = analyzeRequirements("A página tem um erro; faça algo.");
  assert.equal(terse.requiresClarification, false);
  assert.deepEqual(terse.questions, []);
});

test("a máquina cognitiva rejeita transições impossíveis e cria snapshot recuperável", () => {
  const brain = new CognitiveStateMachine("task-state", "build", fixedClock());
  assert.deepEqual(allowedBrainTransitions("observing"), ["clarifying", "planning", "abstaining"]);
  assert.throws(
    () => brain.transition("executing", "pular entendimento"),
    /Transição cognitiva inválida/,
  );
  brain.transition("planning", "pedido entendido");
  const snapshot = brain.snapshot();
  const restored = new CognitiveStateMachine("task-state", "build", fixedClock());
  restored.restore(snapshot);
  assert.equal(restored.current.kind, "planning");
  assert.equal(restored.events.length, 1);
  assert.equal(restored.events[0]?.to, "planning");
});

test("CognitiveBrain registra requisitos e persiste a decisão de clarificar", async () => {
  const log = new InMemoryBrainEventLog();
  const brain = new CognitiveBrain({eventLog: log, clock: fixedClock()});
  const preparation = await brain.prepare({
    taskId: "task-clarify",
    prompt: "Crie um sistema.",
    objective: "build",
  });
  assert.equal(preparation.state.kind, "clarifying");
  assert.equal((await log.read("task-clarify")).length, 1);
  assert.equal(preparation.analysis.questions.length, 3);
});

test("prévia cognitiva não grava eventos nem memória", async () => {
  const log = new InMemoryBrainEventLog();
  const remembered: string[] = [];
  const brain = new CognitiveBrain({eventLog: log, memory: {
    remember: async (entry) => { remembered.push(entry.key); },
    search: async () => [],
  }});
  const preparation = await brain.prepare({taskId: "task-preview", prompt: "Crie um sistema.", objective: "build", persist: false});
  assert.equal(preparation.state.kind, "clarifying");
  assert.deepEqual(await log.read("task-preview"), []);
  assert.deepEqual(remembered, []);
});

test("CognitiveBrain sai de clarifying quando as respostas fecham o escopo", async () => {
  const brain = new CognitiveBrain({clock: fixedClock()});
  const first = await brain.prepare({
    taskId: "task-resume",
    prompt: "Quero criar algo.",
    objective: "build",
  });
  const resumed = await brain.recordClarification(first, [
    "Uma função TypeScript para dividir listas.",
    "Node 22, sem dependências externas.",
    "Deve passar testes de caso normal e lista vazia.",
  ]);
  assert.equal(resumed.state.kind, "planning");
  assert.equal(resumed.analysis.requiresClarification, false);
});

test("clarificações sucessivas preservam respostas confirmadas anteriores", async () => {
  const brain = new CognitiveBrain();
  const first = await brain.prepare({taskId: "task-multi-clarify", prompt: "Crie um sistema.", objective: "build"});
  const second = await brain.recordClarification(first, ["Um sistema interno."]);
  const third = await brain.recordClarification(second, [
    "Resultado: acompanhar entregas ativas; usuários: equipe de operações; stack: React e TypeScript.",
  ]);
  assert.match(third.request.prompt, /Um sistema interno/);
  assert.match(third.request.prompt, /React e TypeScript/);
});

test("log JSONL mantém eventos e rejeita linha corrompida", async () => {
  const directory = mkdtempSync(join(tmpdir(), "ia-brain-log-"));
  const path = join(directory, "events.jsonl");
  try {
    const log = new JsonlBrainEventLog(path);
    const state = new CognitiveStateMachine("task-log", "research", fixedClock());
    state.transition("planning", "pedido entendido");
    await log.append(state.events[0]!);
    assert.equal((await log.read("task-log")).length, 1);
    assert.match(readFileSync(path, "utf8"), /task-log/);
    rmSync(path);
    const invalid = new JsonlBrainEventLog(path);
    await import("node:fs/promises").then(({writeFile}) => writeFile(path, "{invalido}\\n", "utf8"));
    await assert.rejects(invalid.read(), /Linha inválida/);
  } finally {
    rmSync(directory, {recursive: true, force: true});
  }
});

function tool(name: string, risk: ToolDefinition["risk"], handler: ToolDefinition["handler"]): ToolDefinition {
  return {
    name,
    risk,
    reversible: risk === "none" || risk === "low",
    description: name,
    handler,
  };
}

test("executor conclui plano de baixo risco somente após verificação", async () => {
  const state = new CognitiveStateMachine("task-execute", "build", fixedClock());
  state.transition("planning", "requisitos aprovados");
  const log = new InMemoryBrainEventLog();
  const calls: string[] = [];
  const plan: BrainPlan = {
    id: "plan-1",
    taskId: "task-execute",
    objective: "criar artefato",
    actions: [{
      id: "action-1",
      tool: "inspect",
      input: {path: "."},
      reason: "Ler a estrutura antes de escrever.",
      expectedEffect: "Retornar uma inspeção.",
    }],
  };
  const executor = new PlanExecutor({
    controller: state,
    eventLog: log,
    tools: [tool("inspect", "low", async (_input, context) => {
      calls.push(context.actionId);
      return {ok: true, summary: "Inspeção aprovada.", evidence: ["inspection.json"]};
    })],
  });
  const report = await executor.execute(plan);
  assert.equal(report.status, "completed");
  assert.deepEqual(calls, ["action-1"]);
  assert.deepEqual(report.evidence, ["inspection.json"]);
  assert.equal(state.current.kind, "completed");
  assert.ok((await log.read("task-execute")).length >= 5);
});

test("executor nunca executa ação de alto risco sem aprovação", async () => {
  const state = new CognitiveStateMachine("task-risk", "operate", fixedClock());
  state.transition("planning", "plano preparado");
  let called = false;
  const approval: BrainApprovalPort = {request: async () => false};
  const report = await new PlanExecutor({
    controller: state,
    approval,
    tools: [tool("delete", "high", async () => {
      called = true;
      return {ok: true, summary: "apagado"};
    })],
  }).execute({
    id: "plan-risk",
    taskId: "task-risk",
    objective: "apagar",
    actions: [{
      id: "delete-1",
      tool: "delete",
      input: {path: "old.txt"},
      reason: "Remover um arquivo fora da política.",
      expectedEffect: "Apagar o arquivo.",
    }],
  });
  assert.equal(report.status, "blocked");
  assert.equal(called, false);
  assert.equal(state.current.kind, "abstaining");
});

test("executor publica estados em tempo real antes de chamar a ferramenta", async () => {
  const state = new CognitiveStateMachine("task-live", "build", fixedClock());
  state.transition("planning", "plano preparado");
  const events: string[] = [];
  const report = await new PlanExecutor({
    controller: state,
    onEvent: (event) => events.push(event.to),
    tools: [tool("inspect", "low", async () => {
      assert.ok(events.includes("executing"), "a interface deve receber o estado antes da ação");
      return {ok: true, summary: "Inspeção concluída."};
    })],
  }).execute({
    id: "plan-live",
    taskId: "task-live",
    objective: "inspecionar",
    actions: [{
      id: "inspect-live",
      tool: "inspect",
      input: {},
      reason: "Observar antes de agir.",
      expectedEffect: "Retornar observação.",
    }],
  });
  assert.equal(report.status, "completed");
  assert.ok(events.indexOf("executing") < events.indexOf("verifying"));
  assert.equal(events.at(-1), "completed");
});

test("executor não repete exceção com efeito colateral indeterminado", async () => {
  const state = new CognitiveStateMachine("task-no-retry", "operate", fixedClock());
  state.transition("planning", "plano preparado");
  let calls = 0;
  const report = await new PlanExecutor({
    controller: state,
    tools: [tool("write", "low", async () => {
      calls += 1;
      throw new Error("conexão caiu depois da gravação");
    })],
  }).execute({
    id: "plan-no-retry",
    taskId: "task-no-retry",
    objective: "gravar uma vez",
    actions: [{
      id: "write-once",
      tool: "write",
      input: {},
      reason: "Gravar artefato.",
      expectedEffect: "Criar exatamente um artefato.",
      maxAttempts: 3,
    }],
  });
  assert.equal(report.status, "failed");
  assert.equal(report.actions[0]?.attempts, 1);
  assert.equal(calls, 1);
  assert.match(report.error ?? "", /depois da gravação/);
});

test("executor aborta timeout e deixa recuperação explícita", async () => {
  const state = new CognitiveStateMachine("task-timeout", "operate", fixedClock());
  state.transition("planning", "plano preparado");
  const report = await new PlanExecutor({
    controller: state,
    tools: [tool("slow", "low", async (_input, context) => {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, 100);
        context.signal.addEventListener("abort", () => {
          clearTimeout(timer);
          reject(new Error("cancelado pelo timeout"));
        }, {once: true});
      });
      return {ok: true, summary: "não deveria completar"};
    })],
  }).execute({
    id: "plan-timeout",
    taskId: "task-timeout",
    objective: "verificar timeout",
    actions: [{
      id: "slow-1",
      tool: "slow",
      input: {},
      reason: "Testar limite.",
      expectedEffect: "Parar quando exceder 5 ms.",
      timeoutMs: 5,
    }],
  });
  assert.equal(report.status, "failed");
  assert.match(report.error ?? "", /timeout|cancelado/);
  assert.equal(state.current.kind, "abstaining");
  assert.ok(state.events.some((event) => event.to === "recovering"));
});

test("política de conversa permite consultas e exige workspace para leitura local", () => {
  for (const workspaceRoot of [undefined, "/tmp/assistente"]) {
    const policy = operationalPolicyFor({taskId: "conversation", prompt: "Qual versão serve?", objective: "conversation", workspaceRoot});
    assert.ok(policy.allowedTools.includes("search_web"));
    assert.equal(policy.allowedTools.includes("read_file"), Boolean(workspaceRoot));
    assert.equal(policy.allowedTools.includes("create_file"), false);
    assert.equal(policy.allowedTools.includes("terminal_run"), false);
    assert.equal(policy.maxActions, 6);
  }
});

test("escrita autoral segue pelo cérebro criativo no chat, sem ferramentas de workspace", async () => {
  const {personalityLayersFor} = await import("../src/personality.ts");
  for (const prompt of [
    "Crie um poema sobre o mar",
    "Escreva um roteiro de vídeo para o lançamento do produto",
    "Crie uma campanha de marketing para uma padaria",
    "Escreva um e-mail para meu chefe pedindo férias",
    "Me dê 5 ideias de nome para uma cafeteria",
  ]) {
    const objective = classifyObjective(prompt);
    assert.equal(objective, "conversation", prompt);
    assert.equal(personalityLayersFor(prompt, objective).mode, "creative", prompt);
    const policy = operationalPolicyFor({prompt, objective} as Parameters<typeof operationalPolicyFor>[0]);
    assert.ok(!policy.allowedTools.some((tool) => ["create_file", "edit_file", "apply_batch"].includes(tool)), prompt);
  }
  // Artefatos de software e roteiros técnicos continuam fora da rota criativa.
  assert.equal(classifyObjective("Crie uma landing page para a campanha da padaria"), "build");
  assert.equal(classifyObjective("Crie uma API REST em Python para cadastro de clientes"), "build");
  assert.equal(classifyObjective("Escreva testes para a função de soma"), "testing");
});

test("perguntas sobre novidades, lançamentos e resultados consultam fontes atuais", () => {
  for (const prompt of ["Quais são as novidades do Python 3.14?", "Quem ganhou a copa do mundo de 2022?",
    "O que mudou no último release do Node.js?", "Qual a cotação do dólar hoje?"]) {
    assert.equal(classifyObjective(prompt), "research", prompt);
  }
  assert.equal(classifyObjective("O que é Rust?"), "conversation");
});
