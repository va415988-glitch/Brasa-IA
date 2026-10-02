import assert from "node:assert/strict";
import test from "node:test";
import {contextualBuildPrompt} from "../src/build-continuity.ts";
import {evaluateTaskAcceptance} from "../src/task-acceptance.ts";

test("análise estrutural não conclui um pedido de testes de comportamento", () => {
  for (const kind of ["syntax", "typecheck", "build", "combined"] as const) {
    const report = evaluateTaskAcceptance({objective: "testing", prompt: "Execute os testes do projeto",
      successfulTools: ["project_checks"], finalText: "Verifiquei o projeto.",
      verification: {executed: true, passed: true, kind, behaviorExecuted: false, summary: "Sintaxe válida", evidence: []}});
    assert.equal(report.passed, false);
    assert.ok(report.pending.some(check => check.id === "verification.behavior"));
  }
  assert.equal(evaluateTaskAcceptance({objective: "testing", prompt: "Execute os testes do projeto",
    successfulTools: ["project_checks"], finalText: "A suíte falhou em um caso.",
    verification: {executed: true, passed: false, kind: "test", behaviorExecuted: true, testsExecuted: 1, summary: "Um teste falhou", evidence: []}}).passed, true);
});
import {operationalPolicyFor} from "../src/capability-registry.ts";
import {analyzeRequirements, classifyObjective, explicitCorrectionRequest, repairRequested} from "../src/requirements.ts";

const goal = "Crie uma interface base para um ambiente de criação de jogos de computador";
const ui = "Agora, isso precisa de uma interface para que eu possa interagir";
const stack = "Rust, Typescript e Html parecem boas opções";
const history = (...content: string[]) => content.map(content => ({role: "user" as const, content}));

test("preserva o produto e a stack nos três complementos do caso real", () => {
  for (const [prompt, previous] of [
    [ui, [goal]], [stack, [goal, ui]],
    ["Crie uma interface usando rust, typescript, css e html", [goal, ui, stack]],
  ] as const) {
    const result = contextualBuildPrompt({prompt, objective: "auto", history: history(...previous)});
    assert.ok(result?.includes(goal));
    assert.ok(result?.includes(prompt));
  }
});

test("trata pedido de primeira versão funcional como build e mantém o complemento de interface", () => {
  const goal = "Este projeto precisa de melhorias para uma primeira versão funcional";
  const followUp = "Ele precisa de uma interface, primeiramente";
  assert.equal(classifyObjective(goal), "build");
  assert.equal(classifyObjective("Ele é um projeto incompleto. Consegue me ajudar a chegar na primeira versão funcional dele?"), "build");
  const result = contextualBuildPrompt({
    prompt: followUp,
    objective: "auto",
    history: [
      {role: "user", content: goal},
      {role: "assistant", content: "Estou acompanhando. Pode me contar um pouco mais?"},
      {role: "user", content: followUp},
    ],
  });
  assert.ok(result?.includes(goal));
  assert.ok(result?.includes(followUp));
});

test("encaminha pedidos explícitos de refinamento da interface como continuação do build", () => {
  const prompts = [
    "Ajuste a interface: aumente o contraste e deixe os botões mais fáceis de encontrar.",
    "Mude a tela inicial para destacar as entregas atrasadas.",
    "Deixe a navegação mais simples e acrescente busca por cliente.",
  ];
  for (const prompt of prompts) {
    const result = contextualBuildPrompt({prompt, objective: "auto", history: history(goal)});
    assert.equal(classifyObjective(prompt), "conversation");
    assert.ok(result?.includes(goal), prompt);
    assert.ok(result?.includes(prompt), prompt);
    assert.equal(contextualBuildPrompt({prompt, objective: "auto"}), undefined, prompt);
  }
});

test("retoma o build quando o pedido usa referência pronominal ao produto", () => {
  for (const prompt of ["Quero trabalhar nela.", "Vamos continuar nisso."]) {
    const result = contextualBuildPrompt({prompt, objective: "auto", history: history(goal)});
    assert.ok(result?.includes(goal), prompt);
    assert.ok(result?.includes(prompt), prompt);
    assert.equal(contextualBuildPrompt({prompt, objective: "auto"}), undefined, prompt);
    assert.equal(contextualBuildPrompt({prompt, objective: "auto",
      history: history(goal, "Vamos falar de música")}), undefined, prompt);
  }
  assert.equal(contextualBuildPrompt({prompt: "Não quero trabalhar nela.", objective: "auto",
    history: history(goal)}), undefined);
});

test("não inventa continuidade ou reutiliza resposta errada do assistente", () => {
  assert.equal(contextualBuildPrompt({prompt: stack, objective: "auto"}), undefined);
  assert.equal(contextualBuildPrompt({prompt: "O que é Rust?", objective: "auto", history: history(goal)}), undefined);
  assert.equal(contextualBuildPrompt({prompt: "Não quero interface agora", objective: "auto", history: history(goal)}), undefined);
  assert.equal(contextualBuildPrompt({prompt: "Analise a interface do projeto", objective: "auto", history: history(goal)}), undefined);
  assert.equal(contextualBuildPrompt({prompt: ui, objective: "conversation", history: history(goal)}), undefined);
  assert.equal(contextualBuildPrompt({prompt: ui, objective: "auto", history: history(goal, "Vamos falar de música")}), undefined);
  assert.equal(contextualBuildPrompt({prompt: "Crie uma interface para uma loja", objective: "auto", history: history(goal)}), undefined);
  const result = contextualBuildPrompt({prompt: ui, objective: "auto", history: [
    ...history(goal), {role: "assistant", content: "Criei uma lista de tarefas em Python."},
  ]});
  assert.ok(result?.includes(goal));
  assert.ok(!result?.includes("lista de tarefas"));
});

test("classifica construção com verificação e registra tecnologias concretas", () => {
  assert.equal(classifyObjective(
    "Crie o primeiro protótipo de um app para entregadores registrarem as entregas feitas num determinado período e os quilômetros rodados nesse tempo",
  ), "build");
  assert.equal(classifyObjective("Crie testes para o aplicativo de entregas"), "testing");
  assert.equal(classifyObjective("Revise o arquivo ativo procurando bugs, riscos e melhorias priorizadas."), "analyze");
  assert.equal(classifyObjective("Projete testes úteis para o código ativo e explique como executá-los."), "analyze");
  assert.equal(classifyObjective("Rode os testes do aplicativo de entregas"), "testing");
  assert.equal(classifyObjective("Crie uma interface e teste o projeto"), "build");
  assert.equal(classifyObjective("Crie um backend e teste o aplicativo"), "build");
  assert.equal(classifyObjective("Preciso de uma interface para cadastrar clientes"), "build");
  const constraints = analyzeRequirements("Crie uma interface usando Rust, TypeScript, CSS e HTML").constraints;
  const text = constraints.map(item => item.text).join(" ");
  for (const language of ["Rust", "TypeScript", "CSS", "HTML"]) assert.ok(text.includes(language));
  assert.ok(!text.includes("(?:"));
});

test("criar testes exige escrita e verificação; executar testes preserva modo de leitura", () => {
  const creation = "Crie testes para o aplicativo de entregas";
  const createPolicy = operationalPolicyFor({taskId: "test-creation", prompt: creation,
    objective: "testing", workspaceRoot: "/tmp/projeto"});
  assert.equal(createPolicy.mode, "mutating");
  assert.ok(createPolicy.allowedTools.includes("create_file"));
  assert.ok(createPolicy.allowedTools.includes("project_checks"));
  assert.equal(evaluateTaskAcceptance({objective: "testing", prompt: creation,
    finalText: "Executei o check existente.", successfulTools: ["project_checks"],
    verification: {executed: true, passed: true, summary: "check passou", evidence: []}}).passed, false);
  assert.equal(evaluateTaskAcceptance({objective: "testing", prompt: creation,
    finalText: "Criei tests/test_app.py e executei o check após a escrita.",
    successfulTools: ["create_file", "project_checks"], hasChanges: true,
    verification: {executed: true, passed: true, summary: "check passou", evidence: []}}).passed, true);
  const runPolicy = operationalPolicyFor({taskId: "test-run", prompt: "Rode os testes do projeto",
    objective: "testing", workspaceRoot: "/tmp/projeto"});
  assert.equal(runPolicy.mode, "read_only");
  assert.ok(!runPolicy.allowedTools.includes("create_file"));
});

test("relato real de erro inicia debug com reparo e mantém perguntas diagnósticas sem escrita", () => {
  const report = 'a pagina web criada retornou esse erro na interface: Unexpected token \'<\', "<!DOCTYPE "... is not valid JSON\nArquivo ativo: index.html · html · 70 linhas · cursor na linha 1.';
  assert.equal(classifyObjective(report), "debug");
  assert.equal(repairRequested(report), true);
  const policy = operationalPolicyFor({taskId: "web-error", prompt: report,
    objective: "debug", workspaceRoot: "/tmp/projeto"});
  assert.equal(policy.mode, "mutating");
  assert.ok(policy.allowedTools.includes("read_file"));
  assert.ok(policy.allowedTools.includes("project_checks"));
  assert.ok(policy.allowedTools.includes("edit_file"));
  assert.equal(classifyObjective("Por que a página do meu projeto retornou erro 500?"), "debug");
  assert.equal(classifyObjective("Os testes do projeto falharam."), "debug");
  assert.equal(repairRequested("Por que a página do meu projeto retornou erro 500?"), false);
  const diagnosticPolicy = operationalPolicyFor({taskId: "diagnose-web-error",
    prompt: "Por que a página do meu projeto retornou erro 500?",
    objective: "debug", workspaceRoot: "/tmp/projeto"});
  assert.equal(diagnosticPolicy.mode, "read_only");
  assert.ok(diagnosticPolicy.allowedTools.includes("research_web"));
  assert.ok(!diagnosticPolicy.allowedTools.includes("edit_file"));
  assert.equal(repairRequested("A página do projeto tem erro, mas não corrija; apenas investigue."), false);
  assert.equal(classifyObjective("Como investigar um erro de JSON em geral?"), "conversation");
});

test("classifica avaliação controlada com fixture como build e ignora verbos de escrita negados", () => {
  const prompt = `Faça uma avaliação controlada da sua capacidade de criar e verificar um projeto.
Não altere o workspace atual. No workspace isolado, crie somente estes arquivos: package.json e src/calculadora.js.
Execute os checks. Se ocorrer falha, pare e relate o resultado.`;
  assert.equal(classifyObjective(prompt), "build");
  assert.equal(classifyObjective("Faça uma avaliação controlada dos checks disponíveis, sem criar arquivos."), "testing");
  assert.equal(explicitCorrectionRequest("Não altere o workspace atual. Não edite arquivos."), false);
  assert.equal(explicitCorrectionRequest("Depure este projeto e corrija o erro."), true);
});

test("pedido explícito de planejamento sem escrita segue rota somente leitura", () => {
  const prompt = `Quero um jeito simples de organizar meus estudos que eu possa usar no navegador.

Por enquanto, faça somente análise e planejamento. Inspecione o workspace ativo e as ferramentas disponíveis, mas não crie, edite ou exclua arquivos, nem execute ações que alterem o projeto. Não use a internet.

Decida qual seria a menor versão útil para esse objetivo. Apresente premissas, um plano ordenado, arquivos que propõe criar, critérios de verificação e riscos. Não diga que implementou ou verificou nada: nesta etapa, apenas planeje.`;
  assert.equal(classifyObjective(prompt), "analyze");

  const policy = operationalPolicyFor({
    taskId: "plan-only-regression",
    prompt,
    objective: "analyze",
    workspaceRoot: "/tmp/assistente",
  });
  assert.equal(policy.mode, "read_only");
  assert.ok(policy.allowedTools.includes("inspect_project"));
  assert.ok(policy.allowedTools.includes("list_tools"));
  assert.ok(!policy.allowedTools.includes("apply_batch"));
  assert.ok(!policy.allowedTools.includes("create_file"));

  const acceptance = evaluateTaskAcceptance({
    objective: "analyze",
    prompt,
    finalText: "Recomendo, com base em package.json, criar a pasta organizador-estudos e depois validar os fluxos definidos. Riscos: dados locais ficam neste navegador.",
    successfulTools: ["inspect_project", "list_tools", "read_file"],
    readPaths: ["package.json"],
  });
  assert.equal(acceptance.passed, true, acceptance.pending.map(check => check.id).join(", "));
  assert.ok(!acceptance.checks.some(check => check.id === "build.change" || check.id === "build.verification"));
});


test("relatos cotidianos de falhas na interface iniciam diagnóstico e permitem reparo", () => {
  for (const prompt of [
    `A interface está mostrando a mensagem "Unexpected token '<', \"<!DOCTYPE \"... is not valid JSON"`,
    "A tela não carrega.", "O botão não responde.", "O formulário não salva.", "O painel travou.",
  ]) {
    assert.equal(classifyObjective(prompt), "debug", prompt);
    assert.equal(repairRequested(prompt), true, prompt);
    const policy = operationalPolicyFor({taskId: "implicit-debug", prompt,
      objective: "debug", workspaceRoot: "/tmp/projeto"});
    assert.ok(policy.allowedTools.includes("read_file"), prompt);
    assert.ok(policy.allowedTools.includes("edit_file"), prompt);
    assert.ok(policy.allowedTools.includes("project_checks"), prompt);
  }
  assert.equal(classifyObjective("Como investigar um erro na interface em geral?"), "conversation");
  assert.equal(repairRequested("Só explique o erro da interface, sem alterar arquivos."), false);
  assert.equal(classifyObjective("A interface está funcionando."), "conversation");
});

test("diagnóstico implícito pode concluir com causa e ação observadas sem inventar escrita", () => {
  const diagnosis = evaluateTaskAcceptance({objective: "debug",
    prompt: "A interface está mostrando Unexpected token '<'.",
    finalText: "Em index.html, fetch('/api/tasks') usa caminho relativo. O README.md informa que a API inicia na porta 8765. Isso indica que abrir a página em outro servidor pode devolver HTML ao parser JSON. Inicie app.py e abra http://127.0.0.1:8765.",
    successfulTools: ["read_file", "project_checks"], readPaths: ["index.html", "README.md"],
    correctionRequested: false, verification: {executed: true, passed: true, summary: "checks passaram", evidence: []}});
  assert.equal(diagnosis.passed, true, diagnosis.pending.map(check => check.id).join(", "));

  const weak = evaluateTaskAcceptance({objective: "debug", prompt: "A interface falhou.",
    finalText: "Li o projeto. Verificação aprovada.", successfulTools: ["read_file"],
    readPaths: ["index.html"], correctionRequested: false,
    verification: {executed: true, passed: true, summary: "checks passaram", evidence: []}});
  assert.equal(weak.passed, false);
  assert.ok(weak.pending.some(check => check.id === "debug.actionable"));

  const staleVerification = evaluateTaskAcceptance({objective: "debug", prompt: "Corrija a falha.",
    finalText: "Corrigi index.html.", successfulTools: ["read_file", "project_checks"],
    readPaths: ["index.html"], hasChanges: false, correctionRequested: true,
    verification: {executed: true, passed: true, summary: "checks passaram antes da edição", evidence: []}});
  assert.ok(staleVerification.pending.some(check => check.id === "debug.change"));
  assert.ok(staleVerification.pending.some(check => check.id === "debug.verification"));
});
