import assert from "node:assert/strict";
import test from "node:test";
import {packageLookupFromPrompt, routeTask, taskRouteResponse} from "../src/task-router.ts";
import {parseTaskRouteRequest} from "../src/server-contract.ts";

test("pergunta de versão de pacote consulta o registro oficial antes da web", () => {
  const route = routeTask({prompt: "Qual a versão mais nova do React?"});
  assert.equal(route.objective, "research");
  assert.equal(route.brain, "research");
  assert.equal(route.research.mode, "required");
  assert.deepEqual(route.research.sources, ["package-registry", "web"]);
  assert.deepEqual(route.research.package, {name: "react", ecosystems: ["npm"]});
  assert.deepEqual(packageLookupFromPrompt("Qual a última versão do pacote requests no PyPI?"), {name: "requests", ecosystems: ["pypi"]});
  assert.deepEqual(packageLookupFromPrompt("Novidades do tokio"), {name: "tokio", ecosystems: ["crates"]});
  assert.deepEqual(packageLookupFromPrompt("Qual a versão mais recente da biblioteca leftpad-x?"), {name: "leftpad-x", ecosystems: ["npm", "pypi"]});
  // Linguagens e runtimes não são pacotes de registro.
  assert.equal(packageLookupFromPrompt("Qual a versão mais nova do Python?"), undefined);
  assert.deepEqual(routeTask({prompt: "Qual a versão mais nova do Python?"}).research.sources, ["web"]);
});

test("cada tipo de pedido recebe cérebro, personalidade e ferramentas próprios", () => {
  const cases: Array<[string, string, string]> = [
    ["Crie um poema sobre o mar sem rimas", "creative", "none"],
    ["Quanto é 15% de 300?", "computation", "none"],
    ["Crie uma API REST em Python para cadastro de clientes", "engineering", "none"],
    ["Crie uma interface web para acompanhar entregas", "interface", "none"],
    ["O que é Rust?", "conversation", "recommended"],
    ["Qual a cotação do dólar hoje?", "research", "required"],
  ];
  for (const [prompt, brain, research] of cases) {
    const route = routeTask({prompt});
    assert.equal(route.brain, brain, prompt);
    assert.equal(route.research.mode, research, prompt);
  }
  const creative = routeTask({prompt: "Faça um brainstorm de nomes para uma cafeteria sem trocadilhos"});
  assert.equal(creative.creative?.profile, "divergent");
  assert.equal(creative.creative?.variations, true);
  assert.deepEqual(creative.creative?.constraints.forbidden, ["trocadilhos"]);
  assert.equal(creative.tools.writesPossible, false);
  const build = routeTask({prompt: "Crie uma API REST em Python para cadastro de clientes"});
  assert.equal(build.tools.writesPossible, true);
  assert.equal(build.tools.requiresWorkspace, true);
  assert.match(build.nextStep, /Selecionar um workspace/);
  assert.match(routeTask({prompt: "Crie uma API REST em Python para cadastro de clientes", workspaceSelected: true}).nextStep,
    /Inspecionar o workspace/);
  assert.deepEqual(routeTask({prompt: "O que é Rust?"}).research.sources, ["wikipedia", "web"]);
  assert.deepEqual(routeTask({prompt: "Quais bibliotecas para validação em TypeScript você recomenda?"}).research.sources, ["github", "web"]);
  assert.equal(routeTask({prompt: "Explique Rust sem internet"}).research.mode, "none");
  assert.equal(routeTask({prompt: "What is the latest version of a library like Express?"}).research.language, "en");
  assert.equal(routeTask({prompt: "Qual a versão mais nova do React?"}).research.language, "pt");
});

test("contrato HTTP do roteador é estrito e nunca autoriza execução", () => {
  const body = taskRouteResponse(routeTask(parseTaskRouteRequest({schema: "task-route-request/v1",
    prompt: "Qual a versão mais nova do React?", workspace_selected: false})));
  assert.equal(body.schema, "task-route/v1");
  assert.equal(body.execution_allowed, false);
  assert.equal((body.research as {package: {name: string}}).package.name, "react");
  assert.equal((body.tools as {approval_required_for_writes: boolean}).approval_required_for_writes, true);
  assert.throws(() => parseTaskRouteRequest({prompt: ""}), /entre 1 e 24000/);
  assert.throws(() => parseTaskRouteRequest({prompt: "oi", schema: "outro/v1"}), /Contrato de roteamento/);
  assert.throws(() => parseTaskRouteRequest({prompt: "oi", execute: true}), /Campos inválidos/);
  assert.throws(() => parseTaskRouteRequest({prompt: "oi", workspace_selected: "sim"}), /booleano/);
});
