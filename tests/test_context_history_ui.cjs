const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '..', 'runtime', 'static', 'app.js'), 'utf8');
const start = source.indexOf('const MAX_CHAT_HISTORY_BYTES=');
const end = source.indexOf('async function chatModel(', start);
assert(start >= 0 && end > start, 'requestMessages block should exist');

function invoke(messages) {
  const context = {conversation: messages, TextEncoder};
  vm.createContext(context);
  vm.runInContext(source.slice(start, end) + '\nglobalThis.invokeRequestMessages=requestMessages;', context);
  return context.invokeRequestMessages();
}

const shortHistory = Array.from({length: 150}, (_, index) => ({
  role: index % 2 ? 'assistant' : 'user',
  content: `Mensagem curta ${index}`,
}));
const allTurns = invoke(shortHistory);
assert.equal(allTurns.length, 150, 'keeps all turns when the request fits');
assert.equal(allTurns.at(-1).content, 'Mensagem curta 149');

const longHistory = Array.from({length: 200}, (_, index) => ({
  role: index % 2 ? 'assistant' : 'user',
  content: `${index === 0 ? 'Objetivo prioritário: manter a API local de pesquisa.' : `Atualização técnica ${index}:`} ${'memória contexto agente '.repeat(800)}`,
}));
longHistory[longHistory.length - 1].content = 'Pedido atual: valide o contexto dobrado.';
const compacted = invoke(longHistory);
const serializedBytes = new TextEncoder().encode(JSON.stringify(compacted)).length;
assert(serializedBytes <= 1536 * 1024, 'compacted request stays below the worker limit');
assert(compacted.length < longHistory.length, 'old messages are compacted');
assert(compacted[0].content.includes('Objetivo prioritário'), 'summary preserves an old goal');
assert.equal(compacted.at(-1).content, 'Pedido atual: valide o contexto dobrado.');
const routingContext = {
  conversation: [{role: 'user', content: 'Analise esta pasta', attachments: [{name: 'Perfumaria'}]}],
  localStorage: {getItem: () => '/workspace/ativo'},
  TextEncoder,
};
vm.createContext(routingContext);
vm.runInContext(source.slice(start, end) + '\nglobalThis.refersToAttachedProject=refersToAttachedProject;globalThis.asksToChangeAttachedProject=asksToChangeAttachedProject;', routingContext);
assert.equal(routingContext.refersToAttachedProject('E os testes desse projeto?'), true);
assert.equal(routingContext.refersToAttachedProject('Crie testes desse projeto'), false);
assert.equal(routingContext.asksToChangeAttachedProject('Crie testes desse projeto'), true);
assert.equal(routingContext.refersToAttachedProject('Analise meu projeto ativo'), false);
console.log(`UI history checks passed: 150 turns retained; 200 turns compacted to ${compacted.length}, ${serializedBytes} bytes.`);
