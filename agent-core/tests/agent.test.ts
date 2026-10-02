import {readFileSync} from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";
import {AgentCore, CognitiveBrain, OperationalBrain, InMemoryBrainEventLog, classifyObjective, evaluateTaskAcceptance, explicitWorkspaceFilePaths, personalityLayersFor, projectAnalysisPaths} from "../src/index.ts";
import type {AgentPorts, Artifact, Evidence, ProjectInspection, Verification} from "../src/index.ts";

function fakePorts(calls: string[], existing = false, approved = true): AgentPorts {
  const inspection: ProjectInspection = {
    workspace: "/tmp/assistente",
    files: existing ? ["package.json", "src/index.ts"] : [],
    manifests: existing ? ["package.json"] : [],
    testFiles: existing ? ["tests/agent.test.ts"] : [],
    entrypoints: existing ? ["src/index.ts"] : [],
  };
  const evidence: Evidence[] = [{
    title: "TypeScript Handbook",
    url: "https://www.typescriptlang.org/docs/",
    excerpt: "Documentação da linguagem.",
  }];
  const verification: Verification = {
    passed: true,
    executed: true,
    summary: "Smoke test aprovado.",
    evidence: ["node --test"],
  };

  return {
    workspace: {
      select: async (root) => {
        calls.push("select:" + (root ?? "current"));
        return root ?? inspection.workspace;
      },
      inspect: async () => {
        calls.push("inspect");
        return inspection;
      },
      write: async (artifact: Artifact) => {
        calls.push("write:" + artifact.path);
        return artifact;
      },
      verify: async () => {
        calls.push("verify");
        return verification;
      },
    },
    research: {
      research: async () => {
        calls.push("research");
        return evidence;
      },
    },
    learning: {
      remember: async () => {
        calls.push("learn");
      },
    },
    approval: {
      request: async () => {
        calls.push("approval");
        return approved;
      },
    },
  };
}

test("o ciclo operacional impede releitura idêntica e pede outra hipótese", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.tools = {call: async (tool, args) => {
    calls.push(tool);
    return {tool, ok: true, data: {path: args.path, content: "def square(x):return x*x"}};
  }};
  ports.planner = {plan: async () => ({text: "Ler a fonte para avaliar a função.", backend: "test", toolCall: {
    id: "read-again", tool: "read_file", arguments: {path: "logic.py"}, reason: "Ler fonte", risk: "low", requiresApproval: false,
  }})};
  const report = await new AgentCore(ports).pursue({prompt: "Execute uma avaliação local da função square em logic.py.",
    objective: "operate", workspaceRoot: "/tmp/assistente"});
  assert.equal(report.status, "blocked");
  assert.equal(calls.filter(call => call === "read_file").length, 1);
  assert.ok(report.events.some(event => event.kind === "engine.repetition.blocked"));
});

test("falha explícita do motor não vira operação concluída só por conter texto", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.tools = {call: async (tool) => ({tool, ok: true, data: {}})};
  ports.planner = {plan: async () => ({text: "A avaliação não foi concluída: divisão por zero.",
    backend: "execution-engine", toolCall: null, retryable: false, stopReason: "execution_engine_evaluation_failed"})};
  const report = await new AgentCore(ports).pursue({prompt: "Calcule 8/0", objective: "conversation"});
  assert.equal(report.status, "blocked");
  assert.match(report.finalText ?? "", /divisão por zero/);
  assert.ok(report.events.some(event => event.kind === "execution.engine.blocked"));
});

test("último evento de aprovação mantém o caminho e o diff de um reparo validado", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls, false, false);
  ports.workspace.inspect = async () => ({workspace: "/tmp/assistente", files: ["logic.py"],
    manifests: [], testFiles: [], entrypoints: ["logic.py"]});
  const diff = {path: "logic.py", old_text: "return x + 30", new_text: "return x + 32", reason: "Corrigir a falha observada"};
  ports.tools = {call: async (tool, args) => {
    calls.push(tool);
    if (tool === "read_file") return {ok: true, tool, data: {path: args.path, content: "def answer(x): return x + 30"}};
    if (tool === "propose_repair") return {ok: true, tool, data: {status: "ready", ...diff}};
    throw new Error("O reparo não deve executar antes da aprovação: " + tool);
  }};
  ports.planner = {plan: async () => ({text: "Preparar um reparo exato", toolCall: {
    id: "propose-fix", tool: "propose_repair", arguments: diff, reason: diff.reason,
    requiresApproval: false, risk: "low"}})};
  const report = await new AgentCore(ports).pursue({prompt: "Corrija logic.py e execute os testes sem internet.",
    objective: "build", workspaceRoot: "/tmp/assistente"});
  const approval = [...report.events].reverse().find(event => event.kind === "approval.required");
  assert.equal(report.status, "blocked");
  assert.equal(approval?.detail, "logic.py");
  assert.equal(approval?.payload?.tool, "apply_repair");
  assert.deepEqual(approval?.payload?.repairDiff, {path: diff.path, oldText: diff.old_text, newText: diff.new_text});
  assert.ok(approval?.payload?.actionId);
  assert.ok(!calls.includes("apply_repair"));
});


test("análise não aceita recusa genérica como conclusão mesmo após uma leitura", () => {
  const report = evaluateTaskAcceptance({
    objective: "analyze",
    prompt: "Analise o projeto.",
    finalText: "Não consegui iniciar uma investigação útil. Indique um arquivo.",
    successfulTools: ["read_file"],
    readPaths: ["src/main.ts"],
  });
  assert.equal(report.passed, false);
  assert.ok(report.pending.some((check) => check.id === "analysis.answer"));
});

test("frase de acompanhamento não satisfaz síntese de debug", () => {
  const report = evaluateTaskAcceptance({objective: "debug",
    finalText: "Estou acompanhando. Pode me contar um pouco mais?",
    successfulTools: ["read_file"], correctionRequested: false});
  assert.equal(report.passed, false);
  assert.ok(report.pending.some((check) => check.id === "delivery.summary"));
});

test("debug de página lê o frontend e executa check antes de concluir", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.workspace.inspect = async () => ({workspace: "/tmp/assistente",
    files: ["index.html", "app.py", "tests/test_app.py"],
    directories: [], manifests: [], entrypoints: ["app.py"], testFiles: ["tests/test_app.py"]});
  ports.tools = {call: async (tool, arguments_) => {
    calls.push(tool + ":" + String(arguments_.path ?? ""));
    if (tool === "read_file") return {ok: true, tool,
      data: {path: arguments_.path, content: "fetch('/api/tasks').then(response => response.json())"}};
    if (tool === "project_checks") return {ok: true, tool,
      data: {check: "unittest", passed: true, executed: true,
        summary: "2 testes passaram", evidence: ["Ran 2 tests: OK"]}};
    throw new Error("Ferramenta inesperada: " + tool);
  }};
  ports.planner = {plan: async ({messages}) => {
    assert.ok(messages.some((message) => message.role === "tool"
      && message.content.includes('"tool":"project_checks"')));
    const state = messages.find((message) => {
      try { return JSON.parse(message.content).tool === "task_state"; }
      catch { return false; }
    });
    assert.ok(state);
    const facts = JSON.parse(state.content).data;
    assert.equal(facts.objective, "debug");
    assert.equal(facts.fileChangeConfirmed, false);
    assert.ok(facts.observations.some((item: {tool: string; outcome: string}) =>
      item.tool === "project_checks" && item.outcome === "succeeded"));
    return {text: "Em index.html, a chamada relativa a /api/tasks espera JSON. app.py declara essa rota na API local. Isso indica que a página aberta em outro servidor pode receber HTML e causar o erro. Inicie app.py e abra a página pela porta da API.",
      backend: "test", toolCall: null};
  }};
  const report = await new AgentCore(ports).pursue({
    prompt: "Por que a página web criada retornou erro de JSON? Arquivo ativo: index.html",
    objective: "auto", workspaceRoot: "/tmp/assistente",
  });
  assert.equal(report.status, "completed", report.error);
  assert.equal(calls.find((call) => call.startsWith("read_file:")), "read_file:index.html");
  assert.ok(calls.findIndex((call) => call.startsWith("project_checks:")) >
    calls.findIndex((call) => call === "read_file:index.html"));
  assert.equal(report.verification?.executed, true);
  assert.ok(report.events.some(event => event.kind === "verification.passed" && event.payload?.executed === true));
  assert.ok(!report.finalText?.includes("Estou acompanhando"));
});

test("debug prioriza buscar evidência local e não pesquisa antes de ler a rota", async () => {
  for (const offline of [false, true]) {
    const calls: string[] = [];
    const ports = fakePorts(calls, true);
    ports.tools = {call: async (tool, args) => {
      calls.push(tool);
      if (tool === "read_file") return {ok: true, tool, data: {path: args.path, content: "private-source"}};
      if (tool === "project_checks") return {ok: true, tool, data: {
        executed: true, passed: false, check: "npm-test", stderr: "SyntaxError", stdout: ""}};
      if (tool === "diagnose_project") {
        assert.equal(args.stderr, "SyntaxError");
        return {ok: true, tool, data: {summary: "Falha de sintaxe observada"}};
      }
      if (tool === "search_files") {
        assert.match(String(args.query), /fetch|json/i);
        assert.doesNotMatch(String(args.query), /private-source|segredo/);
        return {ok: true, tool, data: {matches: [
          {path: "index.html", line: 14, text: "fetch('/api/tasks').then(r => r.json())"},
          {path: "app.py", line: 22, text: "@app.get('/api/tasks')"},
        ]}};
      }
      if (tool === "research_web") {
        assert.equal(args.provider, "brave");
        assert.doesNotMatch(String(args.query), /private-source|segredo/);
        return {ok: false, tool, error: "Brave indisponível"};
      }
      return {ok: false, tool, error: "Não disponível"};
    }};
    ports.planner = {plan: async () => {
      assert.ok(calls.includes("diagnose_project"));
      return {text: "Sem reparo validável", toolCall: null,
        stopReason: "implementation_proposal_unavailable", retryable: false};
    }};
    const result = await new AgentCore(ports).pursue({
      prompt: "A página falhou: Unexpected token '<', not valid JSON. URL privada: segredo."
        + (offline ? " Trabalhe sem internet." : ""),
      objective: "debug", workspaceRoot: "/tmp/assistente",
    });
    assert.ok(calls.includes("search_files"), "a falha JSON deve iniciar investigação no código local");
    assert.equal(calls.filter(call => call === "research_web").length, 0,
      "pesquisa externa não substitui a leitura do código que dispara o erro");
    assert.equal(result.status, "blocked");
  }
});

test("fallback de erro JSON continua da busca local até leitura, reparo e verificação", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls, true);
  ports.workspace.inspect = async () => ({workspace: "/tmp/assistente",
    files: ["index.html", "app.py", "tests/test_app.py"], directories: [],
    manifests: [], entrypoints: ["app.py"], testFiles: ["tests/test_app.py"]});
  let page = "fetch('/api/tasks').then(response => response.json())";
  ports.tools = {call: async (tool, args) => {
    calls.push(tool);
    if (tool === "read_file") return {ok: true, tool,
      data: {path: args.path, content: String(args.path) === "index.html" ? page : "@app.get('/api/tasks')\ndef tasks(): return jsonify([])"}};
    if (tool === "project_checks") return {ok: true, tool, data: {check: String(args.check ?? "auto"),
      passed: true, executed: true, summary: "Fluxo e testes aprovados", evidence: ["2 testes passaram"]}};
    if (tool === "search_files") return {ok: true, tool, data: {matches: [
      {path: "index.html", line: 8, text: "fetch('/api/tasks').then(response => response.json())"},
      {path: "app.py", line: 12, text: "@app.get('/api/tasks')"},
    ]}};
    if (tool === "edit_file") {
      assert.equal(args.path, "index.html");
      assert.ok(page.includes(String(args.old_text)));
      page = page.replace(String(args.old_text), String(args.new_text));
      return {ok: true, tool, data: {updated: true, path: "index.html", diff: {changed: 1}}};
    }
    throw new Error("Ferramenta inesperada: " + tool);
  }};
  let plannerTurns = 0;
  ports.planner = {plan: async ({messages}) => {
    plannerTurns += 1;
    if (plannerTurns === 1) return {text: "O check inicial passou, mas ainda não há reparo validável.",
      backend: "debug-evidence-fallback", stopReason: "implementation_proposal_unavailable", retryable: false,
      toolCall: null};
    if (plannerTurns === 2) {
      assert.ok(messages.some((message) => message.role === "tool" && message.content.includes('"tool":"search_files"')));
      return {text: "Vou conferir a resposta da API no backend.", backend: "test", toolCall: {
        id: "inspect-api-route", tool: "read_file", arguments: {path: "app.py", start_line: 1, end_line: 60},
        reason: "Comparar a rota que entrega JSON com o fetch da interface.", requiresApproval: false, risk: "low"}};
    }
    if (plannerTurns === 3) {
      assert.ok(messages.some((message) => message.role === "tool" && message.content.includes("jsonify")));
      return {text: "A rota existe; o cliente precisa conferir status e Content-Type antes de analisar JSON.", backend: "test",
        toolCall: {id: "fix-json-http-error", tool: "edit_file", arguments: {
          path: "index.html", old_text: "response => response.json()",
          new_text: "response => { if (!response.ok) throw new Error('HTTP ' + response.status); return response.json(); }"},
        reason: "Relatar status HTTP antes de tentar converter uma resposta de erro em JSON.", requiresApproval: true, risk: "medium"}};
    }
    if (plannerTurns === 4) return {text: "Agora vou executar a suíte após a alteração.", backend: "test",
      toolCall: {id: "verify-repair", tool: "project_checks", arguments: {check: "unittest"},
        reason: "Verificar a correção após a escrita.", requiresApproval: true, risk: "medium"}};
    return {text: "Corrigi index.html e os testes passaram após a alteração.", backend: "test", toolCall: null};
  }};
  const report = await new AgentCore(ports).pursue({
    prompt: "Ao adicionar uma tarefa a página mostra Unexpected token '<', <!DOCTYPE ... is not valid JSON. Investigue, corrija e rode os testes.",
    objective: "debug", workspaceRoot: "/tmp/assistente",
  });
  assert.equal(report.status, "completed", JSON.stringify({error: report.error, calls}));
  assert.ok(calls.indexOf("search_files") < calls.indexOf("research_web") || !calls.includes("research_web"));
  assert.ok(calls.includes("edit_file"));
  assert.equal(report.verification?.passed, true);
  assert.ok(report.artifacts.some((artifact) => artifact.path === "index.html"));
  assert.ok(plannerTurns >= 5);
});

test("relato de erro sem imperativo permite reparar e exige nova verificação", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.workspace.inspect = async () => ({workspace: "/tmp/assistente",
    files: ["index.html", "app.py", "tests/test_app.py"], directories: [],
    manifests: [], entrypoints: ["app.py"], testFiles: ["tests/test_app.py"]});
  let source = "fetch('/api/tasks').then(response => response.json())";
  ports.tools = {call: async (tool, arguments_) => {
    calls.push(tool);
    if (tool === "read_file") return {ok: true, tool,
      data: {path: arguments_.path, content: String(arguments_.path) === "index.html" ? source : "def app(): pass"}};
    if (tool === "project_checks") return {ok: true, tool,
      data: {check: "unittest", passed: true, executed: true, summary: "2 testes passaram"}};
    if (tool === "edit_file") {
      assert.equal(arguments_.path, "index.html");
      assert.ok(source.includes(String(arguments_.old_text)));
      source = source.replace(String(arguments_.old_text), String(arguments_.new_text));
      return {ok: true, tool, data: {updated: true, path: "index.html", diff: {changed: 1}}};
    }
    throw new Error("Ferramenta inesperada: " + tool);
  }};
  let plannerTurns = 0;
  ports.planner = {plan: async ({messages}) => {
    plannerTurns += 1;
    assert.ok(messages.some((message) => message.role === "tool" && message.content.includes('"tool":"project_checks"')));
    if (plannerTurns === 1) return {text: "O parser JSON recebe uma resposta não JSON; proponho tratar a resposta HTTP antes do parse.",
      backend: "test", toolCall: {id: "fix-json-response", tool: "edit_file",
        arguments: {path: "index.html", old_text: "response => response.json()",
          new_text: "response => response.ok ? response.json() : Promise.reject(new Error('HTTP ' + response.status))"},
        reason: "Expor a falha HTTP antes da conversão JSON.", requiresApproval: true, risk: "medium"}};
    return {text: "O tratamento da resposta foi alterado em index.html e os checks executados após a mudança passaram.",
      backend: "test", toolCall: null};
  }};
  const report = await new AgentCore(ports).pursue({
    prompt: `A interface está mostrando a mensagem "Unexpected token '<', \"<!DOCTYPE \"... is not valid JSON"`,
    objective: "auto", workspaceRoot: "/tmp/assistente",
  });
  assert.equal(report.status, "completed", JSON.stringify({error: report.error, calls}));
  assert.ok(!calls.includes("approval"), "relato concreto no projeto autoriza reparo local: " + JSON.stringify(calls));
  assert.ok(calls.includes("edit_file"));
  assert.ok(calls.includes("verify"));
  assert.equal(report.verification?.passed, true);
  assert.ok(plannerTurns >= 2);
});

test("fallback raciocina erro JSON pela página, rota backend e README quando o modelo não sintetiza", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls, true);
  ports.workspace.inspect = async () => ({workspace: "/tmp/assistente",
    files: ["index.html", "app.py", "README.md", "tests/test_app.py"], directories: [],
    manifests: [], entrypoints: ["app.py"], testFiles: ["tests/test_app.py"]});
  const files: Record<string, string> = Object.fromEntries(
    ["index.html", "app.py", "README.md"].map(path => [path,
      readFileSync(new URL("./fixtures/relative-api-wrapper/" + path, import.meta.url), "utf8")]),
  );
  ports.tools = {call: async (tool, args) => {
    calls.push(tool);
    if (tool === "read_file") return {ok: true, tool,
      data: {path: args.path, content: files[String(args.path)] ?? ""}};
    if (tool === "project_checks") return {ok: true, tool,
      data: {check: "unittest", passed: true, executed: true, summary: "testes passaram"}};
    if (tool === "search_files") return {ok: true, tool,
      data: {query: args.query, matches: [{path: "index.html", line: 1, text: "response.json()"}]}};
    throw new Error("Ferramenta inesperada: " + tool);
  }};
  ports.planner = {plan: async () => ({text: "", backend: "debug-evidence-fallback",
    stopReason: "implementation_proposal_unavailable", retryable: false, toolCall: null})};
  const report = await new AgentCore(ports).pursue({
    prompt: `A interface está mostrando a mensagem "Unexpected token '<', \\"<!DOCTYPE \\"... is not valid JSON"`,
    objective: "auto", workspaceRoot: "/tmp/assistente",
  });
  assert.equal(report.status, "completed", report.error);
  assert.match(report.finalText ?? "", /index\.html/);
  assert.match(report.finalText ?? "", /README\.md/);
  assert.match(report.finalText ?? "", /http:\/\/127\.0\.0\.1:8765/);
  assert.ok(calls.includes("read_file"));
  assert.ok(calls.includes("project_checks"));
});

test("pedido de criar testes lê código, escreve teste e verifica após a escrita", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.workspace.inspect = async () => ({workspace: "/tmp/assistente", files: ["app.py"],
    directories: [], manifests: [], entrypoints: ["app.py"], testFiles: []});
  let created = false;
  ports.tools = {call: async (tool, arguments_) => {
    calls.push(tool + ":" + String(arguments_.path ?? ""));
    if (tool === "read_file") return {ok: true, tool,
      data: {path: arguments_.path, content: String(arguments_.path) === "app.py"
        ? "def soma(a, b): return a + b" : "from app import soma\nassert soma(2, 3) == 5\n"}};
    if (tool === "create_file") {
      created = true;
      return {ok: true, tool, data: {created: true, path: arguments_.path}};
    }
    throw new Error("Ferramenta inesperada: " + tool);
  }};
  let plannerTurns = 0;
  ports.planner = {plan: async ({messages}) => {
    plannerTurns += 1;
    assert.ok(messages.some((message) => message.role === "tool"
      && message.content.includes('"tool":"read_file"')));
    if (plannerTurns === 1) return {text: "A função soma precisa de um teste de resultado.", backend: "test",
      toolCall: {id: "new-test", tool: "create_file", arguments: {path: "tests/test_app.py",
        content: "from app import soma\nassert soma(2, 3) == 5\n"},
      reason: "Cobrir a soma com um caso observável.", requiresApproval: true, risk: "medium"}};
    return {text: "Criei tests/test_app.py e a verificação executada após a escrita passou.",
      backend: "test", toolCall: null};
  }};
  const report = await new AgentCore(ports).pursue({prompt: "Crie testes para app.py",
    objective: "auto", workspaceRoot: "/tmp/assistente"});
  assert.equal(report.status, "completed", JSON.stringify({error: report.error, calls}));
  assert.equal(created, true);
  assert.ok(!calls.some((call) => call === "approval"), "criar os testes foi pedido: " + JSON.stringify(calls));
  assert.ok(calls.includes("verify"));
  assert.equal(report.verification?.passed, true);
});

test("build vazio que encerra em inspeção recebe instrução explícita de propor arquivos", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  const plannerMessages: string[] = [];
  const allPlannerContext: string[] = [];
  ports.tools = {call: async (tool, arguments_) => {
    calls.push(tool);
    if (tool === "create_file") return {ok: true, tool, data: {created: true, path: arguments_.path}};
    throw new Error("Ferramenta inesperada: " + tool);
  }};
  ports.planner = {plan: async ({messages}) => {
    allPlannerContext.push(messages.map((message) => message.content).join("\n"));
    plannerMessages.push(messages.at(-1)?.content ?? "");
    if (plannerMessages.length === 1) {
      return {text: "Concluí a etapa `inspect_project`.", backend: "test", toolCall: null};
    }
    if (plannerMessages.length > 2) {
      return {text: "Criei app.py e a verificação do projeto foi aprovada.", backend: "test", toolCall: null};
    }
    assert.match(plannerMessages[1] ?? "", /workspace está vazio/i);
    assert.match(plannerMessages[1] ?? "", /create_file ou apply_batch/i);
    return {text: "Proposta pronta para aprovação.", backend: "test", toolCall: {
      id: "create-eval-app", tool: "create_file",
      arguments: {path: "app.py", content: "print('avaliação')\n"},
      reason: "Criar o primeiro arquivo solicitado.", requiresApproval: true, risk: "medium",
    }};
  }};

  const result = await new AgentCore(ports).pursue({
    prompt: "Crie um projeto pequeno com app.py e um teste unittest para a função soma.",
    objective: "build",
    workspaceRoot: "/tmp/assistente",
  });

  assert.equal(result.status, "completed", result.error);
  assert.equal(calls.includes("approval"), true);
  assert.equal(calls.includes("create_file"), true);
  assert.equal(calls.includes("verify"), true);
  assert.equal(plannerMessages.length, 3);
  assert.match(allPlannerContext[0] ?? "", /Camadas de personalidade do AgentCore/);
  assert.match(allPlannerContext[0] ?? "", /defaults locais seguros/);
});

test("build diagnostica um teste falho, corrige o arquivo com aprovação e verifica novamente", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  let source = "def soma(a, b):\n    return a - b\n";
  let verificationCount = 0;
  const approvals: string[] = [];
  ports.approval = {request: async ({path}) => {
    approvals.push(path);
    return true;
  }};
  ports.workspace.verify = async () => {
    verificationCount += 1;
    return verificationCount === 1
      ? {passed: false, executed: true, check: "unittest", summary: "test_soma falhou: esperado 5, recebido -1",
          evidence: ["test_soma esperava 5; recebeu -1"], stdout: "FAIL test_soma", stderr: ""}
      : {passed: true, executed: true, check: "unittest", summary: "1 teste aprovado", evidence: ["Ran 1 test: OK"]};
  };
  ports.tools = {call: async (tool, arguments_) => {
    calls.push(tool);
    const path = String(arguments_.path ?? "app.py");
    if (tool === "create_file") {
      source = String(arguments_.content);
      return {ok: true, tool, data: {created: true, path}};
    }
    if (tool === "read_file") return {ok: true, tool, data: {path, content: source}};
    if (tool === "diagnose_project") {
      return {ok: true, tool, data: {diagnosis: "A função subtrai; o teste exige soma."}};
    }
    if (tool === "edit_file") {
      const oldText = String(arguments_.old_text);
      assert.ok(source.includes(oldText), "a edição precisa corresponder ao arquivo lido");
      source = source.replace(oldText, String(arguments_.new_text));
      return {ok: true, tool, data: {updated: true, path, diff: {changed: 1}}};
    }
    throw new Error("Ferramenta inesperada: " + tool);
  }};
  let plannerTurn = 0;
  ports.planner = {plan: async ({messages}) => {
    plannerTurn += 1;
    if (plannerTurn === 1) return {text: "Vou criar a primeira versão e o teste.", backend: "test", toolCall: {
      id: "create-app", tool: "create_file",
      arguments: {path: "app.py", content: source}, reason: "Criar a primeira versão solicitada.",
      requiresApproval: true, risk: "medium",
    }};
    if (plannerTurn === 2) return {text: "A primeira versão está pronta; vou verificar.", backend: "test", toolCall: null};
    if (plannerTurn === 3) {
      assert.ok(messages.some((message) => message.role === "tool"
        && message.content.includes('"tool":"diagnose_project"')
        && message.content.includes("A função subtrai")));
      return {text: "Vou ler o arquivo para apoiar a correção no conteúdo atual.", backend: "test", toolCall: {
        id: "read-app", tool: "read_file", arguments: {path: "app.py"},
        reason: "Ler o trecho que o diagnóstico identificou.", requiresApproval: false, risk: "low",
      }};
    }
    if (plannerTurn === 4) {
      assert.ok(messages.some((message) => message.role === "tool"
        && message.content.includes('"tool":"read_file"')
        && message.content.includes("return a - b")));
      return {text: "O diagnóstico confirmou a operação incorreta; vou corrigi-la.", backend: "test", toolCall: {
        id: "fix-app", tool: "edit_file",
        arguments: {path: "app.py", old_text: "return a - b", new_text: "return a + b"},
        reason: "Corrigir a função conforme a falha observada.", requiresApproval: true, risk: "medium",
      }};
    }
    if (plannerTurn === 5) {
      return {text: "Corrigi a função, executei o teste novamente e ele passou.", backend: "test", toolCall: null};
    }
    throw new Error("O planejador recebeu ciclos inesperados: " + plannerTurn);
  }};

  const result = await new AgentCore(ports).pursue({
    prompt: "Crie app.py com a função soma e um teste unittest; corrija qualquer falha e rode o teste novamente.",
    objective: "build",
    workspaceRoot: "/tmp/assistente",
  });

  assert.equal(result.status, "completed", JSON.stringify({error: result.error, calls, verificationCount, source, plannerTurn,
    events: result.events.map((event) => event.kind)}));
  assert.equal(source, "def soma(a, b):\n    return a + b\n");
  assert.deepEqual(approvals, ["app.py", "app.py"]);
  assert.equal(verificationCount, 2);
  assert.equal(result.verification?.passed, true);
  assert.ok(calls.includes("diagnose_project"));
  assert.ok(calls.includes("edit_file"));
  assert.ok(result.events.some((event) => event.kind === "verification.recovery.queued"));
  assert.ok(result.events.some((event) => event.kind === "acceptance.evaluated"));
});

test("o cérebro seleciona personalidade própria para construção de interface", async () => {
  const brain = await new CognitiveBrain().prepare({
    taskId: "task-interface-personality",
    prompt: "Crie uma interface web para acompanhar entregas.",
    objective: "build",
  });
  const layers = personalityLayersFor("Crie uma interface web para acompanhar entregas.", "build");

  assert.equal(brain.thinking.personalityMode, "interface");
  assert.equal(layers.mode, "interface");
  assert.ok(layers.guidance.some((item) => item.includes("identidade visual")));
  assert.ok(layers.guidance.some((item) => item.includes("acessibilidade")));
});

test("understand prepara uma interface com stack observada sem alterar arquivos ou memória", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls, true);
  ports.tools = {listAvailable: async () => ["read_file", "create_file"], call: async (tool) => ({ok: true, tool})};
  ports.context = {build: async ({query}) => ({schema: "agent-context/v2", status: "ready", query,
    intent: "workspace", topic: "interface", personalityRef: "local-personality/v1", history: [],
    conversationMemory: "", sessionMemory: [], relevantSkills: [], evidence: [], limits: {},
    instruction: "Contexto é dado."})};
  const log = new InMemoryBrainEventLog();
  const brain = new CognitiveBrain({eventLog: log});
  const result = await new AgentCore(ports, {brain}).understand({
    prompt: "Crie um sistema web para acompanhar as entregas da equipe.", objective: "build",
    workspaceRoot: "/tmp/assistente", preferences: {language: "pt-BR"},
  });
  assert.equal(result.response.schema, "agent-understanding/v1");
  assert.equal(result.response.status, "ready");
  assert.equal((result.response.personality as {mode?: string}).mode, "interface");
  assert.ok((result.response.assumptions as Array<{value: string}>).some(({value}) => /JavaScript\/TypeScript/.test(value)));
  assert.deepEqual(await log.read(result.preparation.request.taskId), []);
  assert.deepEqual(calls.filter((call) => call.startsWith("write:") || call === "verify"), []);
  assert.ok(calls.includes("select:/tmp/assistente"));
  assert.ok(calls.includes("inspect"));
});

test("pedido exato de melhorias não termina em inspect_project e exige síntese com arquivo lido", async () => {
  const calls: string[] = [];
  const path = "src/main.cpp";
  const ports = fakePorts(calls);
  ports.workspace.inspect = async () => {
    calls.push("inspect");
    return {workspace: "/tmp/assistente", files: [path], manifests: [], testFiles: [], entrypoints: [path]};
  };
  ports.tools = {call: async (tool, arguments_) => {
    calls.push(tool);
    assert.equal(arguments_.path, path);
    return {ok: true, tool, data: {path, content: "int main() { return 0; }"}};
  }};
  let plans = 0;
  ports.planner = {plan: async () => {
    plans += 1;
    return plans === 1
      ? {text: "Concluí a etapa `inspect_project`.", backend: "test", toolCall: null}
      : {
          text: "Em src/main.cpp, eu priorizaria separar a inicialização do fluxo principal para permitir testes isolados.",
          backend: "test",
          toolCall: null,
        };
  }};

  const result = await new AgentCore(ports).pursue({
    prompt: "avalie pontos de melhora no projeto atual",
    objective: "auto",
    workspaceRoot: "/tmp/assistente",
  });

  assert.equal(classifyObjective("avalie pontos de melhora no projeto atual"), "analyze");
  assert.equal(classifyObjective("Liste os arquivos do workspace"), "analyze");
  assert.equal(classifyObjective("Leia o arquivo src/main.cpp"), "analyze");
  assert.equal(result.status, "completed", result.error);
  assert.equal(plans, 2);
  assert.deepEqual(calls, ["select:/tmp/assistente", "inspect", "read_file"]);
  assert.equal(result.events.some((event) => event.kind === "planner.recovery.requested"), true);
  assert.match(result.finalText ?? "", /src\/main\.cpp/);
});

test("resposta quality-gate em conversa dispara reavaliação antes de concluir", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  const attempts: string[] = [];
  ports.planner = {
    plan: async ({messages}) => {
      attempts.push(messages.at(-1)?.content ?? "");
      return attempts.length === 1
        ? {text: "Indique um arquivo ou peça uma inspeção do projeto.", backend: "quality-gate", toolCall: null}
        : {text: "A resposta é 42.", backend: "local-conversation", toolCall: null};
    },
  };

  const result = await new AgentCore(ports).pursue({prompt: "Quanto é 6 vezes 7?", objective: "conversation"});

  assert.equal(attempts.length, 2);
  assert.match(attempts[1] ?? "", /Reavalie a pergunta e responda diretamente/);
  assert.equal(result.status, "completed");
  assert.equal(result.finalText, "A resposta é 42.");
  assert.equal(result.events.some((event) => event.kind === "task.completed"), true);
  assert.equal(calls.some((call) => call.startsWith("write:") || call === "inspect"), false);
});

test("geração indisponível consulta ferramentas uma vez e preserva o pedido", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.tools = {call: async (tool) => {
    assert.equal(tool, "list_tools"); calls.push(tool);
    return {ok: true, tool, data: {tools: []}};
  }};
  let plannerCalls = 0;
  const prompt = "Neste workspace, crie app.py com soma(a, b) retornando a+b e tests/test_app.py com unittest para soma(2, 3) == 5.";
  ports.planner = {plan: async ({messages}) => {
    plannerCalls += 1;
    assert.equal(messages.filter((message) => message.role === "user").at(-1)?.content,
      prompt);
    return {text: "O checkpoint local não conseguiu gerar uma proposta estruturada. Nenhum arquivo foi alterado.",
      backend: "agent-loop", stopReason: "implementation_proposal_unavailable", toolCall: null};
  }};
  const result = await new AgentCore(ports, {enforceRequirementsGate: false}).pursue({
    prompt, objective: "build", workspaceRoot: "/tmp/assistente",
  });
  assert.equal(plannerCalls, 2, JSON.stringify({error: result.error, events: result.events.map((event) => event.kind)}));
  assert.equal(result.status, "blocked");
  assert.match(result.finalText ?? "", /checkpoint local não conseguiu gerar/);
  assert.equal(result.events.some((event) => event.kind === "planner.recovery.requested"), true);
  assert.equal(calls.filter(call => call === "list_tools").length, 1);
});

test("geração esgotada não repete a mesma abordagem nem declara sucesso", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.tools = {call: async tool => ({ok: true, tool, data: {tools: []}})};
  let attempts = 0;
  ports.planner = {plan: async () => {
    attempts++;
    return {text: "Geração esgotada; nenhum arquivo alterado.", toolCall: null,
      stopReason: "implementation_proposal_unavailable", retryable: false};
  }};
  const report = await new AgentCore(ports, {enforceRequirementsGate: false}).pursue({
    prompt: "Crie uma aplicação no workspace.", objective: "build", workspaceRoot: "/tmp/assistente",
  });
  assert.equal(attempts, 1);
  assert.equal(report.status, "blocked");
  assert.equal(report.events.some(event => event.kind === "planner.recovery.requested"), false);
  assert.equal(calls.some(call => call.startsWith("write:")), false);
});

test("núcleo observa ferramentas disponíveis antes de planejar e compartilha o catálogo filtrado", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.tools = {
    listAvailable: async () => {
      calls.push("listAvailable");
      return ["read_file", "create_file"];
    },
    call: async (tool) => ({ok: false, tool, error: "não executado"}),
  };
  let catalogWasProvided = false;
  ports.planner = {plan: async ({messages}) => {
    const catalog = messages.find((message) => {
      try { return JSON.parse(message.content).tool === "list_tools"; }
      catch { return false; }
    });
    catalogWasProvided = Boolean(catalog
      && catalog.content.includes("create_file")
      && !catalog.content.includes("terminal_run"));
    return {text: "Ainda não consigo propor a implementação.", toolCall: null,
      stopReason: "implementation_proposal_unavailable"};
  }};
  const result = await new AgentCore(ports).pursue({
    prompt: "Crie um protótipo simples para registrar entregas.",
    objective: "build",
    workspaceRoot: "/tmp/assistente",
  });
  assert.equal(calls[0], "listAvailable");
  assert.equal(catalogWasProvided, true);
  assert.deepEqual(result.events.find((event) => event.kind === "runtime.capabilities.observed")?.payload?.allowedTools,
    ["read_file", "create_file"]);
});

test("núcleo bloqueia ferramenta que a política aceita, mas o runtime não oferece", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.tools = {
    listAvailable: async () => ["read_file"],
    call: async (tool) => { calls.push("tool:" + tool); return {ok: true, tool, data: {}}; },
  };
  let plans = 0;
  ports.planner = {plan: async () => {
    plans += 1;
    return plans === 1
      ? {text: "Vou iniciar os testes.", toolCall: {id: "unsupported-tool", tool: "terminal_run",
          arguments: {operation: "git_status"}, reason: "Verificar o projeto.", requiresApproval: false, risk: "medium"}}
      : {text: "O runtime não oferece a ferramenta necessária.", toolCall: null};
  }};
  const result = await new AgentCore(ports).pursue({
    prompt: "Verifique o projeto.", objective: "build", workspaceRoot: "/tmp/assistente",
  });
  assert.equal(calls.some((call) => call.startsWith("tool:")), false);
  assert.equal(result.events.some((event) => event.kind === "runtime.capability.unavailable"), true);
});

test("complemento de interface chega ao planejador como build com o produto original", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls, true);
  const goal = "Crie uma interface base para um ambiente de criação de jogos de computador";
  let planned = false;
  ports.tools = {call: async (tool) => ({tool, ok: true, data: {}})};
  ports.planner = {plan: async ({messages, objective}) => {
    planned = true;
    assert.equal(objective, "build");
    const request = messages.filter(message => message.role === "user").at(-1)?.content ?? "";
    assert.ok(request.includes(goal));
    assert.ok(request.includes("Rust, Typescript e Html"));
    return {text: "Geração indisponível.", toolCall: null, stopReason: "implementation_proposal_unavailable"};
  }};
  const result = await new AgentCore(ports).pursue({
    prompt: "Rust, Typescript e Html parecem boas opções", objective: "auto",
    workspaceRoot: "/tmp/assistente",
    history: [{role: "user", content: goal},
      {role: "user", content: "Agora, isso precisa de uma interface para que eu possa interagir"}],
  });
  assert.ok(planned, JSON.stringify(result));
  assert.ok(calls.includes("inspect"));
  assert.equal(result.status, "blocked");
});

test("resposta sobre capacidade condiciona geração de código ao checkpoint ativo", async () => {
  const result = await new AgentCore(fakePorts([])).pursue({
    prompt: "Você consegue criar código?", objective: "auto",
  });
  assert.equal(result.status, "completed");
  assert.match(result.finalText ?? "", /depende da capacidade do checkpoint ativo/);
  assert.match(result.finalText ?? "", /Toda escrita proposta passa por aprovação/);
});

test("prioriza arquivo pedido e procura substituto na mesma pasta se ele não existir", () => {
  const inspection: ProjectInspection = {
    workspace: "/tmp/assistente",
    files: ["README.md", "agent-core/src/planner.ts", "agent-core/src/agent.ts", "config/huggingface_catalog.json"],
    manifests: [],
    testFiles: [],
    entrypoints: [],
  };
  const exact = explicitWorkspaceFilePaths("Explique agent-core/src/planner.ts.");
  assert.deepEqual(exact, ["agent-core/src/planner.ts"]);
  assert.deepEqual(projectAnalysisPaths(inspection, 4, exact, "Explique o roteador."), ["agent-core/src/planner.ts"]);

  const missing = explicitWorkspaceFilePaths("Leia config/agent-planner.yaml para explicar o catálogo de datasets.");
  const selected = projectAnalysisPaths(inspection, 4, missing, "Explique o catálogo de datasets.");
  assert.ok(selected.includes("config/huggingface_catalog.json"));
  assert.equal(selected.includes("README.md"), false);

  const truncated: ProjectInspection = {
    ...inspection,
    truncated: true,
    files: inspection.files.filter((path) => path !== "agent-core/src/planner.ts"),
  };
  const truncatedExact = projectAnalysisPaths(
    truncated, 4, ["agent-core/src/planner.ts"], "Explique o roteador de ferramentas.",
  );
  assert.equal(truncatedExact[0], "agent-core/src/planner.ts");
  assert.ok(truncatedExact.includes("agent-core/src/agent.ts"));
  const truncatedMissing = projectAnalysisPaths(
    truncated, 4, ["config/agent-planner.yaml"], "Explique o catálogo de datasets.",
  );
  assert.equal(truncatedMissing[0], "config/agent-planner.yaml");
  assert.ok(truncatedMissing.includes("config/huggingface_catalog.json"));
});

test("reconhece arquivos de documento e mídia explicitamente citados", () => {
  assert.deepEqual(explicitWorkspaceFilePaths("Resuma docs/guia.pdf e verifique assets/capa.png."), [
    "docs/guia.pdf", "assets/capa.png",
  ]);
  assert.deepEqual(explicitWorkspaceFilePaths("Confira recordings/demo.mp4 e notes/session.eml."), [
    "recordings/demo.mp4", "notes/session.eml",
  ]);
});

test("seleciona leitura de documento ou inspeção de mídia pelo formato e pela pergunta", async () => {
  const scenarios = [
    {path: "docs/guia.pdf", prompt: "Resuma o conteúdo de docs/guia.pdf.", tool: "extract_document_text"},
    {path: "assets/capa.png", prompt: "Qual o formato e tamanho de assets/capa.png?", tool: "inspect_media"},
    {
      path: "python/agent_planner.py",
      prompt: "Inspecione os símbolos e imports de python/agent_planner.py.",
      tool: "inspect_code",
      truncated: true,
    },
  ] as const;

  for (const scenario of scenarios) {
    const calls: string[] = [];
    const ports = fakePorts(calls);
    ports.workspace.inspect = async () => ({
      workspace: "/tmp/assistente",
      files: "truncated" in scenario && scenario.truncated ? [] : [scenario.path],
      manifests: [],
      testFiles: [],
      entrypoints: [],
      truncated: "truncated" in scenario && scenario.truncated,
    });
    ports.tools = {
      call: async (tool, arguments_) => {
        calls.push(tool);
        assert.equal(arguments_.path, scenario.path);
        return {ok: true, tool, data: {path: scenario.path, content: "evidência local", kind: "image", mime: "image/png", bytes: 256}};
      },
    };
    let plans = 0;
    ports.planner = {
      plan: async () => {
        plans += 1;
        if (scenario.tool === "inspect_code" && plans === 1) return {
          text: "Os símbolos foram mapeados; agora vou ler o mesmo arquivo para explicar o conteúdo.",
          backend: "test",
          toolCall: {
            id: "read-after-inspect-code",
            tool: "read_file",
            arguments: {path: scenario.path},
            reason: "Complementar o inventário de símbolos com leitura do conteúdo solicitado.",
            requiresApproval: false,
            risk: "low",
          },
        };
        return {
          text: `Analisei ${scenario.path} com evidência local observada.`,
          backend: "test",
          toolCall: null,
        };
      },
    };

    const result = await new AgentCore(ports).pursue({
      prompt: scenario.prompt, objective: "analyze", workspaceRoot: "/tmp/assistente",
    });

    assert.equal(result.status, "completed", result.error);
    assert.deepEqual(
      calls.filter((tool) => !tool.startsWith("select:")),
      scenario.tool === "inspect_code" ? ["inspect_code", "read_file"] : [scenario.tool],
    );
  }
});

test("permite inspect_project como ferramenta somente leitura durante análise", async () => {
  const calls: string[] = [];
  const path = "README.md";
  const ports = fakePorts(calls);
  ports.workspace.inspect = async () => ({workspace: "/tmp/assistente", files: [path], manifests: [], testFiles: [], entrypoints: []});
  ports.tools = {
    call: async (tool) => {
      calls.push(tool);
      return {ok: true, tool, data: {files: [path], directories: ["src"]}};
    },
  };
  let plans = 0;
  ports.planner = {
    plan: async () => {
      plans += 1;
      return plans === 1
        ? {text: "A leitura inicial está feita; vou conferir o inventário.", backend: "test", toolCall: {
          id: "inspect-project", tool: "inspect_project", arguments: {}, reason: "Conferir estrutura do workspace.",
          requiresApproval: false, risk: "low",
        }}
        : {text: "README.md está no workspace e há também a pasta src/.", backend: "test", toolCall: null};
    },
  };

  const result = await new AgentCore(ports).pursue({
    prompt: "Analise o projeto.", objective: "analyze", workspaceRoot: "/tmp/assistente",
  });

  assert.equal(result.status, "completed", result.error);
  assert.deepEqual(calls, ["select:/tmp/assistente", "read_file", "inspect_project"]);
  assert.equal(result.events.some((event) => event.kind === "analysis.policy.blocked"), false);
});

test("permite ler a próxima janela contígua do mesmo arquivo sem abrir ciclo de repetição", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls, true);
  const path = "agent-core/src/planner.ts";
  ports.workspace.inspect = async () => ({
    workspace: "/tmp/assistente",
    files: [path],
    manifests: [],
    testFiles: [],
    entrypoints: [],
  });
  ports.tools = {
    call: async (tool, arguments_) => {
      const startLine = typeof arguments_.start_line === "number" ? arguments_.start_line : 1;
      const endLine = typeof arguments_.end_line === "number" ? arguments_.end_line : 120;
      calls.push("read:" + startLine + "-" + endLine);
      return {
        ok: true,
        tool,
        data: {
          path,
          start_line: startLine,
          end_line: endLine,
          total_lines: 215,
          content: "trecho de código verificado",
        },
      };
    },
  };
  let planCount = 0;
  ports.planner = {
    plan: async () => {
      planCount += 1;
      if (planCount === 1) {
        return {
          text: "O arquivo continua após a primeira janela; vou ler as linhas finais.",
          backend: "test",
          toolCall: {
            id: "read-window-continuation",
            tool: "read_file",
            arguments: {path, start_line: 121, end_line: 215, max_bytes: 8192},
            reason: "Ler a próxima janela de linhas solicitada.",
            risk: "low",
            requiresApproval: false,
          },
        };
      }
      return {
        text: "Em agent-core/src/planner.ts, LocalPlannerHttp envia a proposta a /generate e valida os argumentos; não faz o ranqueamento semântico.",
        backend: "test",
        toolCall: null,
      };
    },
  };

  const result = await new AgentCore(ports).pursue({
    prompt: "Explique agent-core/src/planner.ts.",
    objective: "analyze",
    workspaceRoot: "/tmp/assistente",
  });

  assert.deepEqual(
    calls,
    ["select:/tmp/assistente", "read:1-120", "read:121-215"],
    JSON.stringify({planCount, error: result.error,
      events: result.events.map((event) => ({kind: event.kind, detail: event.detail, payload: event.payload}))}),
  );
  assert.equal(planCount, 2);
  assert.equal(result.status, "completed");
  assert.match(result.finalText ?? "", /não faz o ranqueamento semântico/);
  assert.doesNotMatch(result.finalText ?? "", /Inventário observado no workspace/);
  const toolStateEvents = result.events.filter((event) => event.kind === "brain.state"
    && ["executing", "verifying"].includes(String(event.payload?.state)));
  assert.ok(toolStateEvents.length > 0);
  assert.ok(toolStateEvents.every((event) => event.payload?.tool === "read_file"),
    JSON.stringify(toolStateEvents.map((event) => event.payload)));
});

test("usa inspect_code dentro da análise e registra evidência de símbolos e imports", async () => {
  const calls: string[] = [];
  const path = "src/app.ts";
  const ports = fakePorts(calls, true);
  ports.workspace.inspect = async () => ({
    workspace: "/tmp/assistente",
    files: [path],
    directories: ["src"],
    manifests: [],
    testFiles: [],
    entrypoints: [path],
  });
  ports.tools = {
    call: async (tool, arguments_) => {
      calls.push(tool);
      if (tool === "read_file") {
        return {ok: true, tool, data: {path, content: "import {helper} from './helper';\\nexport function run() {}"}};
      }
      if (tool === "inspect_code") {
        return {ok: true, tool, data: {files_scanned: 1, symbols: [{name: "run", kind: "function", path, line: 1}], imports: [{path, line: 1, text: "import helper"}]}};
      }
      return {ok: false, tool, error: "ferramenta inesperada", data: {}};
    },
  };
  let plans = 0;
  ports.planner = {
    plan: async () => {
      plans += 1;
      return plans === 1 ? {
        text: "Vou conferir símbolos e imports do arquivo.",
        backend: "test",
        toolCall: {
          id: "inspect-code",
          tool: "inspect_code",
          arguments: {path},
          reason: "Inspecionar símbolos e imports solicitados.",
          requiresApproval: false,
          risk: "low",
        },
      } : {
        text: "Em src/app.ts, encontrei a função `run` e o import de `helper`.",
        backend: "test",
        toolCall: null,
      };
    },
  };

  const result = await new AgentCore(ports).pursue({
    prompt: "Explique o fluxo implementado em src/app.ts.",
    objective: "analyze",
    workspaceRoot: "/tmp/assistente",
  });

  assert.equal(result.status, "completed", result.error);
  assert.deepEqual(calls, ["select:/tmp/assistente", "read_file", "inspect_code"]);
  assert.match(result.finalText ?? "", /função `run`/);
  assert.equal(result.events.some((event) => event.kind === "brain.state"
    && event.payload?.tool === "inspect_code"), true);
});

test("create_web_page registra o arquivo como alteração e exige aprovação", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls, true);
  ports.tools = {
    call: async (tool, arguments_) => {
      calls.push(tool);
      if (tool === "create_web_page") {
        return {ok: true, tool, data: {created: true, path: "preview/index.html", preview_html: "<html>Protótipo</html>"}};
      }
      if (tool === "read_file") {
        return {ok: true, tool, data: {path: "preview/index.html", content: "<html>Protótipo</html>"}};
      }
      return {ok: false, tool, error: "ferramenta inesperada", data: {}};
    },
  };
  let plans = 0;
  ports.planner = {
    plan: async () => {
      plans += 1;
      return plans === 1 ? {
        text: "Criar a página no workspace.",
        backend: "test",
        toolCall: {
          id: "create-page",
          tool: "create_web_page",
          arguments: {prompt: "Página de apresentação", path: "preview/index.html", title: "Projeto"},
          reason: "Criar a página solicitada.",
          requiresApproval: true,
          risk: "medium",
        },
      } : {text: "Estou acompanhando. Pode me contar um pouco mais?", backend: "test", toolCall: null};
    },
  };

  const result = await new AgentCore(ports).pursue({
    prompt: "Crie uma página HTML de apresentação para o projeto.",
    objective: "build",
    workspaceRoot: "/tmp/assistente",
  });

  assert.equal(result.status, "completed", result.error);
  assert.ok(calls.includes("approval"));
  assert.ok(calls.includes("create_web_page"));
  assert.ok(result.artifacts.some((artifact) => artifact.path === "preview/index.html"));
  assert.equal(result.verification?.passed, true);
  assert.match(result.finalText ?? "", /alteração solicitada foi aplicada no workspace e a verificação passou/);
  assert.doesNotMatch(result.finalText ?? "", /Estou acompanhando|Pode me contar/);
});

test("create_workspace pausa antes de executar até receber aprovação", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls, true, false);
  let runtimeCalls = 0;
  ports.tools = {call: async (tool) => {
    if (tool === "read_file") return {ok: true, tool, data: {content: "contexto"}};
    runtimeCalls += 1;
    return {ok: false, tool, error: "escrita não deveria chegar ao runtime"};
  }};
  ports.planner = {
    plan: async () => ({
      text: "Preparando o workspace do novo projeto.",
      backend: "test",
      toolCall: {
        id: "create-workspace",
        tool: "create_workspace",
        arguments: {path: "/tmp/novo-projeto"},
        reason: "Criar e selecionar o novo projeto solicitado.",
        requiresApproval: true,
        risk: "high",
      },
    }),
  };

  const result = await new AgentCore(ports).pursue({
    prompt: "Crie um novo workspace em /tmp/novo-projeto.",
    objective: "build",
    workspaceRoot: "/tmp/assistente",
  });

  assert.equal(result.status, "blocked");
  assert.equal(runtimeCalls, 0);
  assert.ok(calls.includes("approval"));
  assert.equal(result.events.some((event) => event.kind === "approval.required"
    && event.payload?.tool === "create_workspace"), true);
});

test("inspeciona e pesquisa sem declarar construção concluída sem implementador", async () => {
  const calls: string[] = [];
  const result = await new AgentCore(fakePorts(calls)).pursue({
    prompt: "Criar um assistente pessoal local.",
    objective: "build",
    workspaceRoot: "/tmp/assistente",
  });

  assert.deepEqual(calls, ["select:/tmp/assistente", "inspect", "research", "learn", "verify"]);
  assert.equal(result.status, "blocked");
  assert.equal(result.artifacts.length, 0);
  assert.match(result.error ?? "", /planejador local conectado/);
  assert.equal(result.events.find((event) => event.kind === "plan.created")?.phase, "plan");
  assert.equal(result.events.find((event) => event.kind === "implementation.unavailable")?.status, "blocked");
  assert.equal(result.events.some((event) => event.kind === "task.completed"), false);
});

test("mantém pesquisa, mas não cria plano quando o projeto já possui base", async () => {
  const calls: string[] = [];
  const result = await new AgentCore(fakePorts(calls, true)).pursue({
    prompt: "Verificar a estrutura do assistente.",
    objective: "build",
    workspaceRoot: "/tmp/assistente",
  });

  assert.deepEqual(calls, ["select:/tmp/assistente", "inspect", "research", "learn", "verify"]);
  assert.equal(result.artifacts.length, 0);
  assert.equal(result.status, "blocked");
  assert.match(result.error ?? "", /planejador local conectado/);
});

test("não chama um workspace desconhecido de vazio só porque falta manifesto", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  const originalInspect = ports.workspace.inspect;
  ports.workspace.inspect = async () => ({
    ...(await originalInspect()),
    workspace: "/tmp/assistente",
    files: ["README.md"],
    manifests: [],
    testFiles: [],
    entrypoints: [],
  });

  const result = await new AgentCore(ports).pursue({prompt: "Auditar o projeto", objective: "build", workspaceRoot: "/tmp/assistente"});
  assert.deepEqual(calls, ["select:/tmp/assistente", "inspect", "research", "learn", "verify"]);
  assert.equal(result.artifacts.length, 0);
  assert.equal(result.status, "blocked");
  assert.match(result.error ?? "", /planejador local conectado/);
});

test("pesquisa explicitamente quando o objetivo é research", async () => {
  const calls: string[] = [];
  const result = await new AgentCore(fakePorts(calls)).pursue({prompt: "Pesquisar arquiteturas locais", objective: "research"});
  assert.deepEqual(calls, ["research", "learn"]);
  assert.equal(result.status, "blocked");
  assert.match(result.error ?? "", /planejador local não está conectado/);
  assert.equal(result.events.find((event) => event.kind === "research.completed")?.phase, "learn");
});

test("retoma usando a inspeção e as evidências já obtidas sem repetir pesquisa", async () => {
  const firstCalls: string[] = [];
  const input = {prompt: "Criar um assistente pessoal local.", objective: "build" as const, workspaceRoot: "/tmp/assistente"};
  const first = await new AgentCore(fakePorts(firstCalls)).pursue(input);
  assert.equal(first.status, "blocked");
  assert.ok(first.inspection);

  const resumedCalls: string[] = [];
  const resumed = await new AgentCore(fakePorts(resumedCalls)).pursue(input, {
    inspection: first.inspection,
    evidence: first.evidence,
  });
  assert.deepEqual(resumedCalls, ["select:/tmp/assistente", "verify"]);
  assert.equal(resumed.status, "blocked");
  assert.match(resumed.error ?? "", /planejador local conectado/);
  assert.equal(resumed.events.some((event) => event.kind === "research.started"), false);
  assert.equal(resumed.events.find((event) => event.kind === "research.reused")?.phase, "learn");
});

test("não cria arquivo de plano sem capacidade de implementação conectada", async () => {
  const calls: string[] = [];
  const result = await new AgentCore(fakePorts(calls, false, false)).pursue({
    prompt: "Criar um assistente pessoal local.",
    objective: "build",
    workspaceRoot: "/tmp/assistente",
  });

  assert.deepEqual(calls, ["select:/tmp/assistente", "inspect", "research", "learn", "verify"]);
  assert.equal(result.status, "blocked");
  assert.equal(result.artifacts.length, 0);
  assert.equal(calls.some((call) => call.startsWith("write:") || call === "approval"), false);
  assert.equal(result.events.find((event) => event.kind === "implementation.unavailable")?.status, "blocked");
});

test("compacta inspeções grandes antes de registrá-las na memória", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls, true);
  const originalInspect = ports.workspace.inspect;
  ports.workspace.inspect = async () => ({
    ...(await originalInspect()),
    files: Array.from({length: 5000}, (_, index) => `src/generated-${index}.ts`),
  });
  const memoryValues: string[] = [];
  ports.memory = {
    remember: async (input) => {
      memoryValues.push(input.value);
      return {...input, id: "mem-test", createdAt: "now", updatedAt: "now"};
    },
    search: async () => [],
  };
  const result = await new AgentCore(ports).pursue({prompt: "Verificar o projeto", objective: "build", workspaceRoot: "/tmp/assistente"});
  assert.equal(result.status, "blocked");
  assert.match(result.error ?? "", /planejador local conectado/);
  const inspection = memoryValues.find((value) => value.includes('"counts"')) ?? "";
  assert.ok(inspection.length < 20000);
  assert.match(inspection, /"files":5000/);
});

test("o gate cognitivo pausa um pedido ambíguo antes de tocar no workspace", async () => {
  const calls: string[] = [];
  const result = await new AgentCore(fakePorts(calls), {
    brain: new CognitiveBrain(),
    enforceRequirementsGate: true,
  }).pursue({
    prompt: "Quero criar um aplicativo.",
    objective: "build",
    workspaceRoot: "/tmp/assistente",
  });

  assert.equal(result.status, "blocked");
  assert.equal(calls.length, 0);
  assert.ok(result.requirements);
  assert.equal(result.requirements?.requiresClarification, true);
  assert.equal(result.events.find((event) => event.kind === "requirements.clarification.required")?.phase, "complete");
});

test("conversa consulta fonte por decisão do planejador e usa o resultado no próximo ciclo", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.tools = {listAvailable: async () => ["search_web", "open_page", "create_file"], call: async (tool) => {
    calls.push(tool);
    return {ok: true, tool, data: {url: "https://example.org/docs", content: "A versão atual exige Node 24."}};
  }};
  let turns = 0;
  ports.planner = {plan: async ({messages}) => {
    turns++;
    if (turns === 1) {
      const catalog = messages.find(m => m.role === "tool" && JSON.parse(m.content).tool === "list_tools")!.content;
      assert.ok(catalog.includes("search_web"));
      assert.ok(!catalog.includes("create_file"));
      return {text: "Preciso conferir o requisito atual.", toolCall: {id: "lookup", tool: "open_page",
        arguments: {url: "https://example.org/docs"}, reason: "Verificar a versão exigida", risk: "low", requiresApproval: false}};
    }
    assert.ok(messages.some(m => m.role === "tool" && m.content.includes("Node 24")));
    return {text: "A documentação consultada exige Node 24: https://example.org/docs", toolCall: null};
  }};
  const result = await new AgentCore(ports).pursue({prompt: "Qual versão ela exige?", objective: "conversation"});
  assert.equal(result.status, "completed", result.error);
  assert.deepEqual(calls, ["open_page"]);
  assert.equal(turns, 2);
  assert.ok(result.finalText?.includes("Node 24"));
});

test("conversa simples não seleciona nem inspeciona workspace e não usa ferramentas", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.tools = {call: async () => { throw new Error("Consulta desnecessária"); }};
  ports.planner = {plan: async () => ({text: "Olá!", toolCall: null})};
  const result = await new AgentCore(ports).pursue({prompt: "Oi", objective: "conversation", workspaceRoot: "/tmp/assistente"});
  assert.equal(result.status, "completed", result.error);
  assert.deepEqual(calls, []);
});

test("conversa seleciona workspace somente quando uma leitura é necessária", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.tools = {call: async (tool) => { calls.push(tool); return {ok: true, tool, data: {path: "package.json", content: '{"engines":{"node":">=24"}}'}}; }};
  let turns = 0;
  ports.planner = {plan: async () => ++turns === 1
    ? {text: "Vou conferir a configuração.", toolCall: {id: "read", tool: "read_file", arguments: {path: "package.json"}, reason: "Verificar compatibilidade", risk: "low", requiresApproval: false}}
    : {text: "O package.json exige Node 24 ou posterior.", toolCall: null}};
  const result = await new AgentCore(ports).pursue({prompt: "Qual versão serve?", objective: "conversation", workspaceRoot: "/tmp/assistente"});
  assert.equal(result.status, "completed", result.error);
  assert.deepEqual(calls, ["select:/tmp/assistente", "read_file"]);
});

test("conversa bloqueia escritas, processos e leituras sem workspace mesmo sem cérebro opcional", async () => {
  for (const tool of ["create_file", "terminal_run", "read_file"] as const) {
    const calls: string[] = [];
    const ports = fakePorts(calls);
    ports.tools = {call: async () => { calls.push("unsafe"); throw new Error("Não deveria executar"); }};
    ports.planner = {plan: async () => ({text: "Vou agir.", toolCall: {id: "unsafe", tool,
      arguments: {path: "README.md", content: "x", operation: "git_status"}, reason: "Ação indevida", risk: "low", requiresApproval: false}})};
    const result = await new AgentCore(ports).pursue({prompt: "Olá", objective: "conversation"});
    assert.equal(result.status, "blocked");
    assert.deepEqual(calls, []);
  }
});

test("conversa não aceita quality-gate como resposta e respeita limite de consultas", async () => {
  for (const qualityGate of [true, false]) {
    const calls: string[] = [];
    const ports = fakePorts(calls);
    ports.tools = {call: async (tool) => { calls.push(tool); return {ok: true, tool, data: {}}; }};
    ports.planner = {plan: async () => qualityGate
      ? {text: "Não consegui responder.", backend: "quality-gate", toolCall: null}
      : {text: "Ainda estou buscando.", toolCall: {id: "search-" + calls.length, tool: "search_web", arguments: {query: "consulta " + calls.length}, reason: "Obter evidência", risk: "low", requiresApproval: false}}};
    const result = await new AgentCore(ports, {maxPlannerSteps: 2}).pursue({prompt: "Qual versão serve?", objective: "conversation"});
    assert.equal(result.status, "blocked");
    assert.ok(calls.length <= 2);
  }
});


test("conversa com cérebro operacional refaz consulta após falha e mantém objetivo", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.tools = {listAvailable: async () => ["search_web"], call: async (tool, args) => {
    calls.push(String(args.query));
    return calls.length === 1 ? {ok: false, tool, error: "Nenhum resultado para este termo."}
      : {ok: true, tool, data: {results: [{url: "https://example.org/docs", snippet: "Requer Node 24."}]}};
  }};
  let turns = 0;
  ports.planner = {plan: async ({prompt, messages, cognition}) => {
    assert.equal(prompt, "Qual versão serve?");
    assert.deepEqual(cognition?.availableTools, ["search_web"]);
    turns++;
    if (turns === 2) assert.ok(messages.some(m => m.role === "tool" && m.content.includes("Nenhum resultado")));
    return turns < 3 ? {text: "Vou esclarecer o requisito.", toolCall: {id: "lookup-" + turns, tool: "search_web",
      arguments: {query: turns === 1 ? "requisito" : "documentação requisito Node"}, reason: "Conferir requisito",
      risk: "low", requiresApproval: false}}
      : {text: "A fonte consultada indica Node 24: https://example.org/docs", toolCall: null};
  }};
  const result = await new AgentCore(ports, {brain: new OperationalBrain()}).pursue({prompt: "Qual versão serve?", objective: "conversation"});
  assert.equal(result.status, "completed", result.error);
  assert.deepEqual(calls, ["requisito", "documentação requisito Node"]);
});

test("bloqueio cognitivo explícito do Python não vira conclusão nem dispara novas gerações", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.tools = {call: async () => {throw new Error("Nenhuma consulta autorizada pelo planejador");}};
  let turns = 0;
  ports.planner = {plan: async () => {
    turns++;
    return {text: "Preciso do nome da biblioteca.", backend: "cognitive-dialogue", toolCall: null,
      stopReason: "cognitive_evidence_missing", retryable: false};
  }};
  const result = await new AgentCore(ports).pursue({prompt: "Qual versão serve?", objective: "conversation"});
  assert.equal(result.status, "blocked");
  assert.equal(turns, 1);
  assert.ok(result.error?.includes("nome da biblioteca"));
  assert.deepEqual(calls, []);
});

test("conversa não repete consulta idêntica que não resolveu a lacuna", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  ports.tools = {call: async (tool) => {calls.push(tool); return {ok: false, tool, error: "Fonte indisponível"};}};
  ports.planner = {plan: async () => ({text: "Vou consultar.", toolCall: {id: crypto.randomUUID(), tool: "open_page",
    arguments: {url: "https://example.org/docs"}, reason: "Requisito atual", risk: "low", requiresApproval: false}})};
  const result = await new AgentCore(ports).pursue({prompt: "Qual versão serve?", objective: "conversation"});
  assert.equal(result.status, "blocked");
  assert.deepEqual(calls, ["open_page"]);
});

test("prévia de conversa também espera necessidade antes de inspecionar o workspace", async () => {
  const calls: string[] = [];
  const ports = fakePorts(calls);
  await new AgentCore(ports, {brain: new OperationalBrain()}).understand({
    prompt: "Olá", objective: "conversation", workspaceRoot: "/tmp/assistente"});
  assert.deepEqual(calls, []);
});
