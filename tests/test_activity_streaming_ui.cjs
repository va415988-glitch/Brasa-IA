const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../runtime/static/app.js'), 'utf8');
const code = source.slice(source.indexOf('function consumeAgentAnswerEvent('), source.indexOf('async function drainActivityEvents('));
let created = 0;
const painted = [];
const drafts = new Map([['own-request', {draft: null, text: ''}]]);
const actions = new Map([['own-request', {}]]);
const context = {agentStreamDrafts: drafts, liveActions: actions, decodeURIComponent,
  createStreamingDraft: () => { created++; return {}; },
  updateStreamingDraft: (_, text) => painted.push(text), scrollChat: () => {}, window: {}};
vm.createContext(context);
vm.runInContext(code, context);
const delta = (operation, text) => context.consumeAgentAnswerEvent({operation, message: '__ANSWER_DELTA__ · ' + encodeURIComponent(text)});
assert.equal(delta('other-chat', 'Não deve aparecer'), true);
assert.equal(created, 0, 'another API client cannot create a draft in this chat');
assert.equal(drafts.size, 1);
delta('own-request', 'ação ');
delta('own-request', '🚀');
assert.equal(created, 1);
assert.equal(painted.at(-1), 'ação 🚀');
actions.delete('own-request');
delta('own-request', 'evento atrasado');
assert.equal(painted.at(-1), 'ação 🚀', 'late deltas cannot mutate a settled request');
assert.equal(context.consumeAgentAnswerEvent({operation: 'own-request', message: 'progresso comum'}), false);
console.log('PASS: streaming isolado por pedido, Unicode preservado e eventos atrasados ignorados.');
