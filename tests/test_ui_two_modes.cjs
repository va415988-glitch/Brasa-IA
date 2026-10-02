const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'runtime/static/index.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'runtime/static/app.js'), 'utf8');

test('top-level navigation only exposes Chat and Treinamento', () => {
  const navigation = html.match(/<div class="mode-switch"[\s\S]*?<\/div>/)?.[0] || '';
  assert.match(navigation, /id="mode-chat"/);
  assert.match(navigation, /id="mode-training"/);
  assert.doesNotMatch(navigation, /mode-workspace|Workspace/);
});

test('legacy workspace selection resolves to Chat and files open in its editor', () => {
  assert.match(js, /const value=mode==='training'\?'training':'chat'/);
  assert.match(js, /function setEditorFile\(data\)[\s\S]*?has-editor/);
  assert.match(html, /body\.chat-mode\.has-editor \.workspace-stage \{ display: flex; \}/);
  assert.match(js, /function openArtifactWorkspace\(artifact\)[\s\S]*?setMode\('chat'\)/);
});
