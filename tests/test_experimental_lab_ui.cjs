'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const js = fs.readFileSync(path.join(__dirname, '../runtime/static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../runtime/static/index.html'), 'utf8');
const source = js.slice(js.indexOf('// Laboratório experimental:'), js.indexOf('// Fim do laboratório experimental.'));

class Element {
  constructor() { this.textContent = ''; this.value = ''; this.children = []; this.dataset = {}; this.hidden = false; this.open = false; }
  set innerHTML(_) { throw new Error('Saídas do modelo devem ser texto.'); }
  append(...elements) { this.children.push(...elements); }
  replaceChildren(...elements) { this.children = elements; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() { this.focused = true; }
}
const state = () => ({schema:'experimental-model-status/v1', enabled:true, qualified:false,
  loaded:false, checkpoint:'model/v4.safetensors', context_tokens:512,
  core_states:{'decision-format':'failed', mathematics:'not_evaluated'},
  limits:['Tarefas sintéticas numéricas', 'Sem execução de ferramentas']});
const reply = () => ({schema:'experimental-cognitive-response/v1', ok:true,
  text:'Valor: 42.', decision:{decision:'answer', text:'Valor: 42.', evidence_ids:['obs-1'], tool_call:null},
  raw_output:'{"decision":"answer","text":"Valor: 42."}', experimental:true, qualified:false, tool_executed:false});
function response(data, status=200) { return {ok:status < 400, status, json:async () => data}; }
function harness(responses=[]) {
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const calls = [];
  const context = {
    document:{getElementById:get},
    node:(_tag, _className, text) => { const element = new Element(); if (text != null) element.textContent = text; return element; },
    fetch:async (url, options) => { calls.push({url, options}); const next = responses.shift(); if (next instanceof Error) throw next; return typeof next === 'function' ? await next() : next; },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  get('cognitive-lab-question').value = 'Qual é o limite de Pipa?';
  get('cognitive-lab-source').value = 'Pipa: limite = 42.';
  return {context, get, calls};
}
const event = () => ({preventDefault() {}});

test('laboratório mantém navegação e oferece controles acessíveis separados do chat', () => {
  assert.match(html, /id="cognitive-lab-open"[^>]*aria-haspopup="dialog"/);
  assert.match(html, /<dialog id="cognitive-lab-dialog"[^>]*aria-labelledby="cognitive-lab-title"/);
  assert.match(html, /id="cognitive-lab-error"[^>]*role="alert"/);
  assert.match(html, /<summary>Saída bruta do modelo<\/summary>/);
  assert.match(html, /id="cognitive-lab-source" maxlength="200"/);
  assert.doesNotMatch(source, /toolRequest\(|requestWithActivity\(|agentRun|conversation|innerHTML/);
});

test('estado real habilita apenas análise experimental e traduz as provas por núcleo', async () => {
  const {context, get, calls} = harness([response(state())]);
  await context.openExperimentalLab();
  assert.equal(get('cognitive-lab-dialog').open, true);
  assert.equal(get('cognitive-lab-submit').disabled, false);
  assert.match(get('cognitive-lab-status').textContent, /competência reprovada/);
  assert.match(get('cognitive-lab-model').textContent, /512 tokens.*primeira análise/);
  assert.deepEqual(get('cognitive-lab-cores').children.map(row => row.children[1].textContent), ['Reprovado','Não avaliado']);
  assert.equal(calls[0].url, '/api/v1/models/experimental');
  context.closeExperimentalLab();
  assert.equal(get('cognitive-lab-dialog').open, false);
});

test('request envia uma observação textual e nenhuma ferramenta disponível', async () => {
  const {context, calls, get} = harness([response(reply())]);
  context.renderExperimentalLabStatus(state());
  await context.submitExperimentalLab(event());
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/models/experimental/decide');
  const body = JSON.parse(calls[0].options.body);
  assert.equal(body.schema, 'experimental-cognitive-request/v1');
  assert.deepEqual(body.cognition, {schema:'agent-cognition/v1', available_tools:[]});
  assert.deepEqual(body.messages[0], {role:'user', content:'Qual é o limite de Pipa?'});
  assert.deepEqual(JSON.parse(body.messages[1].content), {tool:'read_file', ok:true, data:{path:'dados.json', content:'Pipa: limite = 42.'}, error:''});
  assert.equal(get('cognitive-lab-result').hidden, false);
  assert.match(get('cognitive-lab-result-state').textContent, /competência reprovada/);
});

test('HTML hostil e proposta de ferramenta são exibidos como texto e nunca executados', async () => {
  const data = reply();
  data.text = '<img src=x onerror="fetch(\'/api/tool-call\')">';
  data.raw_output = '<script>window.exploit=true</script>';
  data.decision.tool_call = {tool:'terminal_run', arguments:{command:'touch /tmp/nao-executar'}};
  const {context, get, calls} = harness([response(data)]);
  context.renderExperimentalLabStatus(state());
  await context.submitExperimentalLab(event());
  assert.equal(get('cognitive-lab-result-text').textContent, data.text);
  assert.equal(get('cognitive-lab-raw').textContent, data.raw_output);
  assert.match(get('cognitive-lab-decision').textContent, /terminal_run/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.method, 'POST');
});

test('422 preserva saída bruta e expõe a razão do bloqueio', async () => {
  const data = {...reply(), ok:false, error:'JSON gerado inválido.', raw_output:'{"decision":'};
  const {context, get} = harness([response(data, 422)]);
  context.renderExperimentalLabStatus(state());
  await context.submitExperimentalLab(event());
  assert.equal(get('cognitive-lab-error').textContent, 'JSON gerado inválido.');
  assert.equal(get('cognitive-lab-raw').textContent, '{"decision":');
  assert.match(get('cognitive-lab-result-state').textContent, /bloqueada/);
  assert.equal(get('cognitive-lab-submit').disabled, false);
});

test('falha de rede libera controles sem inventar resultado', async () => {
  const {context, get} = harness([new Error('Serviço indisponível')]);
  context.renderExperimentalLabStatus(state());
  await context.submitExperimentalLab(event());
  assert.match(get('cognitive-lab-error').textContent, /Serviço indisponível/);
  assert.equal(get('cognitive-lab-result').hidden, true);
  assert.equal(get('cognitive-lab-submit').disabled, false);
});

test('estado incompatível ou desativado impede inferência', async () => {
  const {context, get, calls} = harness([response({...state(), qualified:true})]);
  await context.refreshExperimentalLab();
  await context.submitExperimentalLab(event());
  assert.match(get('cognitive-lab-error').textContent, /não corresponde/);
  assert.equal(get('cognitive-lab-submit').disabled, true);
  assert.equal(calls.length, 1);
  context.renderExperimentalLabStatus({...state(), enabled:false});
  context.setExperimentalLabBusy(false);
  await context.submitExperimentalLab(event());
  assert.equal(get('cognitive-lab-submit').disabled, true);
  assert.equal(calls.length, 1);
});

test('duplo envio não inicia duas gerações simultâneas', async () => {
  let release;
  const {context, calls} = harness([() => new Promise(resolve => { release = resolve; })]);
  context.renderExperimentalLabStatus(state());
  const first = context.submitExperimentalLab(event());
  await context.submitExperimentalLab(event());
  assert.equal(calls.length, 1);
  release(response(reply()));
  await first;
});

test('limites do formulário também são verificados em chamadas diretas', async () => {
  const {context, get, calls} = harness();
  context.renderExperimentalLabStatus(state());
  get('cognitive-lab-source').value = 'x'.repeat(201);
  await context.submitExperimentalLab(event());
  assert.match(get('cognitive-lab-error').textContent, /200 caracteres/);
  assert.equal(calls.length, 0);
  context.useExperimentalLabExample('compare');
  assert.equal(get('cognitive-lab-question').value, 'A versão 41 atende ao mínimo exigido por Pipa?');
  assert.equal(get('cognitive-lab-source').value, 'Pipa: versão mínima = 42.');
});
