const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');

const source = fs.readFileSync(path.join(__dirname, 'extension.js'), 'utf8');
const host = {require: (name) => {
  assert.equal(name, 'vscode');
  return {};
}, module: {exports: {}}};
vm.runInNewContext(source + '\nmodule.exports.Sidebar = LocalSidebarProvider;', host);
const sidebar = new host.module.exports.Sidebar({subscriptions: []});
const html = sidebar.html({cspSource: 'http://127.0.0.1'});
const match = html.match(/<script nonce="([^"]+)">([\s\S]*?)<\/script>/);
assert.ok(match, 'The actual generated webview must contain its client script.');
// Checking extension.js alone cannot catch syntax errors inside its HTML string.
new vm.Script(match[2], {filename: 'generated-webview.js'});
assert.ok(html.includes("form-action 'none'"), 'A broken script must not allow form navigation.');
assert.ok(html.includes('history-panel'), 'The sidebar must expose conversation history.');
assert.ok(html.includes('conversation-list'), 'The webview must handle persisted conversation lists.');
console.log('generated-webview-script=ok');

function exerciseBrowser() {
  const check = (value, message) => { if (!value) throw new Error(message); };
  const deliver = (data) => window.dispatchEvent(new MessageEvent('message', {data}));
  try {
    const input = document.getElementById('input');
    const send = document.getElementById('send');
    const form = document.getElementById('form');
    const locationBefore = location.href;
    let prevented = false;
    // Registered after the real webview's listener: observes its preventDefault.
    form.addEventListener('submit', (event) => { prevented = event.defaultPrevented; });
    input.value = 'Quero criar um assistente pessoal';
    send.click();
    check(prevented, 'Clicking send must prevent native form navigation');
    check(window.sent.length === 1 && window.sent[0].type === 'send', 'Send must reach the host exactly once');
    check(window.sent[0].text === 'Quero criar um assistente pessoal', 'Prompt must be preserved');
    check(send.disabled, 'Send must be disabled while processing');
    deliver({type: 'user', text: window.sent[0].text});
    check(document.querySelector('.user .message-body').textContent === window.sent[0].text, 'User turn must render');
    deliver({type: 'progress', text: 'Inspecionando...'});
    deliver({type: 'assistant', text: '# Resultado\n\n**Pronto**.\n\n- Primeiro passo\n- Segundo passo\n\n```js\nconst x = 1;\n```\nPronto.'});
    check(document.querySelector('.assistant h1')?.textContent === 'Resultado', 'Markdown headings must render');
    check(document.querySelectorAll('.assistant li').length === 2, 'Markdown lists must render');
    check(document.querySelector('.assistant strong')?.textContent === 'Pronto', 'Markdown emphasis must render');
    check(document.querySelector('.assistant pre').textContent === 'const x = 1;\n', 'Code blocks must render as code');
    deliver({type: 'activity', entry: {text: 'Inspecionando workspace', state: 'running', at: Date.now()}});
    deliver({type: 'activity', entry: {text: 'Tarefa concluída', state: 'complete', at: Date.now()}});
    check(document.querySelector('.activity-card.complete')?.textContent.includes('Inspecionando workspace'), 'Activity log must render task steps');
    deliver({type: 'done'});
    check(!send.disabled, 'Send must be available after response');
    deliver({type: 'conversation-list', activeId: 'conversation-1', conversations: [{id: 'conversation-1', title: 'Criar um assistente pessoal', updatedAt: Date.now(), messageCount: 2}]});
    check(document.querySelector('.history-item')?.textContent.includes('Criar um assistente pessoal'), 'Conversation history must render in the sidebar');
    document.querySelector('.history-item').click();
    check(window.sent.at(-1).type === 'select-conversation' && window.sent.at(-1).id === 'conversation-1', 'Selecting history must request conversation restoration');

    for (const approved of [true, false]) {
      const id = 'approval-' + approved;
      deliver({type: 'approval', id, tool: 'create_file', reason: 'Criar arquivo', arguments: {path: 'a.txt', content: '<script>bad()</script>', _expected_workspace: '/private'}});
      const card = Array.from(document.querySelectorAll('.approval-card')).at(-1);
      check(card.querySelector('.approval-details').textContent.includes('Criar arquivo\npath: a.txt'), 'Approval detail newlines must survive HTML generation');
      check(!card.textContent.includes('/private'), 'Internal arguments should be hidden');
      check(!card.querySelector('script'), 'Tool content must be treated as text');
      card.querySelector(approved ? '.approval-approve' : '.approval-cancel').click();
      check(window.sent.at(-1).id === id && window.sent.at(-1).approved === approved, 'Approval decision must reach host');
      check(Array.from(card.querySelectorAll('button')).every(button => button.disabled), 'Decision buttons must be disabled after use');
    }

    deliver({type: 'error', text: 'Runtime indisponível'});
    deliver({type: 'done'});
    check(document.getElementById('messages').textContent.includes('Runtime indisponível'), 'Errors must stay in the chat');
    input.value = 'Segunda mensagem';
    input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true}));
    check(window.sent.at(-1).text === 'Segunda mensagem', 'Keyboard send must work after an error');
    check(document.querySelector('header') && document.querySelector('footer') && form.isConnected, 'Chat interface must remain attached');
    check(location.href === locationBefore, 'Chat document must not navigate');
    document.body.dataset.testResult = 'passed';
  } catch (error) {
    document.body.dataset.testResult = 'failed';
    const report = document.createElement('pre');
    report.textContent = error.stack;
    document.body.appendChild(report);
  }
}

async function browserTest() {
  const nonce = match[1];
  const bootstrap = `<script nonce="${nonce}">window.sent = []; window.acquireVsCodeApi = () => ({postMessage: message => window.sent.push(message)});</script>`;
  const exercise = exerciseBrowser.toString().replace(/<\/script/gi, '<\\/script');
  const fixture = html.replace('<script nonce=', bootstrap + '<script nonce=')
    .replace('</body>', `<script nonce="${nonce}">(${exercise})();</script></body>`);
  const server = http.createServer((_request, response) => {
    response.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
    response.end(fixture);
  });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ia-local-webview-test-'));
  try {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    const output = await new Promise((resolve, reject) => {
      const browser = spawn(process.env.CHROME_BIN || '/usr/bin/google-chrome', [
        '--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
        `--user-data-dir=${profile}`, '--dump-dom', '--timeout=10000',
        `http://127.0.0.1:${server.address().port}/`,
      ], {stdio: ['ignore', 'pipe', 'pipe']});
      let stdout = '', stderr = '';
      const timer = setTimeout(() => { browser.kill('SIGTERM'); reject(new Error('Headless browser timed out')); }, 20000);
      browser.stdout.on('data', chunk => { stdout += chunk; });
      browser.stderr.on('data', chunk => { stderr += chunk; });
      browser.once('error', error => { clearTimeout(timer); reject(error); });
      browser.once('close', code => {
        clearTimeout(timer);
        if (code !== 0) reject(new Error(`Browser exited ${code}: ${stderr.slice(-3000)}`));
        else resolve(stdout);
      });
    });
    assert.ok(output.includes('data-test-result="passed"'), output.slice(-5000));
    console.log('browser-webview-send-response-error-approvals=ok');
  } finally {
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(profile, {recursive: true, force: true});
  }
}

if (process.argv.includes('--browser')) browserTest().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
