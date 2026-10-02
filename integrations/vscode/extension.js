const vscode = require('vscode');

const AGENT_API = 'http://127.0.0.1:3000/api/v1/agent/pursue';
const TOOL_API = 'http://127.0.0.1:3000/api/tool-call';
const CHAT = 'http://127.0.0.1:3000/';
const SIDEBAR_VIEW_ID = 'ia-local.sidebar';
const MAX_AGENT_STEPS = 32;
const SIDEBAR_HISTORY_KEY = 'ia-local.sidebar.conversations.v1';
const MAX_SIDEBAR_CONVERSATIONS = 40;
const MAX_SIDEBAR_MESSAGES = 80;
const MAX_ACTIVITY_ENTRIES = 120;
const MODEL_VENDOR = 'ia-local';
const MODEL_ID = 'ia-local-zero';
const INSPECT_WORKSPACE_TOOL = 'iaLocal_inspectWorkspace';
const MODEL_MAX_INPUT_TOKENS = 256;
const MODEL_MAX_OUTPUT_TOKENS = 1024;
const READ_ONLY_TOOLS = new Set([
  'list_files', 'read_file', 'search_files', 'inspect_project', 'inspect_code',
  'path_info', 'find_paths', 'list_tree', 'compare_files', 'git_diff', 'list_tools',
  'inspect_media', 'extract_document_text', 'diagnose_project', 'project_checks',
  'list_sources', 'cite_sources', 'search_web', 'research_web', 'open_page', 'process_status',
]);

const FILE_WRITE_TOOLS = new Set(['create_file', 'create_web_page', 'edit_file', 'apply_repair', 'create_directory', 'apply_batch']);

function taskEvidence(messages) {
  const results = messages.filter((item) => item.role === 'tool').flatMap((item) => {
    try { return [JSON.parse(item.content)]; } catch (_) { return []; }
  });
  const writes = results.filter((item) => item.ok === true && FILE_WRITE_TOOLS.has(item.tool));
  const checks = results.filter((item) => item.tool === 'project_checks');
  const check = checks.at(-1);
  const lastWriteIndex = results.findLastIndex((item) => item.ok === true && FILE_WRITE_TOOLS.has(item.tool));
  const lastCheckIndex = results.findLastIndex((item) => item.tool === 'project_checks');
  const hasDiff = writes.every((item) => {
    const operations = item.tool === 'apply_batch' ? item.data?.operations || []
      : [{tool: item.tool, result: item.data}];
    return operations.filter((operation) => FILE_WRITE_TOOLS.has(operation?.tool)).every((operation) => {
      if (operation?.tool === 'create_directory') return typeof operation?.result?.path === 'string';
      const diff = operation?.result?.diff || operation?.result?.artifact?.diff;
      return diff?.format === 'line-v1' && Array.isArray(diff.lines);
    });
  });
  return {writes, check, checkAfterWrite: lastCheckIndex > lastWriteIndex, hasDiff};
}

function observedDiffText(messages) {
  const sections = [];
  for (const write of taskEvidence(messages).writes) {
    const operations = write.tool === 'apply_batch' ? write.data?.operations || []
      : [{tool: write.tool, result: write.data}];
    for (const operation of operations) {
      if (!FILE_WRITE_TOOLS.has(operation?.tool)) continue;
      const result = operation?.result || {};
      const diff = result.diff || result.artifact?.diff;
      if (operation.tool === 'create_directory' && typeof result.path === 'string') {
        sections.push(`\`${String(result.path).replaceAll('`', '\\`')}/\`\nDiretório criado e confirmado pelo runtime.`);
        continue;
      }
      if (typeof result.path !== 'string' || !Array.isArray(diff?.lines)) continue;
      const lines = diff.lines.filter((line) => line && ['add', 'remove'].includes(line.kind));
      if (!lines.length) continue;
      let content = lines.slice(0, 40).map((line) => (line.kind === 'add' ? '+' : '-') + String(line.text || '').slice(0, 1000)).join('\n');
      if (lines.length > 40 || content.length > 12000) content = content.slice(0, 12000) + '\n… diff limitado para exibição';
      const longestFence = Math.max(0, ...Array.from(content.matchAll(/`+/g), (match) => match[0].length));
      const fence = '`'.repeat(Math.max(3, longestFence + 1));
      sections.push(`\`${String(result.path).replaceAll('`', '\\`')}\`\n${fence}diff\n${content}\n${fence}`);
    }
  }
  return sections.length ? 'Diff observado nesta execução:\n\n' + sections.slice(0, 8).join('\n\n') : '';
}

function requestedFileChange(prompt) {
  const text = String(prompt || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  const action = /\b(?:crie|criar|implemente|implementar|corrija|corrigir|edite|editar|altere|alterar|refatore|refatorar|construa|construir|desenvolva|desenvolver|melhore|melhorar|ajuste|ajustar)\b/.test(text);
  const negated = /\b(?:nao|sem)\s+(?:crie|criar|implemente|implementar|corrija|corrigir|edite|editar|altere|alterar|refatore|refatorar|construa|construir|desenvolva|desenvolver|melhore|melhorar|ajuste|ajustar)\b/.test(text);
  const planningOnly = /^\s*(?:planeje|planejar|proponha|propor|sugira|sugerir|explique|explicar|como (?:eu )?(?:posso|devo))\b/.test(text)
    || /\bsem (?:alterar|editar|aplicar)\b/.test(text);
  return action && !negated && !planningOnly;
}

function requestedChangeInHistory(messages) {
  const users = messages.filter((item) => item.role === 'user');
  const cleanPrompt = (value) => String(value || '')
    .split('\n\nTrecho selecionado pelo usuário:')[0]
    .replace(/\nArquivo ativo: [^\n]+/g, '')
    .replace(/\nWorkspace local: [^\n]+/g, '')
    .trim();
  const current = cleanPrompt(users.at(-1)?.content);
  if (requestedFileChange(current)) return true;
  const normalized = current.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim();
  if (!/^(?:prossiga|prosseguir|continue|continuar|retome|retomar|pode fazer|pode prosseguir|faca isso|faz isso|faca pra mim|faz pra mim|sim|concordo)[.!?\s]*$/.test(normalized)) return false;
  return requestedFileChange(cleanPrompt(users.at(-2)?.content));
}

function appendToolResult(messages, tool, result) {
  messages.push({role: 'tool', content: JSON.stringify({tool: result.tool || tool,
    ok: result.ok, data: result.data, error: result.error})});
}

function verificationFailedAfterWrite(evidence) {
  return evidence.writes.length > 0 && evidence.checkAfterWrite
    && evidence.check?.ok === true && evidence.check.data?.executed === true
    && evidence.check.data?.passed === false;
}

function hasDiagnosisAfterLatestCheck(messages) {
  const results = messages.filter((item) => item.role === 'tool').flatMap((item) => {
    try { return [JSON.parse(item.content)]; } catch (_) { return []; }
  });
  const lastCheck = results.findLastIndex((item) => item.tool === 'project_checks');
  return lastCheck >= 0 && results.slice(lastCheck + 1).some((item) => item.tool === 'diagnose_project');
}

async function diagnoseFailedVerification(messages, evidence, workspace, report, signal, requestApproval) {
  report('Validação falhou · coletando diagnóstico antes de replanejar');
  const data = evidence.check.data || {};
  const result = await executeAgentTool({tool: 'diagnose_project', arguments: {
    check: String(data.check || data.command || 'project verification'), passed: false,
    executed: true, stdout: String(data.stdout || ''), stderr: String(data.stderr || data.message || ''),
  }, reason: 'A validação falhou; identificar a causa antes de propor nova alteração.'},
  workspace, report, signal, requestApproval);
  appendToolResult(messages, 'diagnose_project', result);
  return result;
}

function successfulToolResults(messages) {
  return messages.filter((item) => item.role === 'tool').flatMap((item) => {
    try {
      const result = JSON.parse(item.content);
      return result.ok === true ? [result] : [];
    } catch (_) { return []; }
  });
}

function contextPathFromInspection(data) {
  const paths = (key) => (Array.isArray(data?.[key]) ? data[key] : [])
    .map((item) => typeof item === 'string' ? item : item?.path)
    .filter((path) => typeof path === 'string' && path.length > 0);
  const files = paths('files');
  const byName = new Map(files.map((path) => [path.split(/[\\/]/).at(-1).toLowerCase(), path]));
  for (const name of ['readme.md', 'readme', 'readme.rst', 'readme.txt']) {
    if (byName.has(name)) return byName.get(name);
  }
  return paths('manifests')[0] || paths('entrypoints')[0]
    || files.find((path) => /\.(?:c|cc|cpp|h|hpp|rs|py|js|ts|tsx|go|java)$/i.test(path));
}

function mutationTargetPaths(call) {
  const operations = call?.tool === 'apply_batch' ? call.arguments?.operations || []
    : [{arguments: call?.arguments}];
  return [...new Set(operations.map((operation) => operation?.arguments?.path)
    .filter((path) => typeof path === 'string' && path.length > 0))];
}

function describeActionPlan(call, rationale = '') {
  const operations = call?.tool === 'apply_batch' ? call.arguments?.operations || []
    : [{tool: call?.tool, arguments: call?.arguments}];
  const steps = operations.slice(0, 8).map((operation) => {
    const tool = String(operation?.tool || call?.tool || 'alteração');
    const path = operation?.arguments?.path;
    return path ? `${tool} → ${path}` : tool;
  });
  const why = String(call?.reason || rationale || '').replace(/\s+/g, ' ').trim().slice(0, 320);
  return [
    `Plano de ação: ${steps.join(' → ') || 'aplicar a alteração solicitada'}`,
    why ? `Motivo: ${why}` : '',
    'Validação prevista: project_checks(auto); se falhar, diagnosticar antes de replanejar.',
  ].filter(Boolean).join('\n');
}

async function ensureMutationContext(messages, call, workspace, report, signal, requestApproval) {
  const results = successfulToolResults(messages);
  const inspected = results.findLast((item) => item.tool === 'inspect_project');
  const readPaths = new Set(results.filter((item) => ['read_file', 'extract_document_text'].includes(item.tool))
    .map((item) => item.data?.path).filter((path) => typeof path === 'string'));
  if (!inspected) {
    report('Plano de ação · inspecionar o workspace antes da alteração');
    const result = await executeAgentTool(
      {tool: 'inspect_project', arguments: {max_depth: 4}, reason: 'Pré-condição do fluxo: conhecer estrutura, stack e verificações antes de editar.'},
      workspace, report, signal, requestApproval,
    );
    messages.push({role: 'assistant', content: 'Vou inspecionar o workspace antes de aplicar a alteração planejada.'});
    messages.push({role: 'tool', content: JSON.stringify({tool: result.tool || 'inspect_project', ok: result.ok,
      data: result.data, error: result.error})});
    if (!result.ok) throw new Error(`não foi possível inspecionar o workspace: ${result.error || 'falha sem detalhes'}`);
    return true;
  }
  const files = (Array.isArray(inspected.data?.files) ? inspected.data.files : [])
    .map((item) => typeof item === 'string' ? item : item?.path).filter((path) => typeof path === 'string');
  const targets = mutationTargetPaths(call, inspected.data);
  const existingTargets = targets.filter((path) => files.includes(path));
  const contextPath = contextPathFromInspection(inspected.data);
  if (!existingTargets.length && files.length > 0 && !contextPath) {
    throw new Error('a inspeção encontrou arquivos, mas não selecionou um arquivo legível para sustentar a alteração');
  }
  const needed = existingTargets.length ? existingTargets : [contextPath];
  const path = needed.find((item) => item && !readPaths.has(item));
  if (path) {
    report(`Plano de ação · ler ${path} e conferir o contexto antes da alteração`);
    const result = await executeAgentTool(
      {tool: 'read_file', arguments: {path, start_line: 1, end_line: 160, max_bytes: 12000},
       reason: 'Pré-condição do fluxo: ler um arquivo central selecionado pela inspeção antes de editar.'},
      workspace, report, signal, requestApproval,
    );
    messages.push({role: 'assistant', content: `Vou ler ${path} antes de aplicar a alteração planejada.`});
    messages.push({role: 'tool', content: JSON.stringify({tool: result.tool || 'read_file', ok: result.ok,
      data: result.data, error: result.error})});
    if (!result.ok) throw new Error(`não foi possível ler ${path}: ${result.error || 'falha sem detalhes'}`);
    return true;
  }
  return false;
}

function workspacePath() {
  return vscode.workspace?.workspaceFolders?.[0]?.uri.fsPath || null;
}
function isCasualPrompt(prompt) {
  const text = String(prompt || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .trim();
  if (!text) return false;
  if (/\b(?:workspace|projeto|arquivo|pasta|sistema|aplicativo|codigo|programacao|api|terminal|teste|criar|crie|implementar|implemente|analisar|analise|pesquisar|pesquise)\b/.test(text)) {
    return false;
  }
  return /^(?:(?:oi|ola|bom dia|boa tarde|boa noite|e ai)[!,.? ]*(?:tudo bem)?|(?:tudo bem|como voce esta|como vai|obrigado|obrigada|valeu|beleza|perfeito|entendi|certo|show))[!,.? ]*$/.test(text);
}

async function postJson(url, payload, signal) {
  const response = await fetch(url, {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(payload),
    signal,
  });
  let data;
  try {
    data = await response.json();
  } catch (_) {
    throw new Error(`resposta não-JSON (HTTP ${response.status})`);
  }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function formatAgentReport(report = {}) {
  const rawAnswer = String(report.finalText || '').trim();
  const genericAnswer = /^(?:estou acompanhando\. pode me contar um pouco mais\?|a tarefa n[aã]o foi conclu[ií]da\.?|conclu[ií] a etapa\b)/i.test(rawAnswer);
  const answer = genericAnswer ? '' : rawAnswer;
  const events = Array.isArray(report.events) ? report.events : [];
  const observedFiles = [...new Set(events.filter((event) => event?.kind === 'engineering.context.observed' && event.status === 'completed')
    .map((event) => String(event.detail || '').trim()).filter(Boolean))].slice(0, 4);
  const concreteBlocker = [...events].reverse().find((event) => event?.status === 'blocked'
    && !['acceptance.failed', 'task.blocked'].includes(event.kind)
    && typeof event.detail === 'string' && event.detail.trim());
  const pendingEffects = [
    /\b(?:debug|build|testing)\.change\b/.test(String(report.error || '')) ? 'Nenhuma alteração em arquivo foi confirmada.' : '',
    /\b(?:debug|build|testing)\.verification\b/.test(String(report.error || '')) ? 'A alteração ainda não passou por uma verificação executada.' : '',
  ].filter(Boolean).join(' ');
  const artifacts = [...new Set((report.artifacts || [])
    .map((item) => String(item?.path || '').trim()).filter(Boolean))]
    .slice(0, 8)
    .map((path) => `- \`${path}\``)
    .join('\n');
  const verification = report.verification;
  const verificationSummary = String(verification?.summary || '').replace(/[.!?]+$/, '');
  const verificationText = verification?.executed === true
    ? `Verificação: ${verification.passed ? 'passou' : 'não passou'}${verificationSummary ? ` — ${verificationSummary}` : ''}.`
    : '';
  return [
    answer || (report.status === 'completed' ? 'A execução não entregou uma síntese verificável.'
      : `A investigação não foi concluída.${observedFiles.length ? ` Arquivos lidos: ${observedFiles.join(', ')}.` : ' Nenhum arquivo foi confirmado como lido.'}`),
    !answer && concreteBlocker ? `Bloqueio observado: ${concreteBlocker.detail}` : '',
    !answer ? pendingEffects : '',
    artifacts ? `Artefatos:\n${artifacts}` : '',
    verificationText,
    report.status !== 'completed' && report.error && !/\b(?:delivery\.summary|(?:debug|build|testing)\.(?:change|verification))\b/.test(report.error)
      ? `Motivo: ${report.error}` : '',
  ].filter(Boolean).join('\n\n');
}

function agentReportUiStatus(report = {}) {
  const status = String(report.status || '').toLowerCase();
  if (status === 'failed') return 'failed';
  if (status !== 'completed') return 'blocked';
  const acceptance = (report.events || []).findLast?.((event) => event?.kind === 'acceptance.evaluated');
  const answer = String(report.finalText || '').trim();
  const genericAnswer = /^(?:estou acompanhando\. pode me contar um pouco mais\?|a tarefa n[aã]o foi conclu[ií]da\.?|conclu[ií] a etapa\b)/i.test(answer);
  return answer && !genericAnswer && acceptance?.status === 'completed'
    ? 'complete'
    : 'blocked';
}

function agentApprovalRequest(report = {}) {
  const event = (report.events || []).findLast?.((item) => item?.kind === 'approval.required');
  if (!event) return null;
  const payload = event.payload || {};
  const arguments_ = Array.isArray(payload.batchPreview)
    ? {operations: payload.batchPreview}
    : payload.fileDiff && typeof payload.fileDiff === 'object'
      ? {
          path: payload.fileDiff.path,
          old_text: payload.fileDiff.oldText,
          new_text: payload.fileDiff.newText,
        }
    : payload.repairDiff && typeof payload.repairDiff === 'object'
      ? {
          path: payload.repairDiff.path,
          old_text: payload.repairDiff.oldText,
          new_text: payload.repairDiff.newText,
        }
      : event.detail ? {target: event.detail} : {};
  return {
    tool: String(payload.tool || 'ação protegida'),
    arguments: arguments_,
    reason: String(payload.reason || event.detail || 'A tarefa precisa de autorização para continuar.'),
  };
}

async function refreshExplorerForAgentArtifacts(report = {}) {
  const hasArtifacts = Array.isArray(report.artifacts)
    && report.artifacts.some((artifact) => typeof artifact?.path === 'string' && artifact.path.trim());
  if (!hasArtifacts || typeof vscode.commands?.executeCommand !== 'function') return;
  try {
    await vscode.commands.executeCommand('workbench.files.action.refreshFilesExplorer');
  } catch (_) {
    // O relatório e os arquivos persistidos continuam válidos mesmo se o host
    // não expuser o comando interno de atualização do Explorer.
  }
}

async function runAgentCoreWithModalApproval({prompt, workspace, history = [], signal, onEvent = () => {}}) {
  const operationId = `agent-core-vscode-command-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  let approved = false;
  for (let step = 0; step < MAX_AGENT_STEPS; step += 1) {
    const data = await postJson(AGENT_API, {
      prompt,
      objective: 'auto',
      workspaceRoot: workspace,
      history,
      approved,
      operationId,
    }, signal);
    const report = data.report || {};
    for (const event of report.events || []) onEvent(event);
    const approval = agentApprovalRequest(report);
    if (data.awaitingApproval === true && report.status === 'blocked' && approval) {
      const target = approval.arguments?.target || approval.arguments?.path || workspace || 'contexto atual';
      const choice = await vscode.window.showWarningMessage(
        `Brasa quer autorizar ${approval.tool} em ${target}.`,
        {modal: true, detail: approval.reason || 'A aprovação vale somente para esta ação do AgentCore.'},
        'Aprovar e continuar',
      );
      if (choice !== 'Aprovar e continuar') return {report, rejected: true};
      approved = true;
      continue;
    }
    await refreshExplorerForAgentArtifacts(report);
    return {report, rejected: false};
  }
  throw new Error(`limite de ${MAX_AGENT_STEPS} aprovações encadeadas atingido`);
}

async function selectWorkspace(workspace, requestId, signal) {
  const data = await postJson(TOOL_API, {
    tool: 'set_workspace',
    arguments: {path: workspace},
    request_id: requestId,
  }, signal);
  if (!data.ok) throw new Error(data.error || 'não foi possível selecionar o workspace');
  return data;
}

function normalizeWorkspacePath(value) {
  return String(value || '').replace(/[\\/]+$/, '');
}

function needsToolApproval(tool, arguments_, workspace) {
  const sameWorkspace = tool === 'set_workspace'
    && normalizeWorkspacePath(arguments_?.path) === normalizeWorkspacePath(workspace);
  return !sameWorkspace && (!READ_ONLY_TOOLS.has(tool) || arguments_?.save_to_corpus === true);
}

async function executeAgentTool(call, workspace, report, signal, requestApproval) {
  const tool = String(call?.tool || '');
  const arguments_ = {...(call?.arguments || {})};
  if (needsToolApproval(tool, arguments_, workspace)) {
    const approved = requestApproval
      ? await requestApproval({tool, arguments: arguments_, reason: call?.reason || ''})
      : await vscode.window.showWarningMessage(
        `A tarefa pede a ferramenta ${tool || 'desconhecida'}. Executar?`,
        {modal: true},
        'Executar'
      ) === 'Executar';
    if (!approved) {
      return {ok: false, tool, error: 'execução recusada pelo usuário'};
    }
  }
  report(`Ferramenta: ${tool}`);
  let result;
  try {
    result = await postJson(TOOL_API, {
      tool,
      arguments: {...arguments_, _expected_workspace: workspace},
      request_id: `vscode-tool-${Date.now()}`,
    }, signal);
  } catch (error) {
    if (signal?.aborted || error?.name === 'AbortError') throw error;
    return {ok: false, tool, error: String(error?.message || error)};
  }
  const data = result?.data || {};
  if (tool === 'process_start' && result.ok) {
    report(`Processo local iniciado · ${data.profile || arguments_.profile} · ${data.process_id || 'identificador indisponível'}`);
  } else if (tool === 'process_status' && result.ok) {
    const ready = data.readiness?.ready === true ? ` · pronto em ${data.readiness.url}`
      : data.readiness?.known ? ' · ainda não pronto' : ' · prontidão não confirmada';
    report(`Processo ${data.process_id || arguments_.process_id || 'mais recente'} · ${data.state || 'estado desconhecido'}${ready}`);
    for (const [stream, value] of [['stdout', data.stdout], ['stderr', data.stderr]]) {
      const lines = String(value?.text || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean).slice(0, 4);
      for (const line of lines) report(`${stream}: ${line.slice(0, 240)}`);
      if (value?.truncated) report(`${stream}: saída antiga descartada pelo limite do buffer`);
    }
  } else if (tool === 'process_stop' && result.ok) {
    report(`Processo ${data.process_id || arguments_.process_id || 'mais recente'} · ${data.state || 'encerrado'}`);
  } else if (tool === 'apply_batch' && result.ok) {
    report(`Lote aplicado · ${data.count || 0} operação(ões) · undo ${data.transaction_id || 'indisponível'}`);
    for (const item of data.operations || []) {
      const path = item?.result?.path;
      if (path) report(`Alterado: ${path}`);
    }
  } else if (tool === 'undo_batch' && result.ok) {
    report(`Lote desfeito · ${data.transaction_id || arguments_.transaction_id || 'mais recente'}`);
    for (const item of data.operations || []) if (item?.path) report(`Restaurado: ${item.path}`);
  }
  return result;
}

function languageModelPartText(part) {
  if (typeof part === 'string') return part;
  if (typeof part?.value === 'string') return part.value;
  if (typeof part?.text === 'string') return part.text;
  if (part == null) return '';
  try { return JSON.stringify(part); } catch (_) { return String(part); }
}

function languageModelMessages(messages) {
  return messages.map((message) => {
    const role = message?.role === vscode.LanguageModelChatMessageRole?.Assistant
      ? 'assistant'
      : 'user';
    const content = Array.from(message?.content || [])
      .map(languageModelPartText)
      .filter(Boolean)
      .join('');
    return {role, content};
  }).filter((message) => message.content);
}

function languageModelTextPart(value) {
  return new vscode.LanguageModelTextPart(value);
}

function createLanguageModelProvider() {
  return {
    async provideLanguageModelChatInformation() {
      return [{
        id: MODEL_ID,
        name: 'Brasa',
        family: 'ia-local',
        version: '0.1.0',
        maxInputTokens: MODEL_MAX_INPUT_TOKENS,
        maxOutputTokens: MODEL_MAX_OUTPUT_TOKENS,
        tooltip: 'Runtime local em 127.0.0.1:3000',
        detail: 'local · sem chave',
        capabilities: {imageInput: false, toolCalling: true},
      }];
    },

    async provideTokenCount(_, text) {
      const value = typeof text === 'string'
        ? text
        : Array.from(text?.content || []).map(languageModelPartText).join('');
      return Math.ceil(String(value).length / 4);
    },

    async provideLanguageModelChatResponse(model, requestMessages, _options, progress, token) {
      if (model?.id && model.id !== MODEL_ID) throw new Error(`modelo não suportado: ${model.id}`);
      const workspace = workspacePath();
      const controller = new AbortController();
      const cancellation = token?.onCancellationRequested(() => controller.abort());
      const report = (message) => progress.report(languageModelTextPart(String(message || '')));
      try {
        const messages = languageModelMessages(requestMessages);
        const current = [...messages].reverse().find((message) => message.role === 'user');
        if (!current) throw new Error('a requisição não contém uma mensagem de usuário');
        const currentIndex = messages.lastIndexOf(current);
        const history = messages.slice(0, currentIndex)
          .filter((message) => message.role === 'user' || message.role === 'assistant')
          .slice(-32);
        const result = await runAgentCoreWithModalApproval({
          prompt: current.content,
          workspace,
          history,
          signal: controller.signal,
          onEvent: (event) => {
            if (!event?.transient && !['assistant.stream.delta', 'assistant.stream.reset'].includes(event?.kind)) {
              report(event.detail ? `${event.title} · ${event.detail}` : event.title);
            }
          },
        });
        progress.report(languageModelTextPart(result.rejected
          ? 'A ação foi recusada; nenhum efeito pendente foi executado.'
          : formatAgentReport(result.report)));
      } catch (error) {
        if (error?.name === 'AbortError' || token?.isCancellationRequested) {
          progress.report(languageModelTextPart('Operação cancelada.'));
          return;
        }
        progress.report(languageModelTextPart(`Não consegui concluir a tarefa local: ${error.message}`));
      } finally {
        cancellation?.dispose();
      }
    },
  };
}

class InspectWorkspaceTool {
  async prepareInvocation(options) {
    const depth = Number(options?.input?.max_depth);
    return {
      invocationMessage: Number.isInteger(depth)
        ? `Inspecionando o workspace até a profundidade ${Math.min(6, Math.max(1, depth))}…`
        : 'Inspecionando o workspace local…',
    };
  }

  async invoke(options, token) {
    if (token?.isCancellationRequested) throw new Error('inspeção cancelada');
    const workspace = workspacePath();
    if (!workspace) {
      return new vscode.LanguageModelToolResult([
        new vscode.LanguageModelTextPart('Nenhum workspace está aberto no VS Code.'),
      ]);
    }
    const requestedDepth = Number(options?.input?.max_depth);
    const maxDepth = Number.isInteger(requestedDepth)
      ? Math.min(6, Math.max(1, requestedDepth))
      : 4;
    await selectWorkspace(workspace, `vscode-tool-workspace-${Date.now()}`);
    const result = await postJson(TOOL_API, {
      tool: 'inspect_project',
      arguments: {max_depth: maxDepth},
      request_id: `vscode-inspect-${Date.now()}`,
    });
    if (!result.ok) throw new Error(result.error || 'a inspeção do workspace falhou');
    const payload = result.data ?? result;
    return new vscode.LanguageModelToolResult([
      new vscode.LanguageModelTextPart(JSON.stringify(payload, null, 2)),
    ]);
  }
}

function createChatSessionBridge(participant) {
  if (!vscode.chat?.registerChatSessionContentProvider ||
      !vscode.Uri?.parse || !participant) return null;
  const changes = new vscode.EventEmitter();
  const sessions = new Map();
  const defaultResource = vscode.Uri.parse('ia-local:/session/default');
  sessions.set(defaultResource.toString(), {title: 'Nova conversa', resource: defaultResource, history: []});
  const itemData = (session) => ({
    resource: session.resource,
    label: session.title,
    description: 'Runtime local · Brasa',
    status: vscode.ChatSessionStatus?.Completed,
    tooltip: 'Conversa local sem chave de API',
  });
  const decorateItem = (item, session) => {
    item.label = session.title;
    item.description = 'Runtime local · Brasa';
    item.status = vscode.ChatSessionStatus?.Completed;
    item.tooltip = 'Conversa local sem chave de API';
    return item;
  };
  let controller = null;
  let itemProvider = null;
  if (typeof vscode.chat.createChatSessionItemController === 'function') {
    try {
      controller = vscode.chat.createChatSessionItemController('ia-local', async () => {
        controller.items.replace(Array.from(sessions.values()).map((session) => {
          const item = controller.createChatSessionItem(session.resource, session.title);
          return decorateItem(item, session);
        }));
      });
      controller.newChatSessionItemHandler = async (context) => {
        const id = `${Date.now().toString(36)}-${sessions.size}`;
        const resource = vscode.Uri.parse(`ia-local:/session/${id}`);
        const title = String(context?.request?.prompt || 'Nova conversa').trim() || 'Nova conversa';
        const session = {title, resource, history: []};
        sessions.set(resource.toString(), session);
        const item = controller.createChatSessionItem(resource, title);
        decorateItem(item, session);
        controller.items.add(item);
        return item;
      };
    } catch (error) {
      console.warn(`IA Local: controlador de sessões indisponível: ${error.message}`);
      controller = null;
    }
  }
  if (!controller && vscode.chat?.registerChatSessionItemProvider && vscode.EventEmitter) {
    itemProvider = {
      onDidChangeChatSessionItems: changes.event,
      provideChatSessionItems: async () => Array.from(sessions.values()).map(itemData),
    };
  }
  const contentProvider = {
    provideChatSessionContent: async (resource, _token, context) => {
      const key = resource.toString();
      let session = sessions.get(key);
      if (!session) {
        session = {title: 'Nova conversa', resource, history: []};
        sessions.set(key, session);
        changes.fire();
      }
      return {
        title: session.title,
        history: session.history,
        requestHandler: handleChatParticipant,
        inputState: context?.inputState,
      };
    },
  };
  return {
    controller,
    itemProvider,
    contentProvider,
    dispose() { controller?.dispose(); changes.dispose(); },
  };
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

class LocalSidebarProvider {
  constructor(context) {
    this.context = context;
    this.view = null;
    this.busy = false;
    this.storage = context.workspaceState || context.globalState || {
      get: () => undefined,
      update: async () => undefined,
    };
    this.conversations = this.loadConversations();
    this.activeConversationId = this.conversations[0]?.id || null;
    this.history = this.conversations[0]?.messages || [];
    this.activity = this.conversations[0]?.activity || [];
    this.pendingApprovals = new Map();
    this.approvalSequence = 0;
  }

  loadConversations() {
    const saved = this.storage.get?.(SIDEBAR_HISTORY_KEY, []);
    if (!Array.isArray(saved)) return [];
    return saved
      .filter((item) => item && typeof item.id === 'string' && Array.isArray(item.messages))
      .map((item) => ({
        id: item.id,
        title: String(item.title || 'Nova conversa').slice(0, 120),
        updatedAt: Number(item.updatedAt) || 0,
        messages: item.messages
          .filter((message) => message && ['user', 'assistant'].includes(message.role) && typeof message.content === 'string')
          .slice(-MAX_SIDEBAR_MESSAGES)
          .map((message) => ({role: message.role, content: message.content.slice(0, 24000), status: ['complete', 'blocked', 'failed'].includes(message.status) ? message.status : undefined})),
        activity: Array.isArray(item.activity)
          ? item.activity.slice(-MAX_ACTIVITY_ENTRIES).map((entry) => ({
            text: String(entry.text || '').slice(0, 500),
            state: ['running', 'complete', 'failed', 'blocked'].includes(entry.state) ? entry.state : 'running',
            at: Number(entry.at) || 0,
          }))
          : [],
      }))
      .filter((item) => item.messages.length)
      .sort((left, right) => right.updatedAt - left.updatedAt)
      .slice(0, MAX_SIDEBAR_CONVERSATIONS);
  }

  conversationList() {
    return this.conversations.map((item) => ({
      id: item.id,
      title: item.title,
      updatedAt: item.updatedAt,
      messageCount: item.messages.length,
    }));
  }

  persistHistory() {
    const pending = this.storage.update?.(SIDEBAR_HISTORY_KEY, this.conversations);
    if (pending?.catch) pending.catch((error) => {
      console.warn(`IA Local: não foi possível persistir o histórico: ${error.message}`);
    });
  }

  postHistory() {
    this.post({type: 'history', messages: this.history});
    this.post({type: 'activity-history', entries: this.activity || []});
    this.post({type: 'conversation-list', conversations: this.conversationList(), activeId: this.activeConversationId});
  }

  resetActivity() {
    this.activity = [];
    this.post({type: 'activity-reset'});
  }

  logActivity(text, state = 'running') {
    const entry = {text: String(text || '').trim(), state, at: Date.now()};
    if (!entry.text) return;
    this.activity = [...(this.activity || []), entry].slice(-MAX_ACTIVITY_ENTRIES);
    this.post({type: 'activity', entry});
    this.post({type: 'progress', text: entry.text});
  }

  ensureConversation(prompt) {
    if (!this.activeConversationId) this.activeConversationId = `conversation-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const existing = this.conversations.find((item) => item.id === this.activeConversationId);
    if (existing) return existing;
    const conversation = {
      id: this.activeConversationId,
      title: String(prompt || 'Nova conversa').replace(/\s+/g, ' ').trim().slice(0, 120) || 'Nova conversa',
      updatedAt: Date.now(),
      messages: [],
    };
    this.conversations.unshift(conversation);
    return conversation;
  }

  saveHistory() {
    if (!this.history.some((message) => message.role === 'user')) return;
    const conversation = this.ensureConversation(this.history.find((message) => message.role === 'user')?.content);
    conversation.messages = this.history.slice(-MAX_SIDEBAR_MESSAGES).map((message) => ({
      role: message.role,
      content: String(message.content || '').slice(0, 24000),
      status: ['complete', 'blocked', 'failed'].includes(message.status) ? message.status : undefined,
    }));
    conversation.activity = (this.activity || []).slice(-MAX_ACTIVITY_ENTRIES);
    conversation.updatedAt = Date.now();
    this.conversations = [conversation, ...this.conversations.filter((item) => item.id !== conversation.id)]
      .slice(0, MAX_SIDEBAR_CONVERSATIONS);
    this.persistHistory();
    this.post({type: 'conversation-list', conversations: this.conversationList(), activeId: this.activeConversationId});
  }

  selectConversation(id) {
    if (this.busy) return;
    const conversation = this.conversations.find((item) => item.id === String(id));
    if (!conversation) return;
    this.activeConversationId = conversation.id;
    this.history = conversation.messages.map((message) => ({...message}));
    this.activity = (conversation.activity || []).map((entry) => ({...entry}));
    this.postHistory();
  }

  startNewConversation() {
    if (this.busy) return;
    this.saveHistory();
    this.activeConversationId = null;
    this.history = [];
    this.activity = [];
    this.postHistory();
  }

  resolveWebviewView(webviewView) {
    this.view = webviewView;
    webviewView.webview.options = {enableScripts: true};
    webviewView.webview.html = this.html(webviewView.webview);
    this.postHistory();
    this.post({type: 'context', context: this.editorContext()});
    webviewView.webview.onDidReceiveMessage((message) => {
      if (message?.type === 'send') this.handlePrompt(message.text);
      if (message?.type === 'approval-decision') this.resolveToolApproval(message);
      if (message?.type === 'new-chat') this.startNewConversation();
      if (message?.type === 'select-conversation') this.selectConversation(message.id);
      if (message?.type === 'refresh-context') this.post({type: 'context', context: this.editorContext()});
      if (message?.type === 'open-app') vscode.env.openExternal(vscode.Uri.parse(CHAT));
    }, undefined, this.context.subscriptions);
    webviewView.onDidDispose(() => {
      if (this.view === webviewView) this.view = null;
      this.cancelToolApprovals();
    }, undefined, this.context.subscriptions);
  }

  post(message) {
    this.view?.webview.postMessage(message);
  }

  requestToolApproval(call) {
    const id = `approval-${Date.now()}-${this.approvalSequence += 1}`;
    this.post({
      type: 'approval',
      id,
      tool: call.tool,
      arguments: call.arguments,
      reason: call.reason,
    });
    return new Promise((resolve) => this.pendingApprovals.set(id, resolve));
  }

  resolveToolApproval(message) {
    const id = String(message?.id || '');
    const resolve = this.pendingApprovals.get(id);
    if (!resolve) return;
    this.pendingApprovals.delete(id);
    resolve(message.approved === true);
  }

  cancelToolApprovals() {
    for (const resolve of this.pendingApprovals.values()) resolve(false);
    this.pendingApprovals.clear();
  }

  editorContext() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return {label: workspacePath() ? 'Workspace: ' + require('node:path').basename(workspacePath()) : 'Abra um workspace', language: '', hasSelection: false};
    const selection = editor.selection;
    const hasSelection = Boolean(selection && !selection.isEmpty);
    const label = hasSelection
      ? `${editor.document.fileName.split('/').pop()}:${selection.start.line + 1}-${selection.end.line + 1}`
      : editor.document.fileName.split('/').pop();
    return {label, language: editor.document.languageId || '', hasSelection};
  }

  async handleAgentCoreTask(prompt, workspace, history, signal) {
    let approved = false;
    const loggedEvents = new Set();
    while (true) {
      const operationId = `agent-core-vscode-sidebar-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
      const data = await postJson(AGENT_API, {
        prompt,
        objective: 'auto',
        workspaceRoot: workspace,
        history: history.slice(-60).map((message) => ({
          role: message.role,
          content: String(message.content || '').slice(0, 12000),
        })),
        approved,
        operationId,
      }, signal);
      if (!data.ok || !data.report) throw new Error(data.error || 'O AgentCore não devolveu um relatório de tarefa.');
      const report = data.report;
      for (const event of report.events || []) {
        if (event?.transient || ['assistant.stream.delta', 'assistant.stream.reset'].includes(event?.kind)) continue;
        const key = `${report.taskId || ''}:${event.seq ?? event.eventId ?? ''}:${event.kind || ''}`;
        if (loggedEvents.has(key)) continue;
        loggedEvents.add(key);
        const detail = String(event.detail || '').trim();
        const text = [event.title, detail && detail !== event.title ? detail : ''].filter(Boolean).join(' · ');
        const state = event.status === 'completed' ? 'complete'
          : event.status === 'failed' ? 'failed'
            : event.status === 'blocked' ? 'blocked' : 'running';
        this.logActivity(text || event.kind || 'Atualização do AgentCore', state);
      }

      if (data.awaitingApproval === true && report.status === 'blocked') {
        const approval = agentApprovalRequest(report);
        if (!approval) throw new Error('O AgentCore aguardou aprovação sem identificar a ação protegida.');
        if (!await this.requestToolApproval(approval)) {
          return {
            text: 'A ação protegida foi cancelada. Nenhuma autorização foi enviada ao AgentCore.',
            status: 'blocked',
          };
        }
        approved = true;
        this.logActivity('Aprovação concedida · retomando a mesma tarefa');
        continue;
      }

      const status = agentReportUiStatus(report);
      const text = formatAgentReport(status === 'complete' ? report : {...report, status: 'blocked'});
      await refreshExplorerForAgentArtifacts(report);
      return {text, status};
    }
  }

  async handlePrompt(rawPrompt) {
    const prompt = String(rawPrompt || '').trim();
    if (!prompt || this.busy) return;
    const workspace = workspacePath();
    this.busy = true;
    this.resetActivity();
    const editor = vscode.window.activeTextEditor;
    const selection = editor?.selection;
    const code = editor && selection && !selection.isEmpty
      ? editor.document.getText(selection)
      : '';
    const contextLabel = editor
      ? (workspace ? workspaceRelativeFile(editor.document.uri, workspace) : '') || editor.document.fileName
      : workspace;
    const editorLocation = editor
      ? `\nArquivo ativo: ${contextLabel} · ${editor.document.languageId} · ${editor.document.lineCount} linhas · cursor na linha ${(selection?.active?.line ?? 0) + 1}.`
      : '';
    const selectedCode = code ? `\n\nTrecho selecionado pelo usuário:\n\n\`\`\`${editor.document.languageId || ''}\n${code.slice(0, 12000)}\n\`\`\`` : '';
    const apiPrompt = `${prompt}${editorLocation}${selectedCode}`;
    this.ensureConversation(prompt);
    this.history.push({role: 'user', content: prompt});
    this.saveHistory();
    this.post({type: 'user', text: prompt});
    this.logActivity('Mensagem recebida · AgentCore classificando a intenção');
    const controller = new AbortController();
    try {
      {
        const priorHistory = this.history.slice(0, -1);
        const result = await this.handleAgentCoreTask(apiPrompt, workspace, priorHistory, controller.signal);
        this.history.push({role: 'assistant', content: result.text, status: result.status});
        this.logActivity(
          result.status === 'complete' ? 'Tarefa concluída pelo AgentCore · resposta entregue'
            : result.status === 'failed' ? 'Tarefa falhou no AgentCore'
              : 'Tarefa pendente no AgentCore · resultado requer atenção',
          result.status,
        );
        this.saveHistory();
        this.post({type: 'assistant', text: result.text, status: result.status});
        return;
      }
    } catch (error) {
      const text = `Não consegui concluir a tarefa local: ${error.message}`;
      this.history.push({role: 'assistant', content: text, status: 'failed'});
      this.logActivity('Tarefa interrompida · erro registrado', 'failed');
      this.saveHistory();
      this.post({type: 'error', text, status: 'failed'});
    } finally {
      this.busy = false;
      this.post({type: 'done'});
    }
  }

  html(webview) {
    const nonce = `${Date.now()}${Math.random().toString(16).slice(2)}`;
    const csp = `default-src 'none'; form-action 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';`;
    return `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin:0; font:13px/1.45 var(--vscode-font-family); color:var(--vscode-foreground); background:var(--vscode-sideBar-background); }
  .shell { min-height:100vh; display:flex; flex-direction:column; }
  header { display:flex; align-items:center; gap:9px; padding:12px 14px 10px; border-bottom:1px solid var(--vscode-sideBarSectionHeader-border, transparent); }
  .logo { display:grid; place-items:center; width:25px; height:25px; border-radius:7px; color:var(--vscode-button-foreground); background:var(--vscode-button-background); font-size:14px; }
  h1 { margin:0; font-size:13px; font-weight:600; }
  .muted { color:var(--vscode-descriptionForeground); font-size:11px; }
  .grow { flex:1; min-width:0; }
  .header-actions { display:flex; gap:2px; }
  .icon-button, .link-button { border:0; color:var(--vscode-foreground); background:transparent; border-radius:5px; cursor:pointer; padding:5px 7px; font:inherit; }
  .icon-button:hover, .link-button:hover { background:var(--vscode-toolbar-hoverBackground); }
  .link-button { color:var(--vscode-textLink-foreground); font-size:11px; }
  .context { margin:10px 12px 0; display:flex; align-items:center; gap:6px; color:var(--vscode-descriptionForeground); font-size:11px; }
  .context-dot { width:6px; height:6px; border-radius:50%; background:var(--vscode-testing-iconPassed, #4ec9b0); flex:none; }
  .context-label { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .quick { display:flex; gap:5px; overflow:auto; padding:10px 12px 6px; }
  .quick button { white-space:nowrap; border:1px solid var(--vscode-button-secondaryBackground, var(--vscode-input-border)); border-radius:5px; padding:5px 8px; color:var(--vscode-button-secondaryForeground, var(--vscode-foreground)); background:var(--vscode-button-secondaryBackground, transparent); cursor:pointer; font:11px var(--vscode-font-family); }
  .quick button:hover { background:var(--vscode-button-secondaryHoverBackground, var(--vscode-toolbar-hoverBackground)); }
  .history-panel { margin:4px 12px 0; padding:8px 0 2px; border-top:1px solid var(--vscode-sideBarSectionHeader-border, transparent); }
  .history-head { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:5px; color:var(--vscode-descriptionForeground); font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; }
  .history-new { border:0; padding:2px 5px; border-radius:4px; color:var(--vscode-textLink-foreground); background:transparent; cursor:pointer; font:10px var(--vscode-font-family); text-transform:none; letter-spacing:0; }
  .history-new:hover { background:var(--vscode-toolbar-hoverBackground); }
  #history-list { display:flex; flex-direction:column; gap:2px; max-height:154px; overflow:auto; }
  .history-empty { padding:5px 2px; color:var(--vscode-descriptionForeground); font-size:11px; }
  .history-item { display:flex; align-items:flex-start; width:100%; padding:6px 7px; border:1px solid transparent; border-radius:6px; color:var(--vscode-foreground); background:transparent; cursor:pointer; text-align:left; font:11px/1.35 var(--vscode-font-family); }
  .history-item:hover { background:var(--vscode-list-hoverBackground); }
  .history-item.active { border-color:var(--vscode-focusBorder); background:var(--vscode-list-activeSelectionBackground); color:var(--vscode-list-activeSelectionForeground); }
  .history-item-title { flex:1; min-width:0; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow-wrap:anywhere; }
  .history-item-meta { flex:none; margin-left:7px; color:var(--vscode-descriptionForeground); font-size:9px; white-space:nowrap; }
  .history-item.active .history-item-meta { color:inherit; opacity:.75; }
  #messages { flex:1; display:flex; flex-direction:column; gap:11px; overflow:auto; padding:10px 12px 16px; }
  .empty { margin:auto 8px; text-align:center; color:var(--vscode-descriptionForeground); }
  .empty strong { display:block; color:var(--vscode-foreground); font-size:13px; margin-bottom:4px; }
  .message { max-width:100%; overflow-wrap:anywhere; }
  .message-head { display:flex; align-items:center; gap:6px; margin-bottom:3px; color:var(--vscode-descriptionForeground); font-size:10px; }
  .message-body { white-space:pre-wrap; }
  .message-body p { margin:0 0 9px; line-height:1.55; }
  .message-body h1, .message-body h2, .message-body h3 { margin:12px 0 6px; color:var(--vscode-foreground); line-height:1.25; }
  .message-body h1 { font-size:17px; } .message-body h2 { font-size:15px; } .message-body h3 { font-size:13px; }
  .message-body ul, .message-body ol { margin:5px 0 10px 20px; padding:0; }
  .message-body li { margin:4px 0; line-height:1.5; }
  .message-body blockquote { margin:8px 0; padding:5px 10px; border-left:3px solid var(--vscode-textLink-foreground); color:var(--vscode-descriptionForeground); background:var(--vscode-textBlockQuote-background, transparent); }
  .message-body strong { color:var(--vscode-foreground); font-weight:700; }
  .message-body em { color:var(--vscode-descriptionForeground); }
  .message-body a { color:var(--vscode-textLink-foreground); }
  .message-body code { padding:1px 4px; border-radius:3px; color:var(--vscode-textPreformat-foreground); background:var(--vscode-textCodeBlock-background); font:12px var(--vscode-editor-font-family); }
  .md-code { margin:8px 0 11px; border:1px solid var(--vscode-textCodeBlock-background); border-radius:7px; overflow:hidden; background:var(--vscode-textCodeBlock-background); }
  .md-code-head { display:flex; justify-content:space-between; padding:5px 8px; color:var(--vscode-descriptionForeground); border-bottom:1px solid var(--vscode-input-border, transparent); font-size:10px; }
  .md-code-copy { border:0; color:var(--vscode-textLink-foreground); background:transparent; cursor:pointer; font:10px var(--vscode-font-family); }
  .md-code pre { margin:0; padding:9px; overflow:auto; white-space:pre; color:var(--vscode-textPreformat-foreground); background:transparent; font:12px/1.5 var(--vscode-editor-font-family); }
  .user { align-self:flex-end; max-width:92%; padding:7px 9px; border-radius:8px; color:var(--vscode-button-foreground); background:var(--vscode-button-background); }
  .user .message-head { color:color-mix(in srgb, var(--vscode-button-foreground) 70%, transparent); }
  .assistant { align-self:stretch; padding:2px 0; }
  .assistant pre { margin:7px 0; padding:8px; overflow:auto; border-radius:5px; color:var(--vscode-textPreformat-foreground); background:var(--vscode-textCodeBlock-background); font:12px/1.45 var(--vscode-editor-font-family); }
  .assistant code { padding:1px 3px; border-radius:3px; background:var(--vscode-textCodeBlock-background); font:12px var(--vscode-editor-font-family); }
  .approval-card { margin-top:5px; padding:11px; border:1px solid var(--vscode-panel-border, var(--vscode-input-border)); border-radius:8px; background:var(--vscode-editorWidget-background); box-shadow:0 8px 20px rgba(0,0,0,.12); }
  .approval-card strong { display:block; margin-bottom:4px; color:var(--vscode-foreground); }
  .approval-details { margin:8px 0; border:1px solid var(--vscode-input-border); border-radius:6px; overflow:hidden; color:var(--vscode-descriptionForeground); }
  .approval-details summary { padding:6px 8px; cursor:pointer; font-size:11px; }
  .approval-details summary:hover { background:var(--vscode-list-hoverBackground); }
  .approval-preview { max-height:180px; margin:0; padding:8px; overflow:auto; border-top:1px solid var(--vscode-input-border); white-space:pre-wrap; color:var(--vscode-textPreformat-foreground); background:var(--vscode-textCodeBlock-background); font:11px/1.45 var(--vscode-editor-font-family); }
  .approval-actions { display:flex; gap:6px; }
  .approval-actions button { border:0; border-radius:4px; padding:5px 9px; cursor:pointer; font:12px var(--vscode-font-family); }
  .approval-approve { color:var(--vscode-button-foreground); background:var(--vscode-button-background); }
  .approval-cancel { color:var(--vscode-button-secondaryForeground, var(--vscode-foreground)); background:var(--vscode-button-secondaryBackground, transparent); }
  .approval-actions button:disabled { opacity:.55; cursor:default; }
  .message-copy { float:right; border:0; color:var(--vscode-descriptionForeground); background:transparent; cursor:pointer; font-size:10px; }
  .message-copy:hover { color:var(--vscode-textLink-foreground); }
  .status { display:flex; align-items:center; gap:6px; padding:4px 12px; color:var(--vscode-descriptionForeground); font-size:11px; }
  .status-dot { width:6px; height:6px; border-radius:50%; background:var(--vscode-charts-blue, #569cd6); }
  .activity-card { margin:2px 0 4px; border:1px solid var(--vscode-panel-border, var(--vscode-input-border)); border-radius:7px; background:var(--vscode-editorWidget-background); }
  .activity-card summary { padding:7px 9px; color:var(--vscode-descriptionForeground); cursor:pointer; font-size:11px; font-weight:600; }
  .activity-card.running summary { color:var(--vscode-textLink-foreground); }
  .activity-card.complete summary { color:var(--vscode-testing-iconPassed, #4ec9b0); }
  .activity-card.failed summary, .activity-card.blocked summary { color:var(--vscode-testing-iconFailed, #f48771); }
  .activity-list { padding:0 9px 8px; }
  .activity-entry { display:flex; align-items:flex-start; gap:7px; padding:4px 0; color:var(--vscode-descriptionForeground); font-size:11px; line-height:1.35; }
  .activity-entry-dot { flex:none; width:6px; height:6px; margin-top:4px; border-radius:50%; background:var(--vscode-charts-blue, #569cd6); }
  .activity-entry.complete .activity-entry-dot { background:var(--vscode-testing-iconPassed, #4ec9b0); }
  .activity-entry.failed .activity-entry-dot, .activity-entry.blocked .activity-entry-dot { background:var(--vscode-testing-iconFailed, #f48771); }
  .activity-entry-time { flex:none; margin-left:auto; opacity:.6; font-size:9px; }
  footer { padding:0 12px 10px; background:var(--vscode-sideBar-background); }
  form { display:flex; gap:6px; align-items:flex-end; padding:8px; border:1px solid var(--vscode-input-border, transparent); border-radius:7px; background:var(--vscode-input-background); }
  textarea { flex:1; min-height:38px; max-height:130px; resize:vertical; border:0; outline:0; padding:3px; color:var(--vscode-input-foreground); background:transparent; font:13px/1.4 var(--vscode-font-family); }
  textarea::placeholder { color:var(--vscode-input-placeholderForeground); }
  .send { width:28px; height:28px; border:0; border-radius:5px; color:var(--vscode-button-foreground); background:var(--vscode-button-background); cursor:pointer; }
  .send:hover { background:var(--vscode-button-hoverBackground); }
  .send:disabled { opacity:.5; cursor:default; }
  .hint { padding:5px 2px 0; color:var(--vscode-descriptionForeground); font-size:10px; }
  .header-state { display:flex; align-items:center; gap:5px; margin-left:auto; padding:3px 7px; border:1px solid var(--vscode-input-border, transparent); border-radius:999px; color:var(--vscode-descriptionForeground); font-size:10px; white-space:nowrap; }
  .header-state::before { content:""; width:6px; height:6px; border-radius:50%; background:var(--vscode-testing-iconPassed, #4ec9b0); }
  .context { min-height:32px; padding:7px 9px; border:1px solid var(--vscode-panel-border, var(--vscode-input-border)); border-radius:7px; background:color-mix(in srgb, var(--vscode-editorWidget-background) 55%, transparent); }
  .context-label { flex:1; min-width:0; }
  .quick { padding-top:9px; padding-bottom:7px; scrollbar-width:thin; }
  .quick button { border-radius:999px; padding:5px 9px; transition:background .15s, border-color .15s, transform .15s; }
  .quick button:hover { border-color:var(--vscode-focusBorder); transform:translateY(-1px); }
  #history-list { max-height:130px; scrollbar-width:thin; }
  #messages { gap:12px; padding:12px 12px 18px; scrollbar-gutter:stable; scrollbar-width:thin; }
  .empty { margin:auto 8px; padding:18px 12px; border:1px dashed var(--vscode-panel-border, var(--vscode-input-border)); border-radius:10px; background:color-mix(in srgb, var(--vscode-editorWidget-background) 30%, transparent); }
  .empty strong { margin-bottom:5px; }
  .message-head { margin:0 2px 5px; }
  .message-head::before { content:""; width:5px; height:5px; border-radius:50%; background:var(--vscode-descriptionForeground); opacity:.7; }
  .user { padding:9px 11px; border:1px solid color-mix(in srgb, var(--vscode-button-background) 75%, transparent); border-radius:10px 10px 3px 10px; box-shadow:0 5px 14px rgba(0,0,0,.12); }
  .assistant { align-self:stretch; padding:10px 11px 11px; border:1px solid var(--vscode-panel-border, var(--vscode-input-border)); border-left:2px solid var(--vscode-textLink-foreground); border-radius:9px; background:color-mix(in srgb, var(--vscode-editorWidget-background) 42%, transparent); box-shadow:0 5px 16px rgba(0,0,0,.08); }
  .assistant .message-head { margin-left:0; color:var(--vscode-textLink-foreground); font-weight:600; }
  .assistant .message-head::before { background:var(--vscode-textLink-foreground); opacity:1; }
  .assistant .message-body { color:var(--vscode-foreground); }
  .message-copy { float:right; margin-top:7px; border:1px solid transparent; border-radius:5px; padding:3px 6px; }
  .message-copy:hover { border-color:var(--vscode-input-border); background:var(--vscode-toolbar-hoverBackground); }
  .next-step { clear:both; margin-top:11px; padding:9px 10px; border:1px solid color-mix(in srgb, var(--vscode-textLink-foreground) 38%, var(--vscode-panel-border)); border-radius:8px; background:color-mix(in srgb, var(--vscode-textLink-foreground) 7%, transparent); }
  .next-step-title { display:flex; align-items:center; gap:6px; color:var(--vscode-foreground); font-size:11px; font-weight:600; }
  .next-step-title::before { content:"↗"; display:grid; place-items:center; width:17px; height:17px; border-radius:5px; color:var(--vscode-button-foreground); background:var(--vscode-button-background); font-size:10px; }
  .next-step p { margin:5px 0 8px; color:var(--vscode-descriptionForeground); font-size:11px; line-height:1.45; }
  .next-step-actions { display:flex; flex-wrap:wrap; gap:5px; }
  .next-step button { border:1px solid var(--vscode-input-border); border-radius:5px; padding:5px 8px; color:var(--vscode-foreground); background:var(--vscode-button-secondaryBackground, transparent); cursor:pointer; font:11px var(--vscode-font-family); }
  .next-step button.primary { color:var(--vscode-button-foreground); border-color:var(--vscode-button-background); background:var(--vscode-button-background); }
  .next-step button:hover { border-color:var(--vscode-focusBorder); background:var(--vscode-button-secondaryHoverBackground, var(--vscode-toolbar-hoverBackground)); }
  .next-step button.primary:hover { background:var(--vscode-button-hoverBackground); }
  .status { margin:0 12px 7px; padding:6px 8px; border:1px solid var(--vscode-panel-border, var(--vscode-input-border)); border-radius:7px; background:color-mix(in srgb, var(--vscode-editorWidget-background) 55%, transparent); }
  footer { border-top:1px solid var(--vscode-sideBarSectionHeader-border, transparent); }
  form { gap:7px; padding:9px; border-radius:9px; box-shadow:0 6px 16px rgba(0,0,0,.10); transition:border-color .15s, box-shadow .15s; }
  form:focus-within { border-color:var(--vscode-focusBorder); box-shadow:0 0 0 2px color-mix(in srgb, var(--vscode-focusBorder) 18%, transparent); }
  .hint { opacity:.82; }
  html, body { width:100%; height:100%; overflow:hidden; }
  .shell { width:100%; height:100vh; height:100dvh; min-height:0; overflow:hidden; }
  header { flex:none; padding:11px 13px 10px; }
  .logo { width:29px; height:29px; border-radius:9px; box-shadow:0 3px 10px color-mix(in srgb, var(--vscode-button-background) 30%, transparent); }
  .context { flex:none; margin-top:9px; }
  .history-panel { flex:none; margin-top:8px; padding-top:7px; }
  .history-head { margin:0; }
  .history-tools { display:flex; align-items:center; gap:4px; }
  .history-toggle { border:0; padding:3px 6px; border-radius:4px; color:var(--vscode-descriptionForeground); background:transparent; cursor:pointer; font:10px var(--vscode-font-family); }
  .history-toggle:hover { color:var(--vscode-foreground); background:var(--vscode-toolbar-hoverBackground); }
  .history-new { padding:3px 6px; font-size:11px; }
  #history-list { margin-top:5px; max-height:116px; }
  #history-list[hidden], .status[hidden] { display:none; }
  .quick { display:none; flex:none; padding-top:7px; padding-bottom:2px; }
  .shell.has-messages .quick { display:flex; }
  #messages { min-height:0; overscroll-behavior:contain; scroll-behavior:smooth; }
  .empty { width:min(100%, 360px); margin:auto; padding:22px 15px 17px; border-style:solid; border-color:color-mix(in srgb, var(--vscode-textLink-foreground) 26%, var(--vscode-panel-border)); background:radial-gradient(ellipse at 50% 0%, color-mix(in srgb, var(--vscode-textLink-foreground) 9%, transparent), transparent 72%), color-mix(in srgb, var(--vscode-editorWidget-background) 48%, transparent); box-shadow:0 12px 36px rgba(0,0,0,.10); }
  .welcome-mark { display:grid; place-items:center; width:37px; height:37px; margin:0 auto 12px; border:1px solid color-mix(in srgb, var(--vscode-textLink-foreground) 32%, transparent); border-radius:12px; color:var(--vscode-textLink-foreground); background:color-mix(in srgb, var(--vscode-textLink-foreground) 10%, transparent); font-size:19px; }
  .empty strong { display:block; margin:0 0 5px; color:var(--vscode-foreground); font-size:15px; font-weight:650; letter-spacing:-.02em; }
  .empty-description { display:block; max-width:290px; margin:0 auto; color:var(--vscode-descriptionForeground); font-size:11px; line-height:1.5; }
  .empty-suggestions { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:7px; margin-top:17px; text-align:left; }
  .empty-action { display:flex; min-width:0; align-items:flex-start; gap:8px; padding:9px 8px; border:1px solid var(--vscode-panel-border, var(--vscode-input-border)); border-radius:8px; color:var(--vscode-foreground); background:color-mix(in srgb, var(--vscode-editorWidget-background) 62%, transparent); cursor:pointer; text-align:left; font:inherit; transition:border-color .15s, background .15s, transform .15s; }
  .empty-action:hover { transform:translateY(-1px); border-color:var(--vscode-focusBorder); background:var(--vscode-list-hoverBackground); }
  .empty-action:focus-visible, .history-toggle:focus-visible, .history-new:focus-visible, .quick button:focus-visible, .send:focus-visible { outline:2px solid var(--vscode-focusBorder); outline-offset:2px; }
  .empty-action-icon { display:grid; flex:none; place-items:center; width:22px; height:22px; border-radius:6px; color:var(--vscode-textLink-foreground); background:color-mix(in srgb, var(--vscode-textLink-foreground) 10%, transparent); font-size:12px; }
  .empty-action-copy { display:block; min-width:0; }
  .empty-action-title { display:block; margin:1px 0 2px; font-size:10px; font-weight:600; }
  .empty-action-note { display:block; color:var(--vscode-descriptionForeground); font-size:9px; line-height:1.35; }
  .message { animation:message-in .18s ease-out both; }
  @keyframes message-in { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:translateY(0); } }
  .activity-card { flex:none; overflow:hidden; border-radius:9px; background:color-mix(in srgb, var(--vscode-editorWidget-background) 70%, transparent); }
  .activity-card summary { display:flex; min-height:34px; align-items:center; gap:8px; padding:7px 10px; list-style:none; }
  .activity-card summary::-webkit-details-marker { display:none; }
  .activity-chevron { flex:none; color:var(--vscode-descriptionForeground); font-size:12px; transition:transform .15s; }
  .activity-card[open] .activity-chevron { transform:rotate(180deg); }
  .activity-state-icon { flex:none; width:8px; height:8px; border:2px solid currentColor; border-radius:50%; color:var(--vscode-charts-blue, #569cd6); }
  .activity-card.running .activity-state-icon { border:0; background:currentColor; box-shadow:0 0 0 3px color-mix(in srgb, currentColor 16%, transparent); animation:activity-pulse 1.5s ease-in-out infinite; }
  .activity-card.complete .activity-state-icon { color:var(--vscode-testing-iconPassed, #4ec9b0); }
  .activity-card.failed .activity-state-icon, .activity-card.blocked .activity-state-icon { color:var(--vscode-testing-iconFailed, #f48771); }
  @keyframes activity-pulse { 50% { box-shadow:0 0 0 5px color-mix(in srgb, currentColor 7%, transparent); opacity:.7; } }
  .activity-title { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .activity-count { flex:none; color:var(--vscode-descriptionForeground); font-size:9px; font-weight:400; }
  .activity-list { padding:1px 10px 8px 17px; }
  .activity-entry { gap:8px; padding:5px 0; }
  .activity-entry-dot { width:7px; height:7px; margin-top:3px; }
  .activity-entry.current .activity-entry-dot { box-shadow:0 0 0 3px color-mix(in srgb, var(--vscode-charts-blue, #569cd6) 15%, transparent); }
  footer { flex:none; padding-top:7px; }
  form { min-height:53px; }
  textarea { max-height:150px; resize:none; }
  .send { display:grid; flex:none; place-items:center; width:30px; height:30px; border-radius:8px; transition:transform .15s, background .15s; }
  .send:not(:disabled):hover { transform:translateY(-1px); }
  .shell.is-working .status-dot { animation:activity-pulse 1.5s ease-in-out infinite; }
  .status { flex:none; }
  @media (prefers-reduced-motion:reduce) { *, *::before, *::after { scroll-behavior:auto !important; animation-duration:.01ms !important; transition-duration:.01ms !important; } }
  @media (max-width:380px) {
    header { padding-left:10px; padding-right:10px; }
    .context, .quick, #messages, footer { margin-left:0; margin-right:0; padding-left:10px; padding-right:10px; }
    .quick { gap:4px; }
    .quick button { padding-left:8px; padding-right:8px; }
    .assistant { padding:9px 9px 10px; }
    .user { max-width:96%; }
    .hint { font-size:9px; }
    .empty { padding:18px 10px 13px; }
    .empty-suggestions { gap:5px; }
    .empty-action { gap:6px; padding:8px 6px; }
    .empty-action-note { font-size:8px; }
  }

  /* Layout da sidebar: uma rolagem principal, controles compactos e histórico flutuante. */
  body { font-size:12px; line-height:1.5; }
  .shell { background:var(--vscode-sideBar-background); }
  header { gap:9px; min-height:52px; padding:8px 12px; border-bottom:1px solid var(--vscode-panel-border, var(--vscode-input-border)); }
  .logo { width:25px; height:25px; flex:none; border-radius:7px; box-shadow:none; font-size:14px; }
  h1 { font-size:13px; line-height:1.2; }
  header .muted { margin-top:2px; font-size:10px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .header-state { margin-left:0; padding:2px 6px; border:0; font-size:10px; }
  .header-actions { gap:2px; }
  .header-actions .icon-button { width:25px; height:25px; padding:0; font-size:16px; }
  .context { min-height:30px; margin:8px 10px 0; padding:5px 8px; border-radius:6px; background:var(--vscode-editorWidget-background); }
  .context .link-button { width:24px; height:22px; padding:0; color:var(--vscode-descriptionForeground); font-size:16px; }
  .quick, .shell.has-messages .quick { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:5px; margin:0; padding:8px 10px 5px; overflow:visible; }
  .quick button { min-width:0; min-height:28px; padding:4px 7px; border:1px solid var(--vscode-panel-border, var(--vscode-input-border)); border-radius:6px; white-space:normal; text-align:left; font-size:11px; line-height:1.2; transition:background .12s,border-color .12s; }
  .quick button:hover { transform:none; }
  .history-panel { position:relative; z-index:10; min-height:31px; margin:2px 10px 0; padding:3px 0; border-top:1px solid var(--vscode-panel-border, var(--vscode-input-border)); }
  .history-head { min-height:24px; font-size:11px; font-weight:600; text-transform:none; letter-spacing:0; }
  .history-new { width:24px; height:24px; padding:0; font-size:16px; line-height:1; }
  .history-toggle { padding:3px 7px; font-size:11px; }
  #history-list { position:absolute; top:100%; left:0; right:0; z-index:20; max-height:min(42vh,300px); margin-top:2px; padding:5px; overflow:auto; border:1px solid var(--vscode-panel-border, var(--vscode-input-border)); border-radius:8px; background:var(--vscode-editorWidget-background); box-shadow:0 12px 25px rgba(0,0,0,.25); }
  .history-item { min-height:34px; padding:6px 8px; font-size:11px; }
  .history-item-title { -webkit-line-clamp:1; }
  #messages { gap:10px; padding:10px 12px 16px; scrollbar-gutter:auto; }
  .message { min-width:0; animation:none; }
  .message-head { margin-bottom:4px; font-size:10px; }
  .user { max-width:90%; padding:8px 10px; border:0; border-radius:9px 9px 3px 9px; box-shadow:none; }
  .assistant { padding:9px 10px 10px; border:1px solid var(--vscode-panel-border, var(--vscode-input-border)); border-left:2px solid var(--vscode-textLink-foreground); border-radius:7px; background:var(--vscode-editorWidget-background); box-shadow:none; }
  .assistant.needs-attention { border-left-color:var(--vscode-testing-iconFailed, #f48771); }
  .assistant.needs-attention .message-head { color:var(--vscode-testing-iconFailed, #f48771); }
  .message-body { min-width:0; overflow-wrap:anywhere; }
  .message-body p { margin:0 0 8px; line-height:1.5; }
  .message-body p:last-child { margin-bottom:0; }
  .message-body.is-collapsed { max-height:300px; overflow:hidden; mask-image:linear-gradient(to bottom,#000 82%,transparent); }
  .assistant.needs-attention .message-body.is-collapsed { max-height:150px; }
  .message-expand { display:block; width:100%; margin:4px 0 0; padding:6px; border:1px solid var(--vscode-input-border); border-radius:5px; color:var(--vscode-textLink-foreground); background:transparent; cursor:pointer; text-align:center; font:11px var(--vscode-font-family); }
  .message-expand:hover { background:var(--vscode-toolbar-hoverBackground); }
  .message-copy { float:none; display:block; margin:6px 0 0 auto; font-size:11px; }
  .activity-card { margin:0; border-radius:7px; background:var(--vscode-editorWidget-background); box-shadow:none; }
  .activity-card summary { min-height:30px; padding:6px 9px; }
  .activity-list { max-height:210px; padding:0 9px 8px 17px; overflow:auto; }
  .activity-entry { padding:4px 0; }
  .activity-entry > span:nth-child(2) { min-width:0; overflow-wrap:anywhere; }
  footer { padding:7px 10px 9px; border-top:1px solid var(--vscode-panel-border, var(--vscode-input-border)); }
  form { min-height:48px; padding:7px; border-radius:7px; box-shadow:none; }
  form:focus-within { box-shadow:0 0 0 1px var(--vscode-focusBorder); }
  textarea { min-height:30px; max-height:130px; }
  .hint { padding-top:5px; font-size:10px; line-height:1.25; }
  .status { margin:0 10px 6px; }
  @media (max-width:320px) { .header-state { display:none; } .quick button { font-size:10px; } .history-item-meta { display:none; } }

  /* A mesma assinatura Aurora em escala adequada à sidebar do VS Code. */
  :root { --ia-violet:#8171f4; --ia-cyan:#55d5d6; --ia-mint:#58dca6; }
  .shell {
    background:
      radial-gradient(circle at 3% 0%, color-mix(in srgb, var(--ia-cyan) 12%, transparent), transparent 30%),
      radial-gradient(circle at 100% 28%, color-mix(in srgb, var(--ia-violet) 10%, transparent), transparent 32%),
      var(--vscode-sideBar-background);
  }
  header {
    position:relative;
    min-height:62px;
    gap:11px;
    padding:10px 12px;
    border-bottom:1px solid color-mix(in srgb, var(--ia-violet) 16%, var(--vscode-panel-border, transparent));
    background:color-mix(in srgb, var(--vscode-editorWidget-background) 52%, transparent);
  }
  header::before {
    content:"";
    position:absolute;
    inset:0 0 auto;
    height:2px;
    background:linear-gradient(90deg, transparent, var(--ia-cyan) 28%, var(--ia-violet) 72%, transparent);
    opacity:.8;
  }
  .logo {
    position:relative;
    width:32px;
    height:32px;
    border:1px solid color-mix(in srgb, white 35%, transparent);
    border-radius:11px;
    color:#fff;
    background:linear-gradient(145deg, #57d7de, #7666eb 78%);
    box-shadow:0 0 0 4px color-mix(in srgb, var(--ia-cyan) 8%, transparent), 0 7px 19px color-mix(in srgb, var(--ia-violet) 28%, transparent);
    font-size:17px;
  }
  h1 { font-size:14px; font-weight:700; letter-spacing:-.025em; }
  header .muted { margin-top:2px; font-size:10px; opacity:.9; }
  .header-state {
    gap:6px;
    padding:4px 7px;
    border:1px solid color-mix(in srgb, var(--ia-mint) 26%, transparent);
    border-radius:999px;
    background:color-mix(in srgb, var(--ia-mint) 8%, transparent);
    color:var(--vscode-foreground);
    font-size:9px;
  }
  .header-state::before { background:var(--ia-mint); box-shadow:0 0 8px color-mix(in srgb, var(--ia-mint) 70%, transparent); }
  .header-actions .icon-button { width:28px; height:28px; border-radius:8px; transition:background .18s, transform .18s; }
  .header-actions .icon-button:hover { transform:translateY(-1px); background:color-mix(in srgb, var(--ia-violet) 15%, var(--vscode-toolbar-hoverBackground)); }
  .context {
    min-height:34px;
    margin:10px 10px 0;
    padding:7px 10px;
    border:1px solid color-mix(in srgb, var(--ia-cyan) 18%, var(--vscode-panel-border, transparent));
    border-radius:10px;
    background:color-mix(in srgb, var(--vscode-editorWidget-background) 83%, transparent);
    box-shadow:inset 0 1px color-mix(in srgb, white 5%, transparent);
  }
  .context-dot { width:7px; height:7px; background:var(--ia-mint); box-shadow:0 0 0 3px color-mix(in srgb, var(--ia-mint) 10%, transparent); }
  .context-label { color:var(--vscode-foreground); opacity:.86; font-size:10px; }
  .context .link-button { border-radius:6px; }
  .quick, .shell.has-messages .quick { gap:6px; padding:9px 10px 7px; }
  .quick button {
    min-height:32px;
    padding:6px 9px;
    border:1px solid color-mix(in srgb, var(--ia-violet) 18%, var(--vscode-panel-border, transparent));
    border-radius:9px;
    background:color-mix(in srgb, var(--vscode-editorWidget-background) 72%, transparent);
    color:var(--vscode-foreground);
    font-weight:600;
    transition:transform .16s, border-color .16s, background .16s, box-shadow .16s;
  }
  .quick button:hover {
    transform:translateY(-2px);
    border-color:color-mix(in srgb, var(--ia-cyan) 48%, var(--vscode-focusBorder));
    background:color-mix(in srgb, var(--ia-violet) 10%, var(--vscode-editorWidget-background));
    box-shadow:0 7px 17px rgba(0,0,0,.1);
  }
  .history-panel { margin-top:3px; padding-top:5px; border-top-color:color-mix(in srgb, var(--ia-violet) 15%, var(--vscode-panel-border, transparent)); }
  #history-list {
    border-radius:11px;
    border-color:color-mix(in srgb, var(--ia-violet) 25%, var(--vscode-panel-border, transparent));
    box-shadow:0 18px 35px rgba(0,0,0,.23);
  }
  .history-item { border-radius:8px; transition:background .15s, border-color .15s; }
  .history-item.active {
    border-color:color-mix(in srgb, var(--ia-violet) 48%, transparent);
    background:color-mix(in srgb, var(--ia-violet) 14%, var(--vscode-list-activeSelectionBackground));
    color:var(--vscode-foreground);
  }
  #messages { gap:12px; padding:13px 12px 18px; }
  .message { animation:ia-message-rise .22s ease-out both; }
  @keyframes ia-message-rise { from { opacity:.3; transform:translateY(7px); } to { opacity:1; transform:translateY(0); } }
  .user {
    max-width:92%;
    padding:10px 12px;
    border:1px solid rgba(255,255,255,.22);
    border-radius:13px 13px 4px 13px;
    color:#fff;
    background:linear-gradient(130deg, #326f9a, #416aa7 48%, #665dc0);
    box-shadow:0 9px 21px rgba(26, 65, 112, .2), inset 0 1px rgba(255,255,255,.16);
  }
  .user .message-head { color:rgba(255,255,255,.76); }
  .user .message-head::before { background:#b3f5ed; }
  .user .message-body, .user .message-body strong { color:#fff; }
  .user .message-body code { color:#fff; background:rgba(255,255,255,.16); }
  .assistant {
    padding:12px 12px 11px;
    border:1px solid color-mix(in srgb, var(--ia-violet) 23%, var(--vscode-panel-border, transparent));
    border-left:3px solid var(--ia-cyan);
    border-radius:12px;
    background:linear-gradient(135deg,
      color-mix(in srgb, var(--ia-cyan) 6%, var(--vscode-editorWidget-background)),
      color-mix(in srgb, var(--ia-violet) 5%, var(--vscode-editorWidget-background)));
    box-shadow:0 9px 24px rgba(0,0,0,.11), inset 0 1px color-mix(in srgb, white 8%, transparent);
  }
  .assistant .message-head { color:var(--vscode-foreground); font-weight:700; }
  .assistant .message-head::before { width:7px; height:7px; background:var(--ia-cyan); box-shadow:0 0 0 3px color-mix(in srgb, var(--ia-cyan) 14%, transparent); }
  .assistant.needs-attention { border-left-color:var(--vscode-testing-iconFailed, #f48771); }
  .message-body { font-size:12px; line-height:1.58; }
  .message-body h1, .message-body h2, .message-body h3 { letter-spacing:-.02em; }
  .message-copy { margin-top:9px; border-radius:7px; }
  .message-copy:hover { border-color:color-mix(in srgb, var(--ia-violet) 34%, transparent); }
  .message-expand { border-radius:8px; }
  .empty {
    position:relative;
    width:min(100%, 390px);
    padding:25px 15px 18px;
    border:1px solid color-mix(in srgb, var(--ia-violet) 30%, var(--vscode-panel-border, transparent));
    border-radius:17px;
    background:
      radial-gradient(circle at 50% 0%, color-mix(in srgb, var(--ia-cyan) 17%, transparent), transparent 58%),
      radial-gradient(circle at 90% 84%, color-mix(in srgb, var(--ia-violet) 10%, transparent), transparent 45%),
      color-mix(in srgb, var(--vscode-editorWidget-background) 72%, transparent);
    box-shadow:0 15px 40px rgba(0,0,0,.13), inset 0 1px color-mix(in srgb, white 8%, transparent);
  }
  .welcome-mark {
    width:48px;
    height:48px;
    margin-bottom:16px;
    border:1px solid color-mix(in srgb, var(--ia-cyan) 55%, transparent);
    border-radius:16px;
    color:#fff;
    background:linear-gradient(140deg, #54d4da, #7766eb);
    box-shadow:0 0 0 5px color-mix(in srgb, var(--ia-cyan) 9%, transparent), 0 10px 24px color-mix(in srgb, var(--ia-violet) 25%, transparent);
    font-size:24px;
  }
  .empty strong { font-size:17px; line-height:1.2; letter-spacing:-.035em; }
  .empty-description { max-width:290px; font-size:11px; line-height:1.55; }
  .empty-suggestions { gap:7px; margin-top:20px; }
  .empty-action {
    min-height:60px;
    padding:9px;
    border:1px solid color-mix(in srgb, var(--ia-violet) 18%, var(--vscode-panel-border, transparent));
    border-radius:10px;
    background:color-mix(in srgb, var(--vscode-editorWidget-background) 81%, transparent);
    transition:transform .18s, border-color .18s, background .18s, box-shadow .18s;
  }
  .empty-action:hover {
    transform:translateY(-2px);
    border-color:color-mix(in srgb, var(--ia-cyan) 52%, var(--vscode-focusBorder));
    background:color-mix(in srgb, var(--ia-cyan) 8%, var(--vscode-editorWidget-background));
    box-shadow:0 8px 17px rgba(0,0,0,.11);
  }
  .empty-action-icon { width:26px; height:26px; border-radius:8px; color:var(--ia-cyan); background:color-mix(in srgb, var(--ia-cyan) 14%, transparent); }
  .empty-action-title { font-size:11px; }
  .empty-action-note { font-size:9px; }

  .activity-card {
    border:1px solid color-mix(in srgb, var(--ia-violet) 26%, var(--vscode-panel-border, transparent));
    border-radius:11px;
    background:linear-gradient(135deg,
      color-mix(in srgb, var(--ia-violet) 7%, var(--vscode-editorWidget-background)),
      var(--vscode-editorWidget-background));
    box-shadow:0 7px 22px rgba(0,0,0,.09);
  }
  .activity-card.running { border-color:color-mix(in srgb, var(--ia-violet) 50%, var(--vscode-panel-border, transparent)); }
  .activity-card summary { position:relative; min-height:38px; padding:8px 10px; }
  .activity-card.running summary::after {
    content:"";
    position:absolute;
    inset:auto 0 0;
    height:2px;
    background:linear-gradient(90deg, transparent, var(--ia-cyan), var(--ia-violet), transparent);
    background-size:220% 100%;
    animation:ia-activity-sweep 2.3s linear infinite;
  }
  @keyframes ia-activity-sweep { to { background-position:-220% 0; } }
  .activity-card.running .activity-state-icon { color:var(--ia-violet); }
  .activity-card.complete .activity-state-icon { color:var(--ia-mint); }
  .activity-title { color:var(--vscode-foreground); font-weight:650; }
  .activity-list { margin:0 10px 8px 13px; padding:3px 0 0 14px; border-left:1px solid color-mix(in srgb, var(--ia-violet) 22%, var(--vscode-panel-border, transparent)); }
  .activity-entry { position:relative; padding:5px 0; font-size:10px; }
  .activity-entry-dot { margin-left:-18px; margin-right:4px; background:var(--ia-violet); }
  .activity-entry.complete .activity-entry-dot { background:var(--ia-mint); }
  .activity-entry.current .activity-entry-dot { box-shadow:0 0 0 4px color-mix(in srgb, var(--ia-violet) 14%, transparent); }
  .status { border-radius:9px; }
  footer {
    padding:9px 10px 10px;
    border-top:1px solid color-mix(in srgb, var(--ia-violet) 16%, var(--vscode-panel-border, transparent));
    background:color-mix(in srgb, var(--vscode-sideBar-background) 88%, var(--vscode-editorWidget-background));
  }
  form {
    min-height:54px;
    padding:8px;
    border:1px solid color-mix(in srgb, var(--ia-violet) 25%, var(--vscode-input-border, transparent));
    border-radius:13px;
    background:var(--vscode-input-background);
    box-shadow:0 10px 24px rgba(0,0,0,.13);
    transition:border-color .18s, box-shadow .18s, transform .18s;
  }
  form:focus-within {
    transform:translateY(-1px);
    border-color:var(--ia-violet);
    box-shadow:0 12px 28px rgba(0,0,0,.17), 0 0 0 3px color-mix(in srgb, var(--ia-violet) 16%, transparent);
  }
  textarea { min-height:34px; padding:5px 4px; line-height:1.45; }
  .send {
    width:34px;
    height:34px;
    border-radius:10px;
    color:#fff;
    background:linear-gradient(135deg, #58bfcf, #655fdb);
    box-shadow:0 5px 12px color-mix(in srgb, var(--ia-violet) 25%, transparent);
  }
  .send:not(:disabled):hover { transform:translateY(-2px); background:linear-gradient(135deg, #55d7d9, #7868ed); }
  .hint { opacity:.8; }
  @media (max-width:380px) {
    header { padding-left:10px; padding-right:10px; }
    #messages { padding-left:10px; padding-right:10px; }
    .empty { padding:21px 11px 15px; }
    .empty-suggestions { gap:6px; }
    .empty-action { min-height:57px; padding:7px; }
  }
  @media (prefers-reduced-motion:reduce) {
    .message, .activity-card.running summary::after { animation:none !important; }
    form, .quick button, .empty-action, .header-actions .icon-button { transition:none !important; }
  }

  /* Brasa: identidade própria, com o mesmo calor da interface principal. */
  :root { --brasa-ink:#17201c; --brasa-ember:#ed744b; --brasa-cream:#fff6e8; --brasa-muted:#a6b3a4; }
  body { color:var(--brasa-cream); background:#171e1a; }
  .shell { color:var(--brasa-cream); background:#171e1a; }
  header {
    min-height:72px; padding:12px 14px; gap:11px;
    border-bottom:1px solid #39463b; background:#202a22;
  }
  header::before { height:3px; opacity:1; background:var(--brasa-ember); }
  .logo {
    width:40px; height:40px; flex:0 0 40px;
    border:1px solid #ffae82; border-radius:12px 12px 12px 3px;
    color:#1d241e; background:var(--brasa-ember);
    box-shadow:0 8px 17px rgba(237,116,75,.19);
    font:italic 700 31px/1 Georgia,serif;
  }
  h1 { color:var(--brasa-cream); font:600 22px/1 Georgia,serif; letter-spacing:-.055em; }
  header .muted { margin-top:5px; color:#c0bcae; font-size:9px; letter-spacing:.04em; }
  .header-state {
    padding:4px 7px; border:1px solid #465b42; border-radius:999px;
    background:#2b3b2e; color:#d9e8c8; font-size:9px;
  }
  .header-state::before { background:#bce39d; box-shadow:0 0 7px rgba(188,227,157,.35); }
  .header-actions .icon-button { width:27px; height:27px; color:#efe8d9; }
  .header-actions .icon-button:hover { background:#354135; color:#ffad86; }
  .context {
    min-height:36px; margin:11px 11px 0; padding:7px 9px;
    border:1px solid #37463a; border-radius:8px; background:#253127; box-shadow:none;
  }
  .context-dot { background:var(--brasa-ember); box-shadow:0 0 0 3px rgba(237,116,75,.12); }
  .context-label { color:#e8e7d7; opacity:1; }
  .context .link-button { color:#e5ad8b; }
  .shell:not(.has-messages) .quick { display:none; }
  .shell.has-messages .quick { display:grid; gap:6px; padding:10px 11px 7px; }
  .quick button {
    min-height:32px; padding:6px 9px; border:1px solid #3c4a3f; border-radius:7px;
    background:#253129; color:#dbe2d4; font-weight:550;
  }
  .quick button:hover { border-color:#ed744b; background:#384037; color:#fff7e9; box-shadow:none; }
  .history-panel { margin:8px 11px 0; border-top-color:#354238; }
  .history-head { color:#b5bba9; }
  .history-toggle, .history-new { color:#e9a17e; }
  #history-list { border-color:#485246; background:#253027; box-shadow:0 13px 25px rgba(0,0,0,.22); }
  .history-item { color:#e4e8d9; }
  .history-item:hover { background:#344033; }
  .history-item.active { border-color:#e1784f; background:#4a3c2d; color:#fff7e8; }
  #messages {
    gap:11px; padding:16px 12px 20px;
    background:radial-gradient(circle at 86% 17%, rgba(237,116,75,.08), transparent 36%), #171e1a;
  }
  .empty {
    position:relative; width:min(100%,480px); margin:auto 8px; padding:26px 24px 22px;
    border:1px solid #f58e65; border-radius:15px;
    background:radial-gradient(circle at 95% 6%, rgba(255,223,179,.4), transparent 33%), var(--brasa-ember);
    color:#232820; text-align:left; box-shadow:7px 8px 0 #374438, 0 24px 42px rgba(0,0,0,.2);
    overflow:hidden;
  }
  .empty::before {
    content:""; position:absolute; width:280px; height:280px; top:-140px; right:-80px;
    border:1px solid rgba(42,43,29,.27); border-radius:50%;
    box-shadow:0 0 0 42px rgba(42,43,29,.06); pointer-events:none;
  }
  .empty > * { position:relative; z-index:1; }
  .empty-kicker { font-size:9px; font-weight:800; letter-spacing:.18em; }
  .welcome-mark {
    display:grid; place-items:center; width:62px; height:62px; margin:21px 0 23px;
    border:1px solid #384139; border-radius:17px 17px 17px 4px;
    background:#253027; color:#ffac83; box-shadow:none;
    font:italic 700 48px/1 Georgia,serif;
  }
  .empty strong {
    margin:0 0 14px; color:#202a22;
    font:600 clamp(28px, 7vw, 42px)/.98 Georgia,serif; letter-spacing:-.06em;
  }
  .empty-description { max-width:350px; margin:0; color:#483b2b; font-size:11px; line-height:1.55; }
  .empty-suggestions { gap:8px; margin-top:25px; }
  .empty-action {
    min-height:65px; padding:10px 9px; border:1px solid rgba(41,43,30,.28); border-radius:8px;
    background:rgba(255,248,230,.86); color:#233026; box-shadow:0 3px 0 rgba(62,42,30,.14);
  }
  .empty-action:hover { transform:translateY(-2px); border-color:#30382e; background:#fff8e8; box-shadow:0 5px 0 rgba(62,42,30,.16); }
  .empty-action-icon { color:#ffab84; background:#263229; }
  .empty-action-title { color:#263027; font-size:11px; }
  .empty-action-note { color:#657164; font-size:9px; }

  .message { animation:ia-message-rise .23s ease-out both; }
  .user {
    max-width:94%; padding:12px 14px; border:1px solid #e8ad8c; border-right:4px solid var(--brasa-ember);
    border-radius:11px 11px 3px 11px; background:#fff2df; color:#17211b;
    box-shadow:0 9px 20px rgba(0,0,0,.18);
  }
  .user .message-head { color:#783b29; font-weight:750; }
  .user .message-head::before { background:var(--brasa-ember); }
  .user .message-body { color:#17211b; font-size:13px; font-weight:500; line-height:1.68; letter-spacing:.005em; }
  .user .message-body strong { color:#101912; font-weight:750; }
  .user .message-body a { color:#87371f; text-decoration:underline; }
  .user .message-body code { color:#642c1d; background:#f8dac4; }
  .assistant {
    padding:12px 13px; border:1px solid #405043; border-left:3px solid var(--brasa-ember);
    border-radius:3px 11px 11px 11px;
    background:#263229; color:#f0eedf; box-shadow:0 8px 17px rgba(0,0,0,.11);
  }
  .assistant .message-head { color:#e79b77; }
  .assistant .message-head::before { background:var(--brasa-ember); box-shadow:0 0 0 3px rgba(237,116,75,.14); }
  .assistant .message-body, .assistant .message-body p, .assistant .message-body strong { color:#f0eedf; }
  .assistant .message-body h1, .assistant .message-body h2, .assistant .message-body h3 { color:#fff7ea; }
  .assistant .message-body a { color:#ffae84; }
  .assistant .message-body code { background:#354237; color:#ffbea0; }
  .assistant .message-body blockquote { border-left-color:#ed744b; color:#d2d8c6; background:#303e32; }
  .message-copy, .message-expand { color:#e6ad8c; }
  .message-expand { border-color:#4d5a4b; background:#2d3a30; }
  .activity-card {
    border:1px solid #485446; border-radius:9px; background:#242f27; box-shadow:none;
  }
  .activity-card.running { border-color:#ad7054; }
  .activity-card summary { color:#bdc8b7; }
  .activity-card.running summary { color:#ffad83; }
  .activity-card.complete summary { color:#bfdca8; }
  .activity-card.running summary::after { background:linear-gradient(90deg, transparent, #ed744b, #ffc197, transparent); }
  .activity-card.running .activity-state-icon { color:#ed744b; }
  .activity-card.complete .activity-state-icon { color:#b9d99e; }
  .activity-title { color:#e8ecdb; }
  .activity-count, .activity-entry, .activity-entry-time { color:#b9c3b2; }
  .activity-list { border-left-color:#5b5c46; }
  .activity-entry-dot { background:#ed744b; }
  .activity-entry.complete .activity-entry-dot { background:#b9d99e; }
  .status { color:#c9cdbc; }
  .status-dot { background:#ed744b; }
  footer { padding:10px 11px 11px; border-top-color:#39483b; background:#202a22; }
  form {
    min-height:57px; padding:8px; border:1px solid #637361; border-radius:10px;
    background:#29352b; box-shadow:none;
  }
  form:focus-within { border-color:#ed744b; box-shadow:0 0 0 3px rgba(237,116,75,.18); }
  textarea { min-height:35px; color:#fff8e8; }
  textarea::placeholder { color:#a7b2a5; }
  .send { width:34px; height:34px; border-radius:8px; background:#ed744b; color:#20271f; box-shadow:none; }
  .send:not(:disabled):hover { background:#ff9368; }
  .hint { color:#9caa9d; }
  @media (max-width:380px) {
    header { padding:10px; }
    .empty { margin:auto 2px; padding:21px 15px 17px; }
    .empty strong { font-size:30px; }
    .empty-suggestions { gap:6px; }
    .empty-action { min-height:58px; padding:8px 6px; }
  }
  @media (prefers-reduced-motion:reduce) {
    .message, .activity-card.running summary::after { animation:none !important; }
  }
</style>
</head>
<body>
<div class="shell">
  <header><div class="logo" aria-hidden="true">b</div><div class="grow"><h1>Brasa</h1><div class="muted">Inteligência local no editor</div></div><div class="header-state" title="Runtime local">Local</div><div class="header-actions"><button class="icon-button" id="new" type="button" title="Nova conversa" aria-label="Nova conversa">＋</button><button class="icon-button" id="open" type="button" title="Abrir aplicativo" aria-label="Abrir aplicativo">↗</button></div></header>
  <div class="context"><span class="context-dot"></span><span class="context-label" id="context">Carregando workspace…</span><button class="link-button" id="refresh" type="button" title="Atualizar contexto" aria-label="Atualizar contexto">↻</button></div>
  <div class="quick"><button data-prompt="Revise o arquivo ativo procurando bugs, riscos e melhorias priorizadas.">⌕ Revisar</button><button data-prompt="Explique a seleção ou o arquivo ativo e indique como testá-lo.">ⓘ Explicar</button><button data-prompt="Projete testes úteis para o código ativo e explique como executá-los.">✓ Testes</button><button data-prompt="Inspecione o workspace e descreva os próximos passos necessários.">⌘ Projeto</button></div>
  <section class="history-panel" aria-label="Conversas anteriores"><div class="history-head"><span id="history-label">Conversas</span><div class="history-tools"><button class="history-toggle" id="history-toggle" type="button" aria-expanded="false" aria-controls="history-list">Mostrar</button><button class="history-new" id="history-new" type="button" title="Nova conversa" aria-label="Nova conversa">＋</button></div></div><div id="history-list" hidden><div class="history-empty">Nenhuma conversa salva neste workspace.</div></div></section>
  <div id="messages" role="log" aria-live="polite" aria-relevant="additions"></div>
  <template id="empty-template"><div class="empty"><div class="empty-kicker">BRASA / ESTÚDIO</div><div class="welcome-mark" aria-hidden="true">b</div><strong class="empty-title">Uma ideia pode<br>mudar tudo.</strong><span class="empty-description">Diga o que deseja criar, entender ou melhorar. Começamos pelo contexto do seu editor.</span><div class="empty-suggestions"><button class="empty-action" type="button" data-prompt="Revise o arquivo ativo procurando bugs, riscos e melhorias priorizadas."><span class="empty-action-icon" aria-hidden="true">⌕</span><span class="empty-action-copy"><span class="empty-action-title">Revisar arquivo</span><span class="empty-action-note">Encontre problemas e melhorias</span></span></button><button class="empty-action" type="button" data-prompt="Explique a seleção ou o arquivo ativo e indique como testá-lo."><span class="empty-action-icon" aria-hidden="true">ⓘ</span><span class="empty-action-copy"><span class="empty-action-title">Entender código</span><span class="empty-action-note">Explore como uma parte funciona</span></span></button><button class="empty-action" type="button" data-prompt="Projete testes úteis para o código ativo e explique como executá-los."><span class="empty-action-icon" aria-hidden="true">✓</span><span class="empty-action-copy"><span class="empty-action-title">Criar testes</span><span class="empty-action-note">Planeje verificações úteis</span></span></button><button class="empty-action" type="button" data-prompt="Inspecione o workspace e descreva os próximos passos necessários."><span class="empty-action-icon" aria-hidden="true">⌘</span><span class="empty-action-copy"><span class="empty-action-title">Explorar projeto</span><span class="empty-action-note">Mapeie estrutura e próximos passos</span></span></button></div></div></template>
  <div class="status" id="status" role="status" aria-live="polite" hidden><span class="status-dot"></span><span id="status-text"></span></div>
  <footer><form id="form"><textarea id="input" rows="1" placeholder="Dê forma à próxima ideia…" aria-label="Mensagem para Brasa"></textarea><button class="send" id="send" type="submit" title="Enviar" aria-label="Enviar mensagem">➤</button></form><div class="hint">Ctrl/Cmd + Enter para enviar · o contexto do editor ativo é incluído automaticamente</div></footer>
</div>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  const messages = document.getElementById('messages');
  const input = document.getElementById('input');
  const send = document.getElementById('send');
  const status = document.getElementById('status');
  const statusText = document.getElementById('status-text');
  const historyList = document.getElementById('history-list');
  const historyToggle = document.getElementById('history-toggle');
  const shell = document.querySelector('.shell');
  const emptyTemplate = document.getElementById('empty-template');
  let activityCard = null;
  let activityList = null;
  let activityTitleNode = null;
  let activityCountNode = null;
  const markdownFence = String.fromCharCode(96).repeat(3);
  const setStatus = (text) => { statusText.textContent = text || ''; status.hidden = !text; };
  const atBottom = () => messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80;
  const scrollToBottom = () => requestAnimationFrame(() => { messages.scrollTop = messages.scrollHeight; });
  const renderEmpty = (title, description) => {
    const view = emptyTemplate.content.cloneNode(true);
    if (title) view.querySelector('.empty-title').textContent = title;
    if (description) view.querySelector('.empty-description').textContent = description;
    messages.replaceChildren(view);
    shell.classList.remove('has-messages');
  };
  renderEmpty();
  const appendInline = (parent, value) => {
    const pattern = /(\\*\\*[^*]+\\*\\*|\\*[^*]+\\*|\\[[^\\]]+\\]\\((?:https?:\\/\\/)[^)\\s]+\\))/g;
    let cursor = 0;
    const appendPlain = (chunk) => {
      const codeParts = String(chunk).split(String.fromCharCode(96));
      codeParts.forEach((part, index) => {
        if (!part) return;
        if (index % 2 === 1) { const code = document.createElement('code'); code.textContent = part; parent.appendChild(code); }
        else parent.appendChild(document.createTextNode(part));
      });
    };
    for (const match of String(value || '').matchAll(pattern)) {
      appendPlain(String(value || '').slice(cursor, match.index));
      const token = match[0];
      if (token.startsWith('**')) { const strong = document.createElement('strong'); strong.textContent = token.slice(2, -2); parent.appendChild(strong); }
      else if (token.startsWith('*')) { const em = document.createElement('em'); em.textContent = token.slice(1, -1); parent.appendChild(em); }
      else {
        const link = token.match(/^\\[([^\\]]+)\\]\\((https?:\\/\\/[^)\\s]+)\\)$/i);
        if (link) { const anchor = document.createElement('a'); anchor.textContent = link[1]; anchor.href = link[2]; anchor.target = '_blank'; anchor.rel = 'noreferrer'; parent.appendChild(anchor); }
        else appendPlain(token);
      }
      cursor = match.index + token.length;
    }
    appendPlain(String(value || '').slice(cursor));
  };
  const renderMarkdown = (text, body) => {
    body.replaceChildren();
    const lines = String(text || '').replace(/\\r/g, '').split('\\n');
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (line.startsWith(markdownFence)) {
        const language = line.slice(markdownFence.length).trim() || 'código';
        const codeLines = []; index += 1;
        while (index < lines.length && !lines[index].startsWith(markdownFence)) { codeLines.push(lines[index]); index += 1; }
        if (index < lines.length) index += 1;
        const wrap = document.createElement('div'); wrap.className = 'md-code';
        const head = document.createElement('div'); head.className = 'md-code-head';
        const label = document.createElement('span'); label.textContent = language;
        const copy = document.createElement('button'); copy.className = 'md-code-copy'; copy.type = 'button'; copy.textContent = 'Copiar';
        const raw = codeLines.join('\\n'); copy.addEventListener('click', () => navigator.clipboard?.writeText(raw).then(() => { copy.textContent = 'Copiado'; setTimeout(() => { copy.textContent = 'Copiar'; }, 1200); }));
        head.append(label, copy); const pre = document.createElement('pre'); pre.textContent = raw + (raw ? '\\n' : ''); wrap.append(head, pre); body.appendChild(wrap); continue;
      }
      const heading = line.match(/^(#{1,3})\\s+(.+)$/);
      if (heading) { const element = document.createElement('h' + heading[1].length); appendInline(element, heading[2]); body.appendChild(element); index += 1; continue; }
      if (/^\\s*[-*+]\\s+/.test(line)) {
        const list = document.createElement('ul');
        while (index < lines.length && /^\\s*[-*+]\\s+/.test(lines[index])) { const item = document.createElement('li'); appendInline(item, lines[index].replace(/^\\s*[-*+]\\s+/, '')); list.appendChild(item); index += 1; }
        body.appendChild(list); continue;
      }
      if (/^\\s*\\d+[.)]\\s+/.test(line)) {
        const list = document.createElement('ol');
        while (index < lines.length && /^\\s*\\d+[.)]\\s+/.test(lines[index])) { const item = document.createElement('li'); appendInline(item, lines[index].replace(/^\\s*\\d+[.)]\\s+/, '')); list.appendChild(item); index += 1; }
        body.appendChild(list); continue;
      }
      if (/^>\\s?/.test(line)) { const quote = document.createElement('blockquote'); appendInline(quote, line.replace(/^>\\s?/, '')); body.appendChild(quote); index += 1; continue; }
      if (!line.trim()) { index += 1; continue; }
      const paragraph = [];
      while (index < lines.length && lines[index].trim() && !lines[index].startsWith(markdownFence) && !/^(#{1,3})\\s+/.test(lines[index]) && !/^\\s*[-*+]\\s+/.test(lines[index]) && !/^\\s*\\d+[.)]\\s+/.test(lines[index]) && !/^>\\s?/.test(lines[index])) { paragraph.push(lines[index]); index += 1; }
      const element = document.createElement('p'); appendInline(element, paragraph.join('\\n')); body.appendChild(element);
    }
  };
  const addNextStep = (el, text) => {
    const pending = /(evidência suficiente|tarefa (?:permanece|continua) pendente|preciso de uma fonte|não consegui concluir|modelo local ainda não conseguiu|limite de .* etapas)/i.test(String(text || ""));
    if (!pending) return;
    const card = document.createElement("div"); card.className = "next-step";
    const title = document.createElement("div"); title.className = "next-step-title"; title.textContent = "Próximo passo";
    const note = document.createElement("p"); note.textContent = "Retomo a tarefa pelos arquivos e ferramentas do workspace.";
    const actions = document.createElement("div"); actions.className = "next-step-actions";
    const continueButton = document.createElement("button"); continueButton.className = "primary"; continueButton.type = "button"; continueButton.textContent = "Retomar tarefa";
    continueButton.addEventListener("click", () => submit("Retome o objetivo original. Use as ferramentas do workspace para obter evidências e execute a próxima ação concreta; consulte a web apenas se o pedido depender de informação externa."));
    const focusButton = document.createElement("button"); focusButton.type = "button"; focusButton.textContent = "Dar contexto";
    focusButton.addEventListener("click", () => { input.focus(); input.placeholder = "Acrescente um arquivo, fonte ou decisão…"; setStatus("Aguardando contexto adicional"); });
    actions.append(continueButton, focusButton); card.append(title, note, actions); el.appendChild(card);
  };
  const add = (kind, text, state) => {
    const stickToBottom = atBottom();
    const needsAttention = kind === 'assistant' && (state === 'blocked' || state === 'failed' || /modelo local ainda não conseguiu|tarefa (?:permanece|continua) pendente/i.test(String(text || '')));
    const el = document.createElement('div'); el.className = 'message ' + kind + (needsAttention ? ' needs-attention' : '');
    const head = document.createElement('div'); head.className = 'message-head';
    head.textContent = kind === 'user' ? 'Você' : needsAttention ? 'Brasa · não concluído' : 'Brasa';
    el.appendChild(head);
    const body = document.createElement('div'); body.className = 'message-body';
    renderMarkdown(text, body);
    el.appendChild(body);
    if (kind === 'assistant' && String(text || '').length > 900) {
      body.classList.add('is-collapsed');
      const expand = document.createElement('button'); expand.type = 'button'; expand.className = 'message-expand';
      expand.textContent = 'Mostrar resposta completa'; expand.setAttribute('aria-expanded', 'false');
      expand.addEventListener('click', () => {
        const collapsed = body.classList.toggle('is-collapsed');
        expand.textContent = collapsed ? 'Mostrar resposta completa' : 'Recolher resposta';
        expand.setAttribute('aria-expanded', String(!collapsed));
      });
      el.appendChild(expand);
    }
    if (kind === 'assistant') { const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'message-copy'; copy.textContent = 'Copiar'; copy.addEventListener('click', () => navigator.clipboard?.writeText(String(text || ''))); el.appendChild(copy); }
    if (kind === 'assistant') addNextStep(el, text);
    messages.appendChild(el); shell.classList.add('has-messages'); if (stickToBottom) scrollToBottom();
  };
  const clearActivity = () => { if (activityCard) activityCard.remove(); activityCard = null; activityList = null; activityTitleNode = null; activityCountNode = null; };
  const activityTitle = (state) => ({running:'Atividade · executando', complete:'Atividade · concluída', failed:'Atividade · falhou', blocked:'Atividade · pausada'})[state] || 'Atividade da tarefa';
  const ensureActivity = () => {
    clearEmpty();
    if (activityCard) return activityCard;
    activityCard = document.createElement('details'); activityCard.className = 'activity-card running'; activityCard.open = true;
    const summary = document.createElement('summary');
    const icon = document.createElement('span'); icon.className = 'activity-state-icon'; icon.setAttribute('aria-hidden', 'true');
    activityTitleNode = document.createElement('span'); activityTitleNode.className = 'activity-title'; activityTitleNode.textContent = 'Atividade · executando';
    activityCountNode = document.createElement('span'); activityCountNode.className = 'activity-count';
    const chevron = document.createElement('span'); chevron.className = 'activity-chevron'; chevron.textContent = '⌄'; chevron.setAttribute('aria-hidden', 'true');
    summary.append(icon, activityTitleNode, activityCountNode, chevron);
    activityList = document.createElement('div'); activityList.className = 'activity-list'; activityList.setAttribute('role', 'log'); activityList.setAttribute('aria-live', 'polite'); activityList.setAttribute('aria-relevant', 'additions');
    activityCard.append(summary, activityList); messages.appendChild(activityCard);
    return activityCard;
  };
  const addActivity = (entry) => {
    if (!entry?.text) return;
    const stickToBottom = atBottom();
    const card = ensureActivity();
    const state = ['running', 'complete', 'failed', 'blocked'].includes(entry.state) ? entry.state : 'running';
    card.className = 'activity-card ' + state;
    activityTitleNode.textContent = activityTitle(state);
    const previous = activityList.querySelector('.activity-entry.current');
    if (previous) { previous.classList.remove('current'); previous.removeAttribute('aria-current'); }
    const row = document.createElement('div'); row.className = 'activity-entry ' + state + ' current';
    if (state === 'running') row.setAttribute('aria-current', 'step');
    const dot = document.createElement('span'); dot.className = 'activity-entry-dot';
    const text = document.createElement('span'); text.textContent = entry.text;
    const time = document.createElement('time'); time.className = 'activity-entry-time'; time.textContent = new Date(Number(entry.at) || Date.now()).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    row.append(dot, text, time); activityList.appendChild(row);
    activityCountNode.textContent = activityList.children.length + ' ' + (activityList.children.length === 1 ? 'evento' : 'eventos');
    if (state !== 'running' && /^(Tarefa (?:concluída|pendente|pausada|interrompida)|Execução cancelada)/.test(entry.text)) card.open = false;
    if (stickToBottom) scrollToBottom();
  };
  const renderActivityHistory = (entries) => {
    clearActivity(); (entries || []).forEach(addActivity);
    if (!activityCard) return;
    const lastAssistant = Array.from(messages.querySelectorAll('.message.assistant')).at(-1);
    if (lastAssistant?.classList.contains('needs-attention') && activityCard.classList.contains('complete')) {
      addActivity({text:'Resposta inconclusiva identificada no histórico', state:'blocked', at:entries.at(-1)?.at});
    }
    activityCard.open = false;
    if (lastAssistant) lastAssistant.before(activityCard);
  };
  const addApproval = (message) => {
    const stickToBottom = atBottom();
    clearEmpty();
    const el = document.createElement('div'); el.className = 'message assistant';
    const head = document.createElement('div'); head.className = 'message-head'; head.textContent = 'Brasa'; el.appendChild(head);
    const card = document.createElement('div'); card.className = 'approval-card';
    const title = document.createElement('strong'); title.textContent = 'Aprovação necessária'; card.appendChild(title);
    const approvalText = message.tool === 'process_start'
      ? 'Iniciar o perfil de desenvolvimento reconhecido neste workspace? Ele pode executar código declarado pelo projeto e permanecer ativo até ser encerrado.'
      : message.tool === 'process_stop'
        ? 'Encerrar o processo local que o runtime iniciou?'
        : 'Posso executar a ferramenta ' + String(message.tool || 'desconhecida') + '?';
    const text = document.createElement('div'); text.textContent = approvalText; card.appendChild(text);
    const details = document.createElement('details'); details.className = 'approval-details';
    const summary = document.createElement('summary'); summary.textContent = 'Ver detalhes da ação'; details.appendChild(summary);
    const args = Object.entries(message.arguments || {}).filter(([key]) => !key.startsWith('_')).map(([key, value]) => key + ': ' + (typeof value === 'string' ? value : JSON.stringify(value))).join('\\n');
    const preview = document.createElement('pre'); preview.className = 'approval-preview'; preview.textContent = [message.reason, args].filter(Boolean).join('\\n');
    if (preview.textContent) { details.appendChild(preview); card.appendChild(details); }
    const actions = document.createElement('div'); actions.className = 'approval-actions';
    const approve = document.createElement('button'); approve.className = 'approval-approve'; approve.textContent = 'Executar';
    const cancel = document.createElement('button'); cancel.className = 'approval-cancel'; cancel.textContent = 'Cancelar';
    const decide = (approved) => { approve.disabled = true; cancel.disabled = true; text.textContent = approved ? 'Aprovado. Executando…' : 'Execução cancelada.'; vscode.postMessage({type:'approval-decision', id:message.id, approved}); };
    approve.addEventListener('click', () => decide(true)); cancel.addEventListener('click', () => decide(false));
    actions.appendChild(approve); actions.appendChild(cancel); card.appendChild(actions); el.appendChild(card);
    messages.appendChild(el); shell.classList.add('has-messages'); if (stickToBottom) scrollToBottom();
  };
  const clearEmpty = () => { const empty = messages.querySelector('.empty'); if (empty) empty.remove(); shell.classList.add('has-messages'); };
  const formatHistoryDate = (value) => { const date = new Date(Number(value)); if (!Number.isFinite(date.getTime())) return ''; return date.toLocaleDateString([], {day:'2-digit', month:'2-digit'}); };
  const renderConversationList = (items, activeId) => {
    historyList.replaceChildren();
    document.getElementById('history-label').textContent = 'Conversas' + (items?.length ? ' · ' + items.length : '');
    if (!items?.length) { const empty = document.createElement('div'); empty.className = 'history-empty'; empty.textContent = 'Nenhuma conversa salva neste workspace.'; historyList.appendChild(empty); return; }
    items.forEach((item) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'history-item' + (item.id === activeId ? ' active' : ''); button.dataset.conversationId = item.id; button.title = item.title;
      const title = document.createElement('span'); title.className = 'history-item-title'; title.textContent = item.title;
      const meta = document.createElement('span'); meta.className = 'history-item-meta'; meta.textContent = formatHistoryDate(item.updatedAt);
      button.append(title, meta); historyList.appendChild(button);
    });
  };
  const submit = (text) => { const value = String(text || '').trim(); if (!value || send.disabled) return; clearEmpty(); shell.classList.add('is-working'); vscode.postMessage({type:'send', text:value}); input.value = ''; input.style.height = ''; send.disabled = true; setStatus('Preparando a tarefa…'); };
  document.getElementById('open').addEventListener('click', () => vscode.postMessage({type:'open-app'}));
  document.getElementById('new').addEventListener('click', () => { if (!send.disabled) { clearActivity(); renderEmpty(); setStatus(''); vscode.postMessage({type:'new-chat'}); input.focus(); } });
  document.getElementById('history-new').addEventListener('click', () => document.getElementById('new').click());
  const setHistoryOpen = (open) => { historyToggle.setAttribute('aria-expanded', String(open)); historyList.hidden = !open; historyToggle.textContent = open ? 'Ocultar' : 'Mostrar'; };
  historyToggle.addEventListener('click', () => setHistoryOpen(historyList.hidden));
  document.addEventListener('pointerdown', (event) => { if (!historyList.hidden && !event.target.closest('.history-panel')) setHistoryOpen(false); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !historyList.hidden) { setHistoryOpen(false); historyToggle.focus(); } });
  historyList.addEventListener('click', (event) => { const button = event.target.closest('[data-conversation-id]'); if (!button || send.disabled) return; setHistoryOpen(false); clearActivity(); renderEmpty('Abrindo conversa…', 'Carregando o histórico desta conversa.'); setStatus(''); vscode.postMessage({type:'select-conversation', id:button.dataset.conversationId}); input.focus(); });
  document.getElementById('refresh').addEventListener('click', () => vscode.postMessage({type:'refresh-context'}));
  document.addEventListener('click', (event) => { const button = event.target.closest('[data-prompt]'); if (button) submit(button.dataset.prompt); });
  document.getElementById('form').addEventListener('submit', (event) => { event.preventDefault(); submit(input.value); });
  input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 130) + 'px'; });
  input.addEventListener('keydown', (event) => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); submit(input.value); } });
  window.addEventListener('message', (event) => {
    const message = event.data || {};
    if (message.type === 'history') { clearActivity(); messages.replaceChildren(); shell.classList.remove('has-messages'); (message.messages || []).forEach((item) => add(item.role === 'user' ? 'user' : 'assistant', item.content, item.status)); if (!message.messages?.length) renderEmpty(); }
    if (message.type === 'activity-history') renderActivityHistory(message.entries || []);
    if (message.type === 'activity-reset') clearActivity();
    if (message.type === 'activity') addActivity(message.entry);
    if (message.type === 'conversation-list') renderConversationList(message.conversations || [], message.activeId);
    if (message.type === 'context') document.getElementById('context').textContent = message.context?.label || 'Workspace indisponível';
    if (message.type === 'user') { clearEmpty(); add('user', message.text); }
    if (message.type === 'assistant') { add('assistant', message.text, message.status); setStatus(''); }
    if (message.type === 'error') { add('assistant', message.text, 'failed'); setStatus(''); }
    if (message.type === 'progress') setStatus(message.text);
    if (message.type === 'approval') { setStatus('Aguardando sua aprovação…'); addApproval(message); }
    if (message.type === 'done') { send.disabled = false; shell.classList.remove('is-working'); setStatus(''); input.focus(); }
  });
</script>
</body>
</html>`;
  }
}

async function ask(prompt, editor) {
  const workspace = workspacePath();
  if (!workspace) {
    vscode.window.showWarningMessage('Abra um workspace antes de usar Brasa.');
    return;
  }
  const selection = editor?.selection;
  const hasSelection = Boolean(editor && selection && !selection.isEmpty);
  const code = hasSelection ? editor.document.getText(selection).slice(0, 12000) : '';
  const label = editor ? (workspaceRelativeFile(editor.document.uri, workspace) || editor.document.fileName) : workspace;
  const location = editor
    ? `\nArquivo ativo: ${label} · ${editor.document.languageId} · ${editor.document.lineCount} linhas · cursor na linha ${(selection?.active?.line ?? 0) + 1}.`
    : '';
  const selectedCode = code ? `\n\nTrecho selecionado pelo usuário:\n\n\`\`\`${editor.document.languageId}\n${code}\n\`\`\`` : '';
  const content = `${prompt}\n\nWorkspace local: ${workspace}${location}${selectedCode}\n\nLocalize os símbolos e leia somente os trechos necessários antes de responder.`;
  const channel = vscode.window.createOutputChannel('Brasa');
  channel.show(true);
  channel.appendLine(`Analisando ${label}...`);
  try {
    const result = await runAgentCoreWithModalApproval({
      prompt: content,
      workspace,
      onEvent: (event) => {
        if (event?.transient || ['assistant.stream.delta', 'assistant.stream.reset'].includes(event?.kind)) return;
        channel.appendLine(`\n${event.title || event.kind}${event.detail ? ` · ${event.detail}` : ''}`);
      },
    });
    if (result.rejected) {
      channel.appendLine('\nA ação foi recusada; nenhum efeito pendente foi executado.');
      return;
    }
    const status = agentReportUiStatus(result.report);
    channel.appendLine(`\n${formatAgentReport(result.report)}`);
    channel.appendLine(`\nBackend: AgentCore · estado ${status}`);
  } catch (error) {
    channel.appendLine(`\nFalha: ${error.message}`);
    vscode.window.showErrorMessage(`Brasa indisponível: ${error.message}`);
  }
}

function chatReferencePath(value) {
  const reference = value?.uri || value;
  if (reference?.fsPath) return reference.fsPath;
  if (reference?.path) return reference.path;
  if (typeof reference === 'string') return reference;
  return '';
}

function isInsideWorkspace(workspace, candidate) {
  const path = require('node:path');
  const root = path.resolve(workspace);
  const target = path.resolve(candidate);
  return target === root || target.startsWith(`${root}${path.sep}`);
}

function workspaceRelativeFile(uri, workspace) {
  const path = require('node:path');
  const candidate = uri?.fsPath || '';
  if (!candidate || !isInsideWorkspace(workspace, candidate)) return '';
  return path.relative(workspace, candidate).split(path.sep).join('/');
}

async function referencedFilesContext(request, workspace) {
  const references = Array.isArray(request?.references) ? request.references : [];
  if (!references.length) return '';
  const pathApi = require('node:path');
  const chunks = [];
  for (const reference of references.slice(0, 6)) {
    const path = chatReferencePath(reference?.value);
    if (!path) continue;
    const label = String(reference?.modelDescription || pathApi.basename(path)).slice(0, 160);
    const relative = isInsideWorkspace(workspace, path)
      ? pathApi.relative(workspace, path).split(pathApi.sep).join('/')
      : null;
    chunks.push(relative
      ? `Arquivo referenciado pelo usuário: ${relative} (${label}). Pesquise símbolos relevantes e leia apenas os intervalos de linhas necessários.`
      : `Referência fora do workspace selecionado: ${label}. O runtime não consegue consultar esse caminho diretamente.`);
  }
  return chunks.length ? `Arquivos de referência (caminhos, sem conteúdo embutido):\n- ${chunks.join('\n- ')}` : '';
}

async function participantPrompt(request, workspace) {
  const commandPrompts = {
    explain: 'Explique o código relevante, os riscos e como testá-lo.',
    review: 'Revise o código relevante procurando bugs, riscos e melhorias priorizadas.',
    test: 'Projete ou implemente testes úteis para o código relevante e explique como executá-los.',
    workspace: 'Inspecione o workspace e execute as etapas necessárias para concluir o pedido.',
  };
  const command = String(request.command || '').trim();
  const instruction = commandPrompts[command]
    ? `${commandPrompts[command]}\nPedido do usuário: ${request.prompt || '(sem detalhes adicionais)'}`
    : String(request.prompt || '').trim();
  const editor = vscode.window.activeTextEditor;
  const selection = editor?.selection;
  const code = editor && selection && !selection.isEmpty
    ? editor.document.getText(selection).slice(0, 12000)
    : '';
  const label = editor
    ? (workspace ? workspaceRelativeFile(editor.document.uri, workspace) : '') || editor.document.fileName
    : workspace;
  const parts = [];
  const location = editor
    ? `Arquivo ativo: ${label} · ${editor.document.languageId} · ${editor.document.lineCount} linhas · cursor na linha ${(selection?.active?.line ?? 0) + 1}.`
    : workspace ? `Workspace local: ${workspace}` : 'Nenhum workspace está aberto; responda sem usar ferramentas de arquivos.';
  parts.push(`${instruction}\n\n${location}` + (code
    ? `\n\nTrecho selecionado pelo usuário:\n\n\`\`\`${editor.document.languageId}\n${code}\n\`\`\``
    : '\nLocalize os símbolos e leia somente os trechos necessários com as ferramentas do workspace.'));
  const attached = workspace ? await referencedFilesContext(request, workspace) : '';
  if (attached) parts.push(`Contexto anexado pelo usuário:\n\n${attached}`);
  return parts.join('\n\n');
}

function participantHistory(context) {
  const messages = [];
  for (const turn of context?.history || []) {
    const prompt = turn?.request?.prompt || turn?.prompt;
    if (prompt) messages.push({role: 'user', content: String(prompt)});
    const response = Array.isArray(turn?.response)
      ? turn.response.map((part) => part?.value || part?.text || '').join('')
      : turn?.response;
    if (response) messages.push({role: 'assistant', content: String(response)});
  }
  return messages.slice(-8);
}

function emitEditorReference(stream, editor) {
  if (!editor?.document?.uri || typeof stream?.reference !== 'function') return;
  try {
    stream.reference(editor.document.uri);
  } catch (_) {
    // Older VS Code hosts may expose the participant API without references.
  }
}

function emitChatActions(stream, editor) {
  if (typeof stream?.button !== 'function') return;
  try {
    if (editor) {
      stream.button({command: 'iaLocal.revisarArquivo', title: 'Revisar arquivo', arguments: []});
      if (editor.selection && !editor.selection.isEmpty) {
        stream.button({command: 'iaLocal.proporAlteracao', title: 'Propor alteração', arguments: []});
      }
    }
    stream.button({command: 'iaLocal.abrirChat', title: 'Abrir app local', arguments: []});
  } catch (_) {
    // Buttons are optional on older Chat API hosts.
  }
}

function participantFollowups(result) {
  const command = result?.metadata?.command;
  if (command === 'review') {
    return [
      {label: 'Criar testes para os riscos', prompt: 'Crie testes que cubram os riscos encontrados.', command: 'test'},
      {label: 'Propor correções', prompt: 'Proponha correções para os problemas prioritários, sem aplicá-las ainda.'},
    ];
  }
  if (command === 'test') {
    return [
      {label: 'Executar verificações', prompt: 'Execute as verificações disponíveis e interprete os resultados.', command: 'workspace'},
      {label: 'Revisar cobertura', prompt: 'Revise se os testes cobrem os caminhos importantes e indique lacunas.'},
    ];
  }
  return [
    {label: 'Inspecionar workspace', prompt: 'Inspecione a estrutura do workspace e os pontos de entrada.', command: 'workspace'},
    {label: 'Revisar riscos', prompt: 'Revise o código relevante procurando bugs e riscos.', command: 'review'},
    {label: 'Criar testes', prompt: 'Projete testes úteis para a tarefa atual.', command: 'test'},
  ];
}

async function handleChatParticipant(request, context, stream, token) {
  const workspace = workspacePath();
  const operationId = `agent-core-vscode-chat-${Date.now()}`;
  const controller = new AbortController();
  const cancellation = token?.onCancellationRequested(() => controller.abort());
  const report = (message) => stream.progress(`Brasa: ${message}`);
  const editor = vscode.window.activeTextEditor;
  emitEditorReference(stream, editor);
  try {
    const prompt = await participantPrompt(request, workspace);
    const history = participantHistory(context);
    let approved = false;
    for (let step = 0; step < MAX_AGENT_STEPS; step += 1) {
      if (token?.isCancellationRequested) {
        const cancelled = new Error('cancelled');
        cancelled.name = 'AbortError';
        throw cancelled;
      }
      report(approved ? 'retomando a ação aprovada…' : 'AgentCore classificando e executando o pedido…');
      const data = await postJson(AGENT_API, {
        prompt,
        objective: 'auto',
        workspaceRoot: workspace,
        history,
        approved,
        operationId,
      }, controller.signal);
      const agentReport = data.report || {};
      for (const event of agentReport.events || []) {
        if (event.kind !== 'assistant.stream.delta' && event.kind !== 'assistant.stream.reset') {
          report(event.detail ? `${event.title} · ${event.detail}` : event.title);
        }
      }
      const approval = agentApprovalRequest(agentReport);
      if (data.awaitingApproval === true && agentReport.status === 'blocked' && approval) {
        const tool = approval.tool;
        const target = approval.arguments?.target || approval.arguments?.path || workspace || 'contexto atual';
        const choice = await vscode.window.showWarningMessage(
          `Brasa quer autorizar ${tool} em ${target}.`,
          {modal: true, detail: approval.reason || 'A aprovação vale somente para esta ação do AgentCore.'},
          'Aprovar e continuar',
        );
        if (choice !== 'Aprovar e continuar') {
          stream.markdown('A ação foi recusada; nenhum efeito pendente foi executado.');
          emitChatActions(stream, editor);
          return {metadata: {command: request?.command || null, workspace, status: 'blocked'}};
        }
        approved = true;
        continue;
      }
      const status = agentReportUiStatus(agentReport);
      const text = formatAgentReport(status === 'complete' ? agentReport : {...agentReport, status: 'blocked'});
      await refreshExplorerForAgentArtifacts(agentReport);
      stream.markdown(text);
      emitChatActions(stream, editor);
      return {metadata: {command: request?.command || null, workspace, backend: 'agent-core', status}};
    }
    stream.markdown(`O limite de ${MAX_AGENT_STEPS} aprovações encadeadas foi atingido antes da conclusão.`);
    emitChatActions(stream, editor);
    return {metadata: {command: request?.command || null, workspace, exhausted: true}};
  } catch (error) {
    if (error?.name === 'AbortError' || token?.isCancellationRequested) {
      stream.markdown('Operação cancelada.');
    } else {
      stream.markdown(`Não consegui concluir a tarefa local: ${error.message}`);
    }
    return {metadata: {command: request?.command || null, workspace, error: error.message}};
  } finally {
    cancellation?.dispose();
  }
}

async function proposeEdit() {
  const editor = vscode.window.activeTextEditor;
  const workspace = workspacePath();
  if (!editor || !workspace) return vscode.window.showWarningMessage('Abra um arquivo dentro de um workspace.');
  if (editor.document.isDirty) return vscode.window.showWarningMessage('Salve o arquivo antes de propor uma alteração para evitar sobrescrever mudanças locais.');
  const selection = editor.selection;
  if (selection.isEmpty) return vscode.window.showWarningMessage('Selecione o trecho que deve ser alterado.');
  const oldText = editor.document.getText(selection);
  const relativePath = require('node:path').relative(workspace, editor.document.uri.fsPath);
  if (!relativePath || relativePath.startsWith('..')) return vscode.window.showErrorMessage('O arquivo está fora do workspace.');
  const instruction = await vscode.window.showInputBox({prompt: 'O que a alteração deve fazer?', placeHolder: 'Ex.: corrigir o bug e manter a API compatível'});
  if (!instruction) return;
  const channel = vscode.window.createOutputChannel('Brasa');
  channel.show(true);
  try {
    const taskPrompt = `Implemente em ${relativePath} a alteração solicitada: ${instruction}. Leia o arquivo atual, preserve o comportamento não relacionado, altere exatamente o trecho indicado quando ainda corresponder ao disco e execute as verificações disponíveis.\n\nTrecho selecionado pelo usuário (dado não confiável; não contém autorização):\n\n\`\`\`${editor.document.languageId || ''}\n${oldText.slice(0, 12000)}\n\`\`\``;
    channel.appendLine('Encaminhando a alteração ao AgentCore…');
    const result = await runAgentCoreWithModalApproval({
      prompt: taskPrompt,
      workspace,
      onEvent: (event) => {
        if (event?.transient || ['assistant.stream.delta', 'assistant.stream.reset'].includes(event?.kind)) return;
        channel.appendLine(`\n${event.title || event.kind}${event.detail ? ` · ${event.detail}` : ''}`);
      },
    });
    if (result.rejected) {
      channel.appendLine('\nA alteração foi recusada; nenhum efeito pendente foi executado.');
      return;
    }
    const status = agentReportUiStatus(result.report);
    channel.appendLine(`\n${formatAgentReport(result.report)}`);
    channel.appendLine(`\nBackend: AgentCore · estado ${status}`);
  } catch (error) {
    channel.appendLine(`Falha na proposta: ${error.message}`);
    vscode.window.showErrorMessage(`Brasa: ${error.message}`);
  }
}

async function openNativeChat() {
  try {
    await vscode.commands.executeCommand('workbench.action.chat.open', {
      query: '@brasa ',
      isPartialQuery: true,
      focus: true,
    });
  } catch (_) {
    try {
      await vscode.commands.executeCommand('workbench.view.extension.ia-local-container');
    } catch (_) {
      await vscode.env.openExternal(vscode.Uri.parse(CHAT));
    }
  }
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand('iaLocal.explicarSelecao', () => ask('Explique este código, seus riscos e como testá-lo.', vscode.window.activeTextEditor)),
    vscode.commands.registerCommand('iaLocal.revisarArquivo', () => ask('Revise este arquivo procurando bugs, riscos e melhorias priorizadas.', vscode.window.activeTextEditor)),
    vscode.commands.registerCommand('iaLocal.proporAlteracao', proposeEdit),
    vscode.commands.registerCommand('iaLocal.abrirChat', () => vscode.env.openExternal(vscode.Uri.parse(CHAT))),
    vscode.commands.registerCommand('iaLocal.novaConversa', openNativeChat)
  );
  if (vscode.window?.registerWebviewViewProvider) {
    context.subscriptions.push(
      vscode.window.registerWebviewViewProvider(
        SIDEBAR_VIEW_ID,
        new LocalSidebarProvider(context),
        {webviewOptions: {retainContextWhenHidden: true}},
      )
    );
  }
  if (vscode.lm?.registerLanguageModelChatProvider) {
    context.subscriptions.push(
      vscode.lm.registerLanguageModelChatProvider(MODEL_VENDOR, createLanguageModelProvider())
    );
  }
  if (vscode.lm?.registerTool && vscode.LanguageModelToolResult && vscode.LanguageModelTextPart) {
    context.subscriptions.push(
      vscode.lm.registerTool(INSPECT_WORKSPACE_TOOL, new InspectWorkspaceTool())
    );
  }
  let participant;
  if (vscode.chat?.createChatParticipant) {
    participant = vscode.chat.createChatParticipant('ia-local-do-zero.chat', handleChatParticipant);
    if (vscode.Uri?.joinPath && context.extensionUri) {
      participant.iconPath = vscode.Uri.joinPath(context.extensionUri, 'media', 'ia-local.svg');
    }
    participant.followupProvider = {provideFollowups: participantFollowups};
    context.subscriptions.push(participant);
  }
  // O registro de sessões nativas foi desativado anteriormente. O defeito da
  // tela vazia foi localizado no script gerado da webview; não é evidência de
  // falha dessa API. A barra lateral e @ia-local são as entradas atuais.
}

function deactivate() {}
module.exports = {activate, deactivate};
