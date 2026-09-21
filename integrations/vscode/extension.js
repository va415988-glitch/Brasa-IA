const vscode = require('vscode');

const API = 'http://127.0.0.1:3000/api/chat';
const TOOL_API = 'http://127.0.0.1:3000/api/tool-call';
const CHAT = 'http://127.0.0.1:3000/';

function workspacePath() {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || null;
}

async function ask(prompt, editor) {
  const workspace = workspacePath();
  if (!workspace) {
    vscode.window.showWarningMessage('Abra um workspace antes de usar a IA Local.');
    return;
  }
  const selection = editor?.selection;
  const code = editor && selection && !selection.isEmpty ? editor.document.getText(selection) : editor?.document.getText() || '';
  const label = editor && selection && !selection.isEmpty
    ? `${editor.document.fileName}:${selection.start.line + 1}-${selection.end.line + 1}`
    : editor?.document.fileName || workspace;
  const content = `${prompt}\n\nContexto local: ${label}\n\n\`\`\`${editor?.document.languageId || ''}\n${code.slice(0, 24000)}\n\`\`\``;
  const channel = vscode.window.createOutputChannel('IA Local do Zero');
  channel.show(true);
  channel.appendLine(`Analisando ${label}...`);
  try {
    const workspaceResponse = await fetch(TOOL_API, {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({tool: 'set_workspace', arguments: {path: workspace}, request_id: `vscode-workspace-${Date.now()}`})
    });
    const workspaceData = await workspaceResponse.json();
    if (!workspaceResponse.ok || !workspaceData.ok) throw new Error(workspaceData.error || 'não foi possível selecionar o workspace');
    const response = await fetch(API, {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({request_id: `vscode-${Date.now()}`, messages: [{role: 'user', content}]})
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    channel.appendLine(`\n${data.text || 'Nenhuma resposta.'}`);
    channel.appendLine(`\nBackend: ${data.backend || 'local'} · ${data.elapsed_ms ?? '?'} ms`);
  } catch (error) {
    channel.appendLine(`\nFalha: ${error.message}`);
    vscode.window.showErrorMessage(`IA Local indisponível: ${error.message}`);
  }
}

function parseProposal(text) {
  const fenced = text.match(/```json\s*([\s\S]*?)```/i);
  const candidate = fenced ? fenced[1] : text;
  try {
    const proposal = JSON.parse(candidate.trim());
    if (!proposal.path || typeof proposal.old_text !== 'string' || typeof proposal.new_text !== 'string') return null;
    return proposal;
  } catch (_) { return null; }
}

async function proposeEdit() {
  const editor = vscode.window.activeTextEditor;
  const workspace = workspacePath();
  if (!editor || !workspace) return vscode.window.showWarningMessage('Abra um arquivo dentro de um workspace.');
  const selection = editor.selection;
  if (selection.isEmpty) return vscode.window.showWarningMessage('Selecione o trecho que deve ser alterado.');
  const oldText = editor.document.getText(selection);
  const relativePath = require('node:path').relative(workspace, editor.document.uri.fsPath);
  if (!relativePath || relativePath.startsWith('..')) return vscode.window.showErrorMessage('O arquivo está fora do workspace.');
  const instruction = await vscode.window.showInputBox({prompt: 'O que a alteração deve fazer?', placeHolder: 'Ex.: corrigir o bug e manter a API compatível'});
  if (!instruction) return;
  const channel = vscode.window.createOutputChannel('IA Local do Zero');
  channel.show(true);
  try {
    const workspaceResponse = await fetch(TOOL_API, {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({tool:'set_workspace',arguments:{path:workspace},request_id:`vscode-workspace-${Date.now()}`})});
    const workspaceData = await workspaceResponse.json();
    if (!workspaceResponse.ok || !workspaceData.ok) throw new Error(workspaceData.error || 'workspace recusado');
    const response = await fetch(API, {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({request_id:`vscode-proposal-${Date.now()}`,messages:[{role:'user',content:`Proponha uma alteração para ${relativePath}. Objetivo: ${instruction}\n\nTrecho selecionado:\n${oldText}\n\nResponda somente com JSON válido neste formato: {"path":"${relativePath}","old_text":"trecho exato","new_text":"substituição","summary":"resumo"}. Não execute nada.`}]})});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
    const proposal = parseProposal(data.text || '');
    if (!proposal || proposal.path !== relativePath || proposal.old_text !== oldText) throw new Error('A resposta não trouxe uma proposta exata para o trecho selecionado.');
    const modified = await vscode.workspace.openTextDocument({language: editor.document.languageId, content: editor.document.getText().replace(oldText, proposal.new_text)});
    await vscode.commands.executeCommand('vscode.diff', editor.document.uri, modified.uri, `Prévia IA: ${relativePath}`);
    const confirm = await vscode.window.showWarningMessage('Aplicar esta alteração? O runtime criará backup antes de escrever.', {modal:true}, 'Aplicar');
    if (confirm !== 'Aplicar') return;
    const result = await fetch(TOOL_API, {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({tool:'edit_file',arguments:{path:relativePath,old_text:oldText,new_text:proposal.new_text},request_id:`vscode-edit-${Date.now()}`})});
    const editData = await result.json();
    if (!result.ok || !editData.ok) throw new Error(editData.error || 'edição recusada');
    channel.appendLine(`Alteração aplicada em ${relativePath}. Backup: ${editData.data?.backup || 'registrado pelo runtime'}`);
    await vscode.commands.executeCommand('workbench.action.files.revert');
  } catch (error) {
    channel.appendLine(`Falha na proposta: ${error.message}`);
    vscode.window.showErrorMessage(`IA Local: ${error.message}`);
  }
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand('iaLocal.explicarSelecao', () => ask('Explique este código, seus riscos e como testá-lo.', vscode.window.activeTextEditor)),
    vscode.commands.registerCommand('iaLocal.revisarArquivo', () => ask('Revise este arquivo procurando bugs, riscos e melhorias priorizadas.', vscode.window.activeTextEditor)),
    vscode.commands.registerCommand('iaLocal.proporAlteracao', proposeEdit),
    vscode.commands.registerCommand('iaLocal.abrirChat', () => vscode.env.openExternal(vscode.Uri.parse(CHAT)))
  );
}

function deactivate() {}
module.exports = {activate, deactivate};
