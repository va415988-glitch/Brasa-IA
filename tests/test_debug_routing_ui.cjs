const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ChatCore = require('../runtime/static/chat-core.js');

const source = fs.readFileSync(path.join(__dirname, '..', 'runtime', 'static', 'app.js'), 'utf8');
const start = source.indexOf('function shouldAutoPursueAgent(');
const end = source.indexOf('\n}\n', start) + 2;
assert(start >= 0 && end > start);
const context = {ChatCore, localStorage: {getItem: () => '/workspace/ativo'}};
vm.createContext(context);
vm.runInContext(source.slice(start, end) + '\nglobalThis.shouldAutoPursueAgent = shouldAutoPursueAgent;', context);

const report = 'a pagina web criada retornou esse erro na interface: Unexpected token \'<\', "<!DOCTYPE "... is not valid JSON';
assert.equal(ChatCore.isProjectFailureReport(report), true);
assert.equal(context.shouldAutoPursueAgent(report), true);
assert.equal(ChatCore.isProjectFailureReport('Por que a página do meu projeto retornou erro 500?'), true);
assert.equal(ChatCore.isProjectFailureReport('Como investigar um erro de JSON em geral?'), false);
assert.equal(context.shouldAutoPursueAgent('Vamos conversar sobre design?'), false);

for (const text of [
  `A interface está mostrando a mensagem "Unexpected token '<', \"<!DOCTYPE \"... is not valid JSON"`,
  'A tela não carrega.', 'O botão não responde.', 'O formulário não salva.',
  'O painel travou.'
]) {
  assert.equal(ChatCore.isProjectFailureReport(text), true, text);
  assert.equal(context.shouldAutoPursueAgent(text), true, text);
}
for (const text of ['Só explique o erro da interface, sem alterar arquivos.',
  'Como investigar um erro na interface em geral?', 'A interface está funcionando.']) {
  assert.equal(ChatCore.isProjectFailureReport(text), false, text);
}

context.localStorage.getItem = () => null;
assert.equal(context.shouldAutoPursueAgent(report), false);
const blocked = ChatCore.formatAgentReport({status: 'blocked',
  finalText: 'Estou acompanhando. Pode me contar um pouco mais?',
  error: 'delivery.summary: falta síntese. debug.change: nenhuma escrita. debug.verification: sem check.',
  events: [{kind: 'engineering.context.observed', status: 'completed', detail: 'index.html'},
    {kind: 'implementation.unavailable', status: 'blocked', detail: 'O gerador não produziu um diff válido.'}]});
assert.match(blocked, /Arquivos lidos: index\.html/);
assert.match(blocked, /O gerador não produziu um diff válido/);
assert.match(blocked, /Nenhuma alteração em arquivo foi confirmada/);
assert.doesNotMatch(blocked, /Estou acompanhando|delivery\.summary/);
console.log('UI debug routing checks passed.');

const baseline = ChatCore.formatAgentReport({status: 'blocked', finalText: 'Investigação pendente.',
  verification: {executed: true, passed: true, summary: 'Verificação aprovada'}});
assert.match(baseline, /resolução do problema ainda não foi confirmada/);
assert.doesNotMatch(baseline, /Verificação aprovada/);
const unexecuted = ChatCore.formatAgentReport({status: 'blocked', finalText: 'Investigação pendente.',
  verification: {passed: true}});
assert.doesNotMatch(unexecuted, /Verificação: passou/);
