const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'extension.js'), 'utf8');
const requests = [];
const reports = [
  {
    ok: true,
    awaitingApproval: false,
    report: {
      taskId: 'task-review-complete',
      status: 'completed',
      finalText: 'Encontrei melhorias priorizadas com evidências nos arquivos lidos.',
      artifacts: [],
      evidence: [],
      events: [
        {seq: 1, kind: 'analysis.file.read', status: 'completed', title: 'Arquivo central lido', detail: 'src/main.cpp'},
        {seq: 2, kind: 'acceptance.evaluated', status: 'completed', title: 'Critérios de aceite satisfeitos', detail: 'análise fundamentada'},
      ],
    },
  },
  {
    ok: true,
    awaitingApproval: false,
    report: {
      taskId: 'task-review-invalid',
      status: 'completed',
      finalText: 'Estou acompanhando. Pode me contar um pouco mais?',
      error: 'delivery.summary: síntese ausente. debug.change: nenhuma escrita. debug.verification: sem check.',
      artifacts: [],
      evidence: [],
      events: [
        {seq: 1, kind: 'engineering.context.observed', status: 'completed', title: 'Arquivo lido', detail: 'index.html'},
        {seq: 2, kind: 'implementation.unavailable', status: 'blocked', title: 'Reparo indisponível', detail: 'O gerador não produziu um diff válido.'},
      ],
    },
  },
  {
    ok: true,
    awaitingApproval: false,
    report: {
      taskId: 'task-conversation-complete',
      status: 'completed',
      finalText: 'Olá! Como posso ajudar?',
      artifacts: [],
      evidence: [],
      events: [
        {seq: 1, kind: 'acceptance.evaluated', status: 'completed', title: 'Critérios de aceite satisfeitos'},
      ],
    },
  },
  {
    ok: true,
    awaitingApproval: false,
    report: {
      taskId: 'task-build-complete',
      status: 'completed',
      finalText: 'Implementei e verifiquei a CLI de tarefas.',
      artifacts: [{path: 'todo_cli.py'}, {path: 'tests/test_todo_cli.py'}],
      verification: {executed: true, passed: true, summary: '4 testes passaram.'},
      evidence: [],
      events: [
        {seq: 1, kind: 'acceptance.evaluated', status: 'completed', title: 'Critérios de aceite satisfeitos'},
      ],
    },
  },
];

const commands = [];
const vscode = {
  workspace: {workspaceFolders: [{uri: {fsPath: '/workspace/GameEngineStudio'}}]},
  window: {activeTextEditor: undefined},
  commands: {executeCommand: async (command) => commands.push(command)},
  Uri: {parse: (value) => ({toString: () => value})},
};
const context = {
  require: (name) => {
    assert.equal(name, 'vscode');
    return vscode;
  },
  module: {exports: {}},
  exports: {},
  console,
  AbortController,
  fetch: async (url, options) => {
    requests.push({url, payload: JSON.parse(options.body)});
    const body = reports.shift();
    return {ok: true, status: 200, json: async () => body};
  },
  setTimeout,
  clearTimeout,
};
vm.runInNewContext(source + '\nmodule.exports.Sidebar = LocalSidebarProvider;', context, {filename: 'extension.js'});

async function run() {
  const posted = [];
  const sidebar = new context.module.exports.Sidebar({
    subscriptions: [],
    workspaceState: {get: () => [], update: async () => undefined},
  });
  sidebar.view = {webview: {postMessage: (message) => posted.push(message)}};

  await sidebar.handlePrompt('avalie pontos de melhora no projeto atual');
  assert.equal(requests.length, 1, 'A tarefa operacional deve fazer uma única chamada ao orquestrador.');
  assert.equal(requests[0].url, 'http://127.0.0.1:3000/api/v1/agent/pursue');
  assert.equal(requests[0].payload.prompt, 'avalie pontos de melhora no projeto atual');
  assert.equal(requests[0].payload.objective, 'auto');
  assert.equal(requests[0].payload.workspaceRoot, '/workspace/GameEngineStudio');
  assert.deepEqual(requests[0].payload.history, []);
  const completed = posted.findLast((message) => message.type === 'assistant');
  assert.equal(completed.status, 'complete');
  assert.match(completed.text, /melhorias priorizadas/);

  await sidebar.handlePrompt('avalie novamente os pontos de melhora no projeto atual');
  assert.equal(requests.length, 2);
  assert.equal(requests[1].url, 'http://127.0.0.1:3000/api/v1/agent/pursue');
  const rejected = posted.findLast((message) => message.type === 'assistant');
  assert.equal(rejected.status, 'blocked', 'A UI deve recusar completed sem acceptance.evaluated aprovado.');
  assert.match(rejected.text, /Arquivos lidos: index\.html/);
  assert.match(rejected.text, /O gerador não produziu um diff válido/);
  assert.doesNotMatch(rejected.text, /Estou acompanhando|recusou a falsa conclusão|delivery\.summary/);

  await sidebar.handlePrompt('Olá');
  assert.equal(requests.length, 3);
  assert.equal(requests[2].url, 'http://127.0.0.1:3000/api/v1/agent/pursue');
  assert.equal(requests[2].payload.prompt, 'Olá');
  assert.equal(requests[2].payload.objective, 'auto');
  const greeting = posted.findLast((message) => message.type === 'assistant');
  assert.equal(greeting.status, 'complete');
  assert.match(greeting.text, /Como posso ajudar/);
  assert.equal(requests.some((request) => request.url.endsWith('/api/chat')), false,
    'Nenhuma mensagem do painel pode voltar ao loop legado de /api/chat.');

  await sidebar.handlePrompt('Crie uma lista de tarefas em Python com testes.');
  assert.equal(requests.length, 4);
  assert.equal(commands.includes('workbench.files.action.refreshFilesExplorer'), true,
    'O Explorer do VS Code deve ser atualizado quando o AgentCore entrega arquivos alterados.');
  console.log('sidebar-agent-core-routing=ok');
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
