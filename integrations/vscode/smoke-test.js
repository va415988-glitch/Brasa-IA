/* Smoke test da integração VS Code; o runtime deve estar em execução. */

const BASE = (process.env.IA_LOCAL_RUNTIME_URL || 'http://127.0.0.1:3000').replace(/\/$/, '');
const workspace = process.argv[2] || process.cwd().replace(/\/integrations\/vscode$/, '');

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, options);
  let body;
  try {
    body = await response.json();
  } catch (_) {
    body = {ok: false, error: `resposta não-JSON (HTTP ${response.status})`};
  }
  if (!response.ok) {
    throw new Error(`${path}: HTTP ${response.status}: ${body.error || JSON.stringify(body)}`);
  }
  return body;
}

async function tool(tool, arguments_, requestId) {
  return request('/api/tool-call', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({tool, arguments: arguments_, request_id: requestId}),
  });
}

async function chat(content, requestId) {
  const data = await request('/api/v1/agent/pursue', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({
      prompt: content,
      objective: 'auto',
      workspaceRoot: workspace,
      history: [],
      approved: false,
      operationId: `agent-core-${requestId}`,
    }),
  });
  return {
    ok: data.ok,
    backend: 'agent-core',
    text: data.report?.finalText || data.report?.error || data.error || '',
    status: data.report?.status,
  };
}

function check(condition, message, failures) {
  if (!condition) failures.push(message);
}

async function main() {
  const failures = [];
  const quality = [];
  const result = {base: BASE, workspace, checks: {}, quality, failures};
  try {
    const health = await request('/api/health');
    result.checks.health = health;
    check(health.ok === true, 'health não retornou ok=true', failures);

    const selected = await tool('set_workspace', {path: workspace}, 'vscode-smoke-workspace');
    result.checks.set_workspace = selected;
    check(selected.ok === true && selected.data?.selected === true,
      'set_workspace não confirmou o workspace', failures);

    const file = await tool('read_file', {path: 'integrations/vscode/package.json'}, 'vscode-smoke-read');
    result.checks.read_file = {ok: file.ok, path: file.data?.path, bytes: file.data?.bytes};
    check(file.ok === true && file.data?.content?.includes('ia-local-do-zero'),
      'read_file não devolveu o package.json da extensão', failures);

    const known = await chat('Explique o que é ownership em Rust.', 'vscode-smoke-known');
    result.checks.curated_chat = {ok: known.ok, backend: known.backend, text: known.text};
    check(known.ok === true && /dono|emprest|posse/i.test(known.text || ''),
      'a resposta curada de programação não foi reconhecida', failures);

    const review = await chat(
      'Revise este trecho JavaScript procurando um bug e sugira um teste.\n\n' +
      'Contexto local: src/example.js:1-1\n\n```javascript\nfunction first(items) { return items[1]; }\n```',
      'vscode-smoke-review'
    );
    const reviewText = review.text || '';
    result.checks.code_review = {ok: review.ok, backend: review.backend, text: reviewText};
    if (!/items\[1\]|índice|indice|undefined|primeiro|primeira/i.test(reviewText)) {
      quality.push('revisão de código não demonstrou entendimento do trecho; investigar antes de uso real');
    }
  } catch (error) {
    failures.push(error.message);
  }
  console.log(JSON.stringify(result, null, 2));
  if (failures.length) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
