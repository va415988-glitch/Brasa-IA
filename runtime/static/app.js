'use strict';
const chat = document.getElementById('chat'), input = document.getElementById('message');
let conversation = [], timeline = [], attachments = [], currentConversationId = null;
let agentRunRef = null;
let selectedTool = null, sending = false, activityCursor = 0, polling = false;
let openFilePath = null, originalFileContent = '';
let previewZoom = 1;
let projectCatalogBase = localStorage.getItem('ia-local-zero-project-base') || '';
const liveActions = new Map();
const workflowLabels = {understand:'Entendendo a solicitação', plan:'Montando o plano', learn:'Consultando conhecimento', retrieve:'Recuperando contexto', act:'Executando a etapa', verify:'Verificando o resultado', answer:'Preparando a resposta', complete:'Fluxo concluído'};
const HISTORY_KEY = 'ia-local-zero-conversations-v1';
const toolLabels = {search_web:'Pesquisando na internet', research_web:'Fazendo pesquisa guiada', open_page:'Lendo página', list_files:'Listando arquivos', search_files:'Buscando no código', inspect_project:'Inspecionando o projeto', project_checks:'Executando testes do projeto', diagnose_project:'Diagnosticando falha', propose_repair:'Validando correção', apply_repair:'Aplicando correção', read_file:'Lendo arquivo', list_sources:'Consultando fontes', set_workspace:'Selecionando projeto', create_workspace:'Criando projeto', create_file:'Criando arquivo', create_web_page:'Criando página web', edit_file:'Editando arquivo', create_directory:'Criando pasta'};
const backendLabels = {'static-analysis':'Análise estática dos anexos','local-conversation':'Conversa local','curated-memory':'Memória curada · fallback','local-knowledge':'Acervo local · fallback','local-neural':'Modelo neural próprio','research-evidence':'Síntese das fontes consultadas','source-assessment':'Verificação de fontes','quality-gate':'Limite de conhecimento'};
function node(tag, className, text) { const el = document.createElement(tag); if (className) el.className = className; if (text != null) el.textContent = text; return el; }
function scrollChat() { chat.scrollTop = chat.scrollHeight; }
function setWorkspaceIndicator(path) {
  const value = path || 'Nenhum projeto selecionado';
  if (path) {
    localStorage.setItem('ia-local-zero-workspace', path);
    const parent = path.replace(/\\/g, '/').replace(/\/$/, '').split('/').slice(0, -1).join('/') || '/';
    projectCatalogBase = parent;
    localStorage.setItem('ia-local-zero-project-base', parent);
  }
  const side = document.getElementById('workspace-current');
  const header = document.getElementById('header-workspace');
  const headerLabel = document.getElementById('header-workspace-label');
  const dock = document.getElementById('dock-workspace');
  if (side) { side.textContent = value; side.title = value; }
  if (headerLabel) headerLabel.textContent = value;
  if (header) { header.title = value; header.setAttribute('aria-label', `Projeto ativo: ${value}`); }
  if (dock) { dock.textContent = value; dock.title = value; }
  const homePath=document.getElementById('workspace-home-path');
  if (homePath) homePath.textContent=value;
  const rootLabel=document.getElementById('dock-root-label');
  if (rootLabel && path) rootLabel.textContent=(path.split('/').filter(Boolean).pop() || 'PROJETO').toUpperCase();
}
function projectParent(path) {
  const normalized = String(path || '').replace(/\\/g, '/').replace(/\/$/, '');
  const index = normalized.lastIndexOf('/');
  return index > 0 ? normalized.slice(0, index) : '/';
}
function closeProjectMenu() {
  const menu = document.getElementById('project-menu');
  const button = document.getElementById('header-workspace');
  if (menu) menu.hidden = true;
  if (button) button.setAttribute('aria-expanded', 'false');
}
function projectMenuItem(project, current) {
  const button = node('button', `project-menu-item${project.path === current ? ' active' : ''}`);
  button.type = 'button';
  button.title = project.path;
  const icon = node('span', 'project-menu-icon', project.kind === 'git' ? '◆' : '▦');
  const copy = node('span', 'project-menu-copy');
  copy.append(node('strong', null, project.name), node('small', null, project.path));
  button.append(icon, copy, node('span', 'project-menu-check', project.path === current ? '✓' : ''));
  button.onclick = () => selectHeaderProject(project.path);
  return button;
}
async function loadProjectMenu() {
  const list = document.getElementById('project-list');
  if (!list) return;
  list.replaceChildren(node('div', 'project-menu-loading', 'Carregando projetos locais…'));
  try {
    const response = await fetch('/api/projects', {cache:'no-store'});
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || 'não foi possível listar os projetos');
    projectCatalogBase = data.base || projectCatalogBase;
    if (projectCatalogBase) localStorage.setItem('ia-local-zero-project-base', projectCatalogBase);
    const current = localStorage.getItem('ia-local-zero-workspace') || data.current || '';
    list.replaceChildren();
    for (const project of data.projects || []) list.append(projectMenuItem(project, current));
    if (!list.children.length) list.append(node('div', 'project-menu-loading', 'Nenhuma pasta de projeto encontrada.'));
  } catch (error) {
    list.replaceChildren(node('div', 'project-menu-error', error.message));
  }
}
async function toggleProjectMenu(event) {
  event?.stopPropagation();
  const menu = document.getElementById('project-menu');
  const button = document.getElementById('header-workspace');
  if (!menu) return;
  const opening = menu.hidden;
  menu.hidden = !opening;
  if (button) button.setAttribute('aria-expanded', String(opening));
  if (opening) await loadProjectMenu();
}
async function selectHeaderProject(path) {
  closeProjectMenu();
  const selected = await toolRequest('set_workspace', {path}, false);
  if (!selected.ok) return notice(selected.error || 'Não foi possível selecionar esse projeto.');
  setWorkspaceIndicator(selected.data.workspace);
  refreshProjectDock();
  notice(`Projeto ativo: ${selected.data.workspace}`);
}
async function chooseHeaderProject() {
  closeProjectMenu();
  try {
    const response = await fetch('/api/choose-directory', {cache:'no-store'});
    const data = await response.json();
    if (data.ok) await selectHeaderProject(data.path);
    else if (!data.cancelled) notice(data.error || 'Não foi possível escolher a pasta.');
  } catch (error) { notice(`Não foi possível abrir o gerenciador: ${error.message}`); }
}
async function createHeaderProject() {
  const field = document.getElementById('project-name-input');
  const name = field?.value.trim() || '';
  if (!name || name === '.' || name === '..' || /[\\/]/.test(name)) return notice('Informe apenas o nome da nova pasta, sem barras.');
  const parent = projectCatalogBase || projectParent(localStorage.getItem('ia-local-zero-workspace'));
  if (!parent) return notice('Escolha primeiro uma pasta-base para o projeto.');
  const path = `${parent.replace(/\/$/, '')}/${name}`;
  const data = await toolRequest('create_workspace', {path}, false);
  if (!data.ok) return notice(data.error || 'Não foi possível criar o projeto.');
  setWorkspaceIndicator(data.data.workspace);
  if (field) field.value = '';
  await loadProjectMenu();
  refreshProjectDock();
  notice(`Novo projeto criado: ${data.data.workspace}`);
}
function updateEditorMeta() {
  const editor=document.getElementById('stage-editor');
  const info=document.getElementById('stage-editor-info');
  if(!editor || !info) return;
  const lines=editor.value ? editor.value.split('\n').length : 0;
  info.textContent=`${lines} ${lines===1?'linha':'linhas'}`;
}
function copyEditorCode() {
  const editor=document.getElementById('stage-editor');
  if(!editor || !editor.value) return notice('Nenhum código aberto para copiar.');
  navigator.clipboard.writeText(editor.value).then(()=>{
    notice('Código do arquivo copiado para a área de transferência.');
  }).catch(()=>{
    notice('Não foi possível copiar o código.');
  });
}
function copyCodeBlock(button) {
  const pre = button.closest('.code-block-wrap')?.querySelector('.code-block-pre');
  const codeEl = pre?.querySelector('code');
  if (!codeEl) return;
  const text = pre.dataset.raw || codeEl.textContent || '';
  navigator.clipboard.writeText(text).then(() => {
    const originalText = button.textContent;
    button.textContent = 'Copiado!';
    button.classList.add('copied');
    setTimeout(() => {
      button.textContent = originalText;
      button.classList.remove('copied');
    }, 1800);
  }).catch(() => {
    button.textContent = 'Erro';
    setTimeout(() => { button.textContent = 'Copiar'; }, 1800);
  });
}
function toggleCodeBlock(button) {
  const wrap = button.closest('.code-block-wrap');
  if (!wrap) return;
  const expanded = wrap.classList.toggle('expanded');
  button.textContent = expanded ? 'Recolher' : 'Expandir';
}
function openCodeOverlay(button) {
  const wrap = button.closest('.code-block-wrap');
  const pre = wrap?.querySelector('.code-block-pre');
  const lang = wrap?.querySelector('.code-block-lang')?.textContent || 'código';
  if (!pre) return;
  const raw = pre.dataset.raw || pre.textContent || '';
  const overlay = node('div', 'code-overlay');
  const card = node('section', 'code-overlay-card');
  const head = node('div', 'code-overlay-head');
  const title = node('div');
  title.append(node('div', 'code-overlay-title', `Código · ${lang}`), node('div', 'code-overlay-subtitle', `${raw.split('\n').length} linhas · modo de foco`));
  const actions = node('div', 'code-overlay-actions');
  const copy = node('button', null, 'Copiar');
  copy.onclick = () => navigator.clipboard.writeText(raw).then(() => { copy.textContent = 'Copiado'; setTimeout(() => { copy.textContent = 'Copiar'; }, 1400); });
  const close = node('button', null, 'Fechar');
  close.onclick = () => overlay.remove();
  actions.append(copy, close); head.append(title, actions);
  const body = node('pre', 'code-overlay-pre'); body.textContent = raw;
  card.append(head, body); overlay.append(card); overlay.onclick = event => { if (event.target === overlay) overlay.remove(); };
  document.body.append(overlay); close.focus();
}
function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  if (sidebar) sidebar.classList.toggle('open');
}
function setEditorFile(data) {
  const tab=document.getElementById('stage-tab'), editor=document.getElementById('stage-editor');
  if(!tab || !editor) return;
  document.body.classList.add('has-editor');
  openFilePath=data.path; originalFileContent=data.content || '';
  document.querySelector('.stage-workarea')?.classList.remove('preview-open');
  tab.textContent=data.path || 'Arquivo';
  editor.value=data.content || '';
  updateEditorMeta();
  const copyBtn = document.getElementById('stage-copy');
  if(copyBtn) copyBtn.disabled = false;
  ['stage-save','stage-review','stage-discard'].forEach(id=>{ const button=document.getElementById(id); if(button) button.disabled=true; });
  const previewButton=document.getElementById('stage-preview-button'); if(previewButton) { previewButton.disabled=!/\.html?$/i.test(data.path || ''); previewButton.textContent='Prévia'; }
  ['stage-zoom-out','stage-zoom-in'].forEach(id=>{ const button=document.getElementById(id); if(button) button.disabled=!/\.html?$/i.test(data.path || ''); });
  editor.oninput=()=>{
    const dirty=editor.value!==originalFileContent;
    ['stage-save','stage-review','stage-discard'].forEach(id=>{ const button=document.getElementById(id); if(button) button.disabled=!dirty; });
    updateEditorMeta();
  };
  editor.onkeydown=(e)=>{
    if (e.key === 'Tab') {
      e.preventDefault();
      const start = editor.selectionStart, end = editor.selectionEnd;
      editor.value = editor.value.substring(0, start) + '  ' + editor.value.substring(end);
      editor.selectionStart = editor.selectionEnd = start + 2;
      editor.dispatchEvent(new Event('input'));
    } else if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      if (editor.value !== originalFileContent) {
        saveEditorFile();
      }
    }
  };
}
async function openDockFolder(path) {
  const dockFiles=document.getElementById('dock-files'); if(!dockFiles) return;
  const data=await toolRequest('list_files',{path},false);
  if(!data.ok) { dockFiles.replaceChildren(node('div','dock-empty',data.error)); return; }
  dockFiles.replaceChildren();
  const back=node('div','dock-empty','‹ voltar'); back.style.cursor='pointer'; back.onclick=()=>loadDockRoot(); dockFiles.append(back);
  for(const entry of data.data.entries.slice(0,80)) {
    const row=node('div','entry'), label=node('div',null,`${entry.kind==='directory'?'▣':'▤'} ${entry.name}`), button=node('button',null,entry.kind==='directory'?'›':'Abrir');
    const relative=`${path}/${entry.name}`;
    button.onclick=()=>entry.kind==='directory'?openDockFolder(relative):runPanelRead(relative);
    label.onclick=button.onclick; label.style.cursor='pointer'; row.append(label,button); dockFiles.append(row);
  }
}
async function loadDockRoot() {
  const dockFiles=document.getElementById('dock-files'); if(!dockFiles) return;
  const data=await toolRequest('list_files',{path:''},false); if(!data.ok) return;
  dockFiles.replaceChildren();
  for(const entry of data.data.entries.slice(0,80)) {
    const row=node('div','entry'), label=node('div',null,`${entry.kind==='directory'?'▣':'▤'} ${entry.name}`), button=node('button',null,entry.kind==='directory'?'›':'Abrir');
    button.onclick=()=>entry.kind==='directory'?openDockFolder(entry.name):runPanelRead(entry.name);
    label.onclick=button.onclick; label.style.cursor='pointer'; row.append(label,button); dockFiles.append(row);
  }
}
function toggleEditorPreview() {
  const editor=document.getElementById('stage-editor'), workarea=document.querySelector('.stage-workarea'), frame=document.getElementById('stage-preview');
  if(!editor || !workarea || !frame) return;
  const open=workarea.classList.toggle('preview-open');
  if(open) frame.srcdoc=editor.value;
}
function changePreviewZoom(delta) {
  previewZoom=Math.max(.6,Math.min(1.4,previewZoom+delta));
  const frame=document.getElementById('stage-preview'), label=document.getElementById('stage-zoom');
  if(frame) { const size=Math.round(420*previewZoom); frame.style.width=`${size}px`; frame.style.height=`${size}px`; }
  if(label) label.textContent=`${Math.round(previewZoom*100)}%`;
}
function reviewEditorChanges() {
  const editor=document.getElementById('stage-editor'); if(!editor || !openFilePath) return;
  const current=editor.value, old=originalFileContent;
  const artifact=makeCodeArtifact(openFilePath,old,current,'proposed');
  assistantReply(`Alterações pendentes em ${openFilePath}. Revise o diff estruturado abaixo antes de salvar.`,'Revisão local',[],null,artifact);
}
function discardEditorChanges() {
  const editor=document.getElementById('stage-editor'); if(!editor) return;
  editor.value=originalFileContent; editor.dispatchEvent(new Event('input'));
  assistantReply(`Descartei as alterações pendentes em ${openFilePath}.`,'Alterações descartadas');
}
async function saveEditorFile() {
  const editor=document.getElementById('stage-editor');
  if(!editor || !openFilePath || editor.value===originalFileContent) return notice('Nenhuma alteração pendente neste arquivo.');
  if(!(await confirmMutation('Salvar alterações?',openFilePath))) return;
  const result=await toolRequest('edit_file',{path:openFilePath,old_text:originalFileContent,new_text:editor.value},true);
  if(!result.ok) return assistantReply(result.error,'Arquivo não salvo');
  originalFileContent=editor.value;
  assistantReply(`Salvei ${openFilePath}. O arquivo atualizado continua aberto no editor.`,`Arquivo salvo · ${result.elapsed_ms} ms`,[],null,result.data?.artifact || makeCodeArtifact(openFilePath,originalFileContent,editor.value,'applied'));
  editor.dispatchEvent(new Event('input'));
  const checks=await toolRequest('project_checks',{check:'auto',path:openFilePath},false);
  const status=document.getElementById('dock-status');
  if(status) status.textContent=checks.ok && checks.data?.executed===false ? 'Arquivo salvo. Este projeto não possui verificações configuradas.' : checks.ok && checks.data?.passed===true ? 'Arquivo salvo. Verificações passaram.' : 'Arquivo salvo. Verificação pendente ou com falha.';
}
function setMode(mode) {
  const value=['workspace','training'].includes(mode)?mode:'chat';
  localStorage.setItem('ia-local-zero-mode',value);
  document.body.classList.toggle('chat-mode',value==='chat');
  document.body.classList.toggle('workspace-mode',value==='workspace');
  document.body.classList.toggle('training-mode',value==='training');
  document.body.classList.remove('has-editor');
  document.getElementById('mode-chat')?.classList.toggle('active',value==='chat');
  document.getElementById('mode-workspace')?.classList.toggle('active',value==='workspace');
  document.getElementById('mode-training')?.classList.toggle('active',value==='training');
  const subtitle=document.getElementById('mode-subtitle');
  if(subtitle) subtitle.textContent=value==='workspace'?'Projeto, arquivos e ferramentas':value==='training'?'Aprendizado verificável':'Conversa simples';
  const homePath=document.getElementById('workspace-home-path');
  if(homePath) homePath.textContent=localStorage.getItem('ia-local-zero-workspace') || 'Nenhum projeto selecionado';
  if(value==='workspace' && localStorage.getItem('ia-local-zero-workspace')) restoreWorkspace();
  if(value==='training') loadTraining();
}
async function loadTraining() {
  const grid=document.getElementById('training-grid'); if(!grid) return;
  grid.replaceChildren(node('div','training-card','Atualizando competências…'));
  try {
    const data=await fetch('/api/skills',{cache:'no-store'}).then(r=>r.json());
    const canonicalSkillTopic=(topic)=>{
      const value=String(topic||''), lower=value.toLowerCase();
      const aliases=[['doc.rust-lang.org','Rust'],['rustlings.rust-lang.org','Rust'],['tokio-rs/axum','Rust'],['ziglang.org','Zig'],['typescriptlang.org','TypeScript'],['nodejs.org','Node.js'],['learn.microsoft.com/dotnet/csharp','C#'],['dev.java','Java'],['learncpp.com','C++'],['python.org','Python'],['thealgorithms/python','Python'],['sqlbolt.com','SQL'],['cheatsheetseries.owasp.org','OWASP']];
      const match=aliases.find(([needle])=>lower.includes(needle));
      return match ? match[1] : value;
    };
    const grouped=new Map();
    for(const skill of Object.values(data.skills||{})) {
      const key=canonicalSkillTopic(skill.topic);
      const current=grouped.get(key);
      if(!current) { grouped.set(key,{...skill,topic:key}); continue; }
      const currentScore=Number(current.evaluation?.progress||current.confidence||0), incomingScore=Number(skill.evaluation?.progress||skill.confidence||0);
      const primary=incomingScore>currentScore?{...skill,topic:key}:current;
      const sources=[...(current.evidence?.sources||[]),...(skill.evidence?.sources||[])];
      primary.evidence={...(primary.evidence||{}),sources:[...new Map(sources.filter(item=>item?.url).map(item=>[item.url,item])).values()],documents:Math.max(Number(current.evidence?.documents||0),Number(skill.evidence?.documents||0)),independent_hosts:Math.max(Number(current.evidence?.independent_hosts||0),Number(skill.evidence?.independent_hosts||0))};
      primary.technologies=[...new Set([...(current.technologies||[]),...(skill.technologies||[])])];
      primary.source_repositories=[...new Set([...(current.source_repositories||[]),...(skill.source_repositories||[]),...(current.source_repository?[current.source_repository]:[]),...(skill.source_repository?[skill.source_repository]:[])])];
      if(primary.source_repositories.length) primary.source_repository=primary.source_repositories[0];
      const verified=[...(current.practice?.verified||[]),...(skill.practice?.verified||[])];
      if(verified.length) {
        const unique=[...new Map(verified.map(item=>[`${item.level||''}:${item.task||item.name||JSON.stringify(item)}`,item])).values()];
        primary.practice={...(primary.practice||{}),verified:unique,passed:unique.filter(item=>item.passed!==false && item.status!=='failed').length,tasks:Math.max(Number(current.practice?.tasks||0),Number(skill.practice?.tasks||0),unique.length),failed:Math.max(Number(current.practice?.failed||0),Number(skill.practice?.failed||0))};
      }
      const covered=[...(current.concepts?.covered||[]),...(skill.concepts?.covered||[])];
      const missing=[...(current.concepts?.gaps||[]),...(skill.concepts?.gaps||[])];
      primary.concepts={...(primary.concepts||{}),covered:[...new Set(covered)],gaps:[...new Set(missing)]};
      grouped.set(key,primary);
    }
    const skills=[...grouped.values()];
    const summary=document.getElementById('training-summary-list');
    const scoreFor=(skill)=>{ const practice=skill.practice||{}, evaluation=skill.evaluation||{}; return evaluation.progress != null ? Math.min(100,Math.round(Number(evaluation.progress||0)*100)) : Math.min(100,Math.round((Number(skill.confidence||0)*70)+(Math.min(1,Number(practice.passed||0)/5)*30))); };
    if(summary) {
      summary.replaceChildren(...(skills.length ? skills.map(skill=>{ const score=scoreFor(skill); const item=node('div','training-summary-item'); item.title=`${skill.topic||'Competência sem nome'} · ${score}%`; item.append(node('span','training-summary-name',skill.topic||'Competência sem nome'),node('span','training-summary-score',`${score}%`)); const meter=node('div','training-summary-meter'); meter.append(Object.assign(document.createElement('i'),{style:`width:${score}%`})); item.append(meter); return item; }) : [node('div','history-empty','Nenhuma competência registrada')]));
    }
    if(!skills.length) { grid.replaceChildren(node('div','training-card','Nenhuma competência foi iniciada. Use “Aprenda <assunto>” no chat.')); return; }
    grid.replaceChildren(...skills.map(skill=>{
      const practice=skill.practice||{}, evidence=skill.evidence||{}, gaps=skill.concepts?.gaps||[], evaluation=skill.evaluation||{};
      const score=scoreFor(skill);
      const card=node('article','training-card'); const head=node('div','training-card-head'); const title=node('div',null); title.append(node('div','training-status',skill.status||'em avaliação'),node('h3',null,skill.topic||'Competência sem nome')); const remove=node('button','training-delete','×'); remove.title='Excluir competência'; remove.onclick=()=>deleteTraining(skill.topic); head.append(title,remove); card.append(head,node('div','training-meter')); card.querySelector('.training-meter').append(Object.assign(document.createElement('i'),{style:`width:${score}%`}));
      card.append(node('div','training-meta',`Domínio ${score}% · Confiança ${Math.round(Number(skill.confidence||0)*100)}% · ${practice.passed||0}/${practice.tasks||0} práticas aprovadas · ${evidence.independent_hosts||0} fontes independentes`));
      card.append(node('div','training-evidence',`Evidências: ${evidence.documents||0} documento(s) · ${practice.failed||0} falha(s) registrada(s)`));
      if(skill.source_repository) card.append(node('div','training-evidence',`Repositório: ${skill.source_repository}`));
      if(skill.technologies?.length) card.append(node('div','training-evidence',`Tecnologias: ${skill.technologies.join(', ')}`));
      const levels=skill.curriculum?.levels||[]; const objectives=levels.reduce((total,level)=>total+(level.concepts||[]).length,0);
      if(objectives) card.append(node('div','training-evidence',`Trilha: ${levels.length} níveis · ${objectives} objetivos a comprovar`));
      if(evaluation.coverage) card.append(node('div','training-evidence',`Cobertura: ${evaluation.coverage.covered||0}/${evaluation.coverage.total||0} conceitos comprovados`));
      const missingCriteria={sources:'fontes independentes',levels:'níveis',coverage:'cobertura',practice:'prática',transfer:'transferência',integration:'integração'};
      const missing=Object.entries(evaluation.criteria||{}).filter(([,ok])=>!ok).map(([key])=>missingCriteria[key]||key);
      if(missing.length) card.append(node('div','training-evidence',`Ainda falta: ${missing.join(', ')}`));
      card.append(node('div','training-gaps',`Lacunas: ${gaps.slice(0,5).join(', ')||'nenhuma registrada'}`)); return card;
    }));
  } catch(error) { grid.replaceChildren(node('div','training-card',`Não foi possível carregar o estado: ${error.message}`)); }
}
async function deleteTraining(topic) {
  if(!confirm(`Excluir todo o estado de treinamento de “${topic}”?\n\nIsso remove fontes de competência, práticas, lacunas e evidências registradas para esse tema.`)) return;
  const response=await fetch(`/api/skills/${encodeURIComponent(topic)}`,{method:'DELETE'});
  const data=await response.json();
  if(!data.ok) return notice(data.error || 'Não foi possível excluir a competência.');
  await loadTraining();
  setLiveStatus({message:`Competência “${topic}” excluída.`,phase:'complete',done:true});
}
async function clearTraining() {
  if(!confirm('Excluir todo o estado de treinamento?\n\nIsso remove todas as competências, fontes, práticas, lacunas e evidências persistidas.')) return;
  const response=await fetch('/api/skills',{method:'DELETE'}); const data=await response.json();
  if(!data.ok) return notice(data.error || 'Não foi possível limpar o treinamento.');
  await loadTraining();
  setLiveStatus({message:`Treinamento limpo: ${data.removed||0} competência(s) removida(s).`,phase:'complete',done:true});
}
function setLiveStatus(event) {
  const status = document.getElementById('live-status');
  const text = document.getElementById('live-status-text');
  if (!status || !text) return;
  status.className = 'live-status ' + (event.done ? (event.phase === 'error' ? 'error' : 'done') : 'busy');
  const confidence = event.confidence != null ? ` · conf. ${ChatCore.confidenceText(event.confidence)}` : '';
  text.textContent = event.message + (event.elapsed_ms != null ? ` · ${event.elapsed_ms} ms` : '') + confidence;
}
function notice(message) { setLiveStatus({message, phase:'error', done:true}); }
function requestId(prefix) { return `${prefix}-${crypto.randomUUID()}`; }
function refreshControls() {
  const reading = attachments.some(item => item.state === 'reading');
  document.querySelector('.send').disabled = sending || reading || liveActions.size > 0;
  document.querySelector('.send').title = reading ? 'Aguarde a leitura dos anexos' : 'Enviar mensagem';
  document.querySelector('.new-chat').disabled = sending || liveActions.size > 0;
  input.setAttribute('aria-busy', String(sending || reading));
}
function renderAction(record) {
  const row = node('div', `action-message ${record.state || 'busy'}`);
  row.append(node('div','action-icon',record.state === 'done' ? '✓' : '✦'));
  const body = node('div','action-body'); body.append(node('div','action-title',record.title), node('div','action-log',record.log));
  if (record.progress != null) { const bar=node('progress','learning-progress'); bar.max=100; bar.value=record.progress; body.append(bar); }
  const details = node('details'); details.append(node('summary',null,'Etapas'));
  for (const step of record.steps || []) details.append(node('div', 'action-log', step));
  body.append(details); row.append(body); chat.append(row); return row;
}
function startAction(operation, title, showInChat) {
  const record = {kind:'action', title, state:'busy', log:'Enviando requisição…', steps:['Enviando requisição…']};
  let row = null;
  if (showInChat) { timeline.push(record); row = renderAction(record); saveConversation(); scrollChat(); }
  liveActions.set(operation, {record, row});
  setLiveStatus({message:title, done:false}); refreshControls(); pollActivity();
}
function updateAction(event, terminal = false) {
  const action = liveActions.get(event.operation); if (!action) return;
  const {record, row} = action;
  const eventTerminal = terminal || event.done === true || ['completed','failed','cancelled'].includes(event.status);
  const log = event.message + (event.elapsed_ms != null ? ` · ${event.elapsed_ms} ms` : '');
  if (record.log !== log) record.steps.push(log);
  record.log = log;
  const progress = typeof event.progress === 'object' ? event.progress.value : event.progress;
  if (progress != null) record.progress=progress;
  if (eventTerminal) record.state = event.phase === 'error' || event.status === 'failed' ? 'error' : 'done';
  if (row) {
    row.className = `action-message ${record.state}`;
    row.querySelector('.action-log').textContent = log;
    const bar=row.querySelector('progress'); if(bar && progress != null) bar.value=progress;
    row.querySelector('.action-icon').textContent = eventTerminal ? (record.state === 'done' ? '✓' : '!') : '✦';
    const details = row.querySelector('details');
    details.replaceChildren(node('summary',null,'Etapas'), ...record.steps.map(s => node('div','action-log',s)));
  }
  setLiveStatus({...event, done:eventTerminal});
}
function recordWorkflow(operation, workflow) {
  if (!workflow || !Array.isArray(workflow.stages)) return;
  const action = liveActions.get(operation); if (!action) return;
  for (const stage of workflow.stages) {
    const label = workflowLabels[stage] || stage;
    if (!action.record.steps.some(step => step === label)) action.record.steps.push(label);
  }
  if (action.row) {
    const details = action.row.querySelector('details');
    details.replaceChildren(node('summary',null,'Etapas'), ...action.record.steps.map(step => node('div','action-log',step)));
  }
}
async function pollActivity() {
  if (polling || !liveActions.size) return;
  polling = true;
  try {
    const response = await fetch(`/api/events?after_seq=${activityCursor}`, {cache:'no-store', signal:AbortSignal.timeout(2500)});
    if (response.ok) {
      const data = await response.json();
      if (data.latest_seq < activityCursor) activityCursor = 0;
      for (const event of data.events || []) {
        activityCursor = Math.max(activityCursor, event.seq || event.id || 0);
        const progress = typeof event.progress === 'object' ? event.progress.value : event.progress;
        updateAction({
          ...event,
          id: event.seq || event.id,
          operation: event.task_id || event.operation,
          message: event.detail || event.title || event.message,
          progress,
          done: event.status === 'completed' || event.status === 'failed' || event.status === 'cancelled',
          phase: event.status === 'failed' ? 'error' : event.phase,
        });
      }
    }
  } catch (_) { /* A resposta HTTP encerra a ação mesmo se o canal de eventos cair. */ }
  finally { polling = false; if (liveActions.size) setTimeout(pollActivity, 450); }
}
async function requestWithActivity(url, payload, operation, title, showInChat = false) {
  startAction(operation, title, showInChat);
  const started = performance.now();
  const controller = new AbortController();
  const timeoutMs = url === '/api/chat' ? 900000 : payload?.tool === 'research_web' ? 30000 : 10000;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let data;
  try {
    const response = await fetch(url, {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(payload), signal:controller.signal});
    data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || `Falha HTTP ${response.status}`);
    recordWorkflow(operation, data.workflow);
    // O agente precisa ser observável. Mesmo respostas locais rápidas ganham
    // uma janela mínima para que o usuário veja a etapa e seus registros.
    const minimumVisibleMs = url === '/api/chat' ? 900 : 350;
    const remaining = minimumVisibleMs - (performance.now() - started);
    if (remaining > 0) await new Promise(resolve=>setTimeout(resolve, remaining));
    if (data.agent?.recovery) updateAction({operation, message:'Falha detectada; procurando uma alternativa verificável.', phase:'recovering'}, false);
    const confidence = data && data.confidence != null ? Number(data.confidence) : null;
    updateAction({operation, message:'Concluído.', phase:'done', elapsed_ms:Math.round(performance.now()-started), confidence}, true);
  } catch (error) {
    const message = error.name === 'AbortError' ? `O limite de ${Math.round(timeoutMs/1000)} s foi atingido. Não houve confirmação de conclusão; confira o resultado antes de repetir uma alteração.` : `Não foi possível concluir: ${error.message}`;
    data = {ok:false, error:message};
    updateAction({operation, message, phase:'error', elapsed_ms:Math.round(performance.now()-started), confidence:null}, true);
  } finally {
    clearTimeout(timer); liveActions.delete(operation); refreshControls(); saveConversation();
  }
  return data;
}
function readHistory() { try { const items=JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); return Array.isArray(items) ? items : []; } catch (_) { return []; } }
function saveConversation() {
  const first = conversation.find(m => m.role === 'user'); if (!first) return;
  currentConversationId ||= requestId('conversation');
  const title = (first.content || first.attachments?.[0]?.name || 'Nova conversa').replace(/\s+/g,' ').slice(0,120);
  const record = {id:currentConversationId,title,updatedAt:Date.now(),messages:conversation,timeline,agentRunRef};
  const items = [record, ...readHistory().filter(item => item.id !== currentConversationId)].slice(0,30);
  while (items.length > 1 && JSON.stringify(items).length > 1800000) items.pop();
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(items)); }
  catch (_) { notice('Não foi possível salvar o histórico: o armazenamento local está cheio ou indisponível.'); }
  renderHistory();
}
function renderHistory(filter='') {
  const list = document.getElementById('history-list'); if (!list) return;
  list.replaceChildren();
  const items = readHistory().filter(i => i.title.toLowerCase().includes(filter.toLowerCase()));
  for (const item of items) {
    const button = node('button', `history-item ${item.id === currentConversationId ? 'active' : ''}`, item.title);
    button.title = item.title; button.onclick = () => loadConversation(item.id); list.append(button);
  }
  if (!list.children.length) list.append(node('div','history-empty', filter ? 'Nenhuma conversa encontrada' : 'Nenhuma conversa salva'));
}
function filterConversations(val) {
  renderHistory(val || '');
  const clearBtn = document.getElementById('clear-search-btn');
  if (clearBtn) clearBtn.style.display = val ? 'inline-block' : 'none';
}
function clearConversationSearch() {
  const input = document.getElementById('conversation-search-input');
  if (input) { input.value = ''; filterConversations(''); input.focus(); }
}
function focusConversationSearch() {
  const input = document.getElementById('conversation-search-input');
  if (input) input.focus();
}
function clearAttachments() { attachments.forEach(item => item.url && URL.revokeObjectURL(item.url)); attachments=[]; renderAttachments(); }
function loadConversation(id) {
  if (sending || liveActions.size) return notice('Aguarde a operação atual terminar para trocar de conversa.');
  const item = readHistory().find(entry => entry.id === id); if (!item) return;
  saveConversation(); clearAttachments(); closeTool(); currentConversationId = item.id; conversation = item.messages || [];
  agentRunRef = item.agentRunRef || null;
  timeline = item.timeline || conversation.map(m => ({kind:m.role,text:m.content,meta:'Histórico local'})); chat.replaceChildren();
  for (const record of timeline) {
    if (record.kind === 'action') {
      if (record.state === 'busy') Object.assign(record,{state:'error',log:'Operação interrompida ao sair da página. Confira o resultado antes de repetir.'});
      renderAction(record);
    } else addMessage(record.kind, record.text, record.meta, record.cards, false, record.preview, record.artifact, record.contract);
  }
  selectedTool=null; renderAttachments(); renderHistory(); scrollChat();
  if(agentRunRef) watchAgentRun();
}
function toggleAttachmentMenu() { document.getElementById('attach-menu').classList.toggle('open'); }
function selectTool(tool) { selectedTool = tool; document.getElementById('attach-menu').classList.remove('open'); renderAttachments(); input.focus(); }
function clearSelectedTool() { selectedTool=null; renderAttachments(); }
function pickAttachment(kind) {
  if (sending) return;
  const picker = document.getElementById('attachment-input'); picker.dataset.kind=kind;
  picker.multiple=true; picker.webkitdirectory=kind === 'directory';
  picker.accept = kind === 'directory' ? '' : kind === 'document' ? '.txt,.md,.json,.pdf,.csv,.py,.js,.jsx,.ts,.tsx,.rs,.html,.css,.toml,.yaml,.yml,.sql' : `${kind}/*`;
  picker.click(); document.getElementById('attach-menu').classList.remove('open');
}
function renderAttachments() {
  const target=document.getElementById('attachment-list'); target.replaceChildren();
  if (selectedTool) {
    const chip=node('div','attachment-chip tool',toolLabels[selectedTool] || selectedTool);
    const remove=node('button',null,'×'); remove.onclick=clearSelectedTool; remove.setAttribute('aria-label','Remover ferramenta'); chip.append(remove); target.append(chip);
  }
  attachments.forEach((item,index) => {
    const chip=node('div','attachment-chip');
    if (item.url) { const preview=node('img','attachment-preview'); preview.src=item.url; preview.alt='Prévia do anexo'; chip.append(preview); }
    const label=node('span',null,`${item.name} · ${item.status}`); label.title=label.textContent; chip.append(label);
    const remove=node('button',null,'×'); remove.setAttribute('aria-label',`Remover ${item.name}`); remove.onclick=()=>removeAttachment(index); chip.append(remove); target.append(chip);
  });
  refreshControls();
}
function removeAttachment(index) { if (sending) return; const item=attachments.splice(index,1)[0]; if(item?.url) URL.revokeObjectURL(item.url); renderAttachments(); }
function handleAttachments(event) {
  const files=Array.from(event.target.files || []), kind=event.target.dataset.kind || 'document';
  if (kind === 'directory' && files.length) addDirectoryAttachment(files); else files.forEach(file=>addAttachment(file,kind));
  event.target.value=''; event.target.webkitdirectory=false;
}
function prepareAttachment(files, kind, name) {
  if (sending) return;
  if (attachments.length >= 8) return notice('Use no máximo oito anexos por mensagem.');
  const item={kind,name,state:'reading',status:'Lendo…',url:kind==='image'?URL.createObjectURL(files[0]):null};
  attachments.push(item);
  item.ready=ChatCore.prepareFiles(files,(read,total)=> { item.status=`Lendo ${read}/${total}`; renderAttachments(); })
    .then(result=> { item.data={kind,name,...result}; item.state='ready'; item.status=result.files.length ? `${result.files.length} lidos · ${result.omitted} omitidos` : 'Conteúdo não interpretável neste fluxo'; })
    .catch(()=> { item.state='error'; item.status='Falha na leitura; remova e tente novamente'; })
    .finally(renderAttachments);
  renderAttachments();
}
function addDirectoryAttachment(files) { prepareAttachment(files,'directory',`${(files[0].webkitRelativePath || files[0].name).split('/')[0]}/`); }
function addAttachment(file,kind) { prepareAttachment([file],kind,file.name || 'Anexo'); }
input.addEventListener('paste', event=> {
  const files=Array.from(event.clipboardData?.files || []); if (!files.length) return;
  event.preventDefault(); files.forEach(file=>addAttachment(file,file.type.startsWith('image/')?'image':file.type.startsWith('audio/')?'audio':file.type.startsWith('video/')?'video':'document'));
});
document.addEventListener('click',event=> { if (!event.target.closest('.attach-wrap')) document.getElementById('attach-menu').classList.remove('open'); });
document.addEventListener('keydown',event=> { if(event.key==='Escape') document.getElementById('attach-menu').classList.remove('open'); });
function safeUrl(value) { try { const url=new URL(value); return ['http:','https:'].includes(url.protocol) ? url.href : null; } catch (_) { return null; } }
function sourceCard(card) {
  const item=node('div','source'), url=safeUrl(card.url);
  const title=node(url?'a':'span',null,`[${card.source_id || 'fonte'}] ${card.title || 'Fonte'}`);
  if(url) { title.href=url; title.target='_blank'; title.rel='noopener noreferrer'; }
  item.append(title,node('small',null,card.snippet || (card.text || '').slice(0,360)));
  if(url && !card.text) { const analyze=node('button','analyze','Analisar aqui'); analyze.onclick=()=>call('open_page',{url,source_id:card.source_id}); item.append(analyze); }
  if(url) { const external=node('a','external','Abrir no navegador ↗'); external.href=url; external.target='_blank'; external.rel='noopener noreferrer'; item.append(external); }
  return item;
}
function makeCodeArtifact(path, oldText='', newText='', status='observed') {
  const oldLines=String(oldText).split('\n'), newLines=String(newText).split('\n');
  const lines=[], max=Math.max(oldLines.length,newLines.length);
  for(let i=0;i<max;i++) {
    if(oldLines[i]===newLines[i]) lines.push({kind:'context',old_line:i+1,new_line:i+1,text:oldLines[i] ?? ''});
    else {
      if(oldLines[i]!==undefined) lines.push({kind:'remove',old_line:i+1,new_line:null,text:oldLines[i]});
      if(newLines[i]!==undefined) lines.push({kind:'add',old_line:null,new_line:i+1,text:newLines[i]});
    }
  }
  const changed=lines.filter(line=>line.kind!=='context').length;
  return {kind:'code',path,language:(path.match(/\.([a-z0-9]+)$/i)?.[1] || 'text').toLowerCase(),status,bytes_before:String(oldText).length,bytes_after:String(newText).length,lines_after:newLines.length,diff:{format:'line-v1',changed,lines,truncated:false}};
}
function artifactDiffText(artifact) {
  const lines=artifact?.diff?.lines || [];
  if(lines.length) return lines.map(line=>`${line.kind==='add'?'+':line.kind==='remove'?'-':' '} ${line.text ?? ''}`).join('\n');
  if(artifact?.content) return String(artifact.content);
  return 'Nenhum detalhe textual foi retornado por esta ferramenta.';
}
function copyArtifactDiff(button,artifact) {
  navigator.clipboard.writeText(artifactDiffText(artifact)).then(()=>{
    const original=button.textContent; button.textContent='Copiado';
    setTimeout(()=>{ button.textContent=original; },1400);
  }).catch(()=>notice('Não foi possível copiar o artefato.'));
}
function openArtifactWorkspace(artifact) {
  if(!artifact?.path) return;
  openTool('workspace'); setTimeout(()=>runPanelRead(artifact.path),0);
}
function openArtifactOverlay(artifact) {
  const overlay=node('div','code-overlay'), card=node('section','code-overlay-card'), head=node('div','code-overlay-head'), title=node('div');
  title.append(node('div','code-overlay-title',`${artifact.kind==='diagnostics'?'Diagnóstico':'Diff'} · ${artifact.path || artifact.check || 'operação'}`),node('div','code-overlay-subtitle',artifact.status || 'detalhes estruturados'));
  const actions=node('div','code-overlay-actions'), copy=node('button',null,'Copiar'); copy.onclick=()=>copyArtifactDiff(copy,artifact);
  const close=node('button',null,'Fechar'); close.onclick=()=>overlay.remove(); actions.append(copy,close); head.append(title,actions);
  const body=node('pre','code-overlay-pre'); body.textContent=artifact.kind==='diagnostics' ? `${artifact.summary || ''}\n\n${artifact.stdout || ''}\n${artifact.stderr || ''}`.trim() : artifactDiffText(artifact);
  card.append(head,body); overlay.append(card); overlay.onclick=event=>{ if(event.target===overlay) overlay.remove(); };
  document.body.append(overlay); close.focus();
}
function renderArtifact(artifact) {
  if(!artifact) return null;
  const card=node('section',artifact.kind==='diagnostics'?'artifact-card diagnostic-card':'artifact-card');
  const head=node('div','artifact-head'), title=node('div','artifact-title');
  title.append(node('strong',null,artifact.kind==='diagnostics'?'Diagnóstico de verificação':`Artefato · ${artifact.path || 'arquivo'}`));
  title.append(node('small',null,artifact.status || artifact.language || 'resultado estruturado'));
  const actions=node('div','artifact-actions');
  if(artifact.path) { const workspace=node('button',null,'Workspace'); workspace.onclick=()=>openArtifactWorkspace(artifact); actions.append(workspace); }
  const focus=node('button',null,'Foco'); focus.onclick=()=>openArtifactOverlay(artifact); actions.append(focus);
  const copy=node('button',null,'Copiar'); copy.onclick=()=>copyArtifactDiff(copy,artifact); actions.append(copy);
  head.append(title,actions); card.append(head);
  if(artifact.kind==='diagnostics') {
    card.append(node('div','diagnostic-summary',artifact.summary || `${artifact.check || 'verificação'} · ${artifact.passed?'passou':'falhou'}`));
    const output=[artifact.stdout,artifact.stderr].filter(Boolean).join('\n');
    if(output) card.append(node('pre','diagnostic-output',output));
    return card;
  }
  const diff=artifact.diff || {changed:0,lines:String(artifact.content || '').split('\n').map((text,index)=>({kind:'context',old_line:index+1,new_line:index+1,text})),truncated:false};
  const lines=diff.lines || [];
  const summary=node('div','artifact-summary',`${artifact.language || 'text'} · ${diff?.changed ?? 0} alteração(ões) · ${artifact.lines_after || 0} linha(s) resultantes`); card.append(summary);
  const viewport=node('div','artifact-diff');
  for(const line of lines.slice(0,240)) {
    const row=node('div',`diff-line ${line.kind || 'context'}`);
    const number=line.kind==='remove'?line.old_line:line.new_line;
    row.append(node('span','diff-sign',line.kind==='add'?'+':line.kind==='remove'?'-':' '),node('span','diff-number',number == null ? '' : String(number)),node('code',null,line.text ?? '')); viewport.append(row);
  }
  if(lines.length>240) viewport.append(node('div','artifact-more',`Mais ${lines.length-240} linha(s) no foco expandido.`));
  if(lines.length) card.append(viewport); else card.append(node('div','artifact-empty','Nenhuma alteração textual detectada.'));
  return card;
}
function renderResponseContract(contract) {
  if (!contract || contract.schema !== 'agent-response/v1') return null;
  const card=node('div',`response-contract ${contract.status || 'complete'}`);
  const head=node('div','response-contract-head');
  head.append(node('strong',null,`Estado: ${contract.status || 'complete'}`));
  if(contract.data?.backend) head.append(node('span',null,contract.data.backend));
  card.append(head);
  for(const warning of contract.warnings || []) card.append(node('div','response-contract-warning',`Aviso: ${warning}`));
  if(contract.data?.context?.schema) card.append(node('div','response-contract-detail',`Contexto: ${contract.data.context.schema} · ${contract.data.context.status || 'ready'}`));
  if(contract.data?.workflow?.strategy) card.append(node('div','response-contract-detail',`Estratégia: ${contract.data.workflow.strategy}`));
  return card;
}
function addMessage(kind,text,meta='',cards=[],record=true,preview=null,artifact=null,contract=null) {
  document.getElementById('welcome')?.remove();
  if(record) timeline.push({kind,text,meta,cards,preview,artifact,contract});
  const row=node('div',`message ${kind}`), body=node('div');
  const bubble=node('div','bubble');
  if(kind==='assistant' && typeof ChatCore!=='undefined' && ChatCore.formatMarkdown) {
    bubble.innerHTML=ChatCore.formatMarkdown(text);
  } else {
    bubble.textContent=text;
  }
  body.append(bubble);
  if(cards?.length) { const sources=node('div','sources'); cards.forEach(card=>sources.append(sourceCard(card))); body.append(sources); }
  if(preview?.html) {
    const previewCard=node('div','preview-card');
    const head=node('div','preview-head');
    const caption=node('div','preview-caption',`Prévia local · ${preview.path || 'arquivo HTML'}`);
    const actions=node('div','preview-actions');
    const workspaceButton=node('button','preview-action','Abrir projeto'); workspaceButton.onclick=()=>openTool('workspace');
    const fileButton=node('button','preview-action','Ler arquivo'); fileButton.onclick=()=>{ openTool('workspace'); setTimeout(()=>runPanelRead(preview.path),0); };
    actions.append(workspaceButton,fileButton); head.append(caption,actions);
    const frame=node('iframe','web-preview'); frame.setAttribute('sandbox','allow-scripts'); frame.setAttribute('title',`Prévia de ${preview.path || 'página HTML'}`); frame.srcdoc=preview.html;
    previewCard.append(head,frame); body.append(previewCard);
  }
  const artifactCard=renderArtifact(artifact); if(artifactCard) body.append(artifactCard);
  const contractCard=renderResponseContract(contract); if(contractCard) body.append(contractCard);
  body.append(node('div','meta',meta)); row.append(node('div','avatar',kind==='user'?'Você':'✦'),body); chat.append(row); scrollChat();
}
function assistantReply(text,meta,cards=[],preview=null,artifact=null,contract=null) { conversation.push({role:'assistant',content:text,artifact:artifact || undefined,contract:contract || undefined}); addMessage('assistant',text,meta,cards,true,preview,artifact,contract); saveConversation(); }
function withPlannerMeta(meta, confidenceValue, fallbackLabel = 'Plano') {
  if (confidenceValue == null || Number.isNaN(Number(confidenceValue))) return meta || fallbackLabel;
  const info = ChatCore.confidenceLabel(confidenceValue);
  const label = `${fallbackLabel} · conf. ${info.label} (${info.score.toFixed(2)})`;
  return meta ? `${label} · ${meta}` : label;
}
function responseSignal(data) {
  const signals = [];
  if (data?.agent?.recovery) signals.push(data.agent.recovery === 'next-source' ? 'Recuperação ativa' : 'Recuperação');
  if (data?.backend === 'quality-gate') signals.push('Contexto limitado');
  if (data?.evidence?.status === 'provisional') signals.push('Fonte não corroborada');
  if (data?.evidence?.status === 'corroborated') signals.push('Fontes independentes');
  if (data?.evidence?.status === 'unverified') signals.push('Não verificado');
  if (data?.skill?.status) signals.push(`Competência: ${data.skill.status}`);
  if (data?.evidence?.fiction_warnings?.length) signals.push('Sinal de ficção');
  const omitted = Number(data?.data?.coverage?.omitted || 0);
  if (omitted > 0) signals.push(`${omitted} arquivo(s) fora da leitura`);
  const workflowPhase = data?.workflow?.phase;
  if (workflowPhase === 'plan') signals.push('Workflow: planejando');
  if (workflowPhase === 'abstain') signals.push('Workflow: aguardando evidência');
  return signals.length ? ` · ${signals.join(' · ')}` : '';
}
function responseMeta(meta, data, confidenceValue, fallbackLabel) {
  return withPlannerMeta(`${meta}${responseSignal(data)}`, confidenceValue, fallbackLabel);
}
function actionIntro(tool, args={}) {
  const project = localStorage.getItem('ia-local-zero-workspace');
  const target = project ? ` no projeto ativo (${project.split('/').pop() || project})` : '';
  const messages = {
    search_web: 'Vou pesquisar fontes atuais para responder com base em evidências.',
    research_web: 'Vou fazer uma pesquisa mais completa, abrir as fontes relevantes e sintetizar o resultado.',
    inspect_project: `Vou analisar a estrutura do projeto${target} antes de sugerir qualquer mudança.`,
    project_checks: 'Vou verificar o projeto e explicar claramente o que for encontrado.',
    create_web_page: `Entendi. Vou criar essa página${target} e mostrar uma prévia do resultado.`,
    create_file: `Entendi. Vou criar o arquivo solicitado${target}.`,
    edit_file: `Entendi. Vou editar o arquivo solicitado${target} preservando o restante do projeto.`,
    create_directory: `Entendi. Vou criar a pasta solicitada${target}.`,
    read_file: 'Vou ler o arquivo solicitado e destacar o que importa.',
    list_files: `Vou explorar o projeto${target} e mostrar a estrutura relevante.`,
    search_files: `Vou procurar no código${target} e trazer apenas as ocorrências úteis.`
  };
  return messages[tool] || `Entendi. Vou executar essa etapa${target} e explicar o resultado.`;
}
function toolNeedsApproval(request) {
  if (!request || typeof request !== 'object') return false;
  if (request.requires_approval === true) return true;
  return ['create_web_page','create_file','edit_file','apply_repair','create_directory'].includes(request.tool);
}

    const panel = document.getElementById('tool-panel');
    const toolNames = {
      search: ['Pesquisar na internet', 'Consulte fontes externas e mantenha os resultados nesta área.'],
      url: ['Analisar uma página', 'Abra uma URL, extraia o texto e registre a fonte para esta sessão.'],
      workspace: ['Explorar workspace', 'Escolha qualquer diretório local existente para navegar e editar.'],
      code: ['Buscar no código', 'Encontre ocorrências em arquivos pequenos do workspace.'],
      sources: ['Fontes desta sessão', 'Revise, abra e acompanhe as fontes já coletadas.'],
      media: ['Multimídia', 'Packs locais para documentos, imagens, áudio e vídeo.']
    };
    function escapeHtml(value) { return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c])); }
    function panelShell(name, form, body='') { const [title, description] = toolNames[name]; panel.innerHTML = `<div class="panel-card"><div class="panel-head"><div><div class="panel-title">${title}</div><div class="panel-description">${description}</div></div><button class="panel-close" onclick="closeTool()">×</button></div>${form}<div class="panel-result" id="panel-result">${body}</div></div>`; panel.classList.add('open'); document.querySelectorAll('.side-item').forEach(item => item.classList.toggle('active', item.dataset.tool === name)); }
    function closeTool() { panel.classList.remove('open'); panel.innerHTML = ''; document.querySelectorAll('.side-item').forEach(item => item.classList.remove('active')); }
    function openTool(name) {
      if (name === 'search') panelShell(name, `<div class="panel-form"><input id="panel-input" placeholder="Ex.: novidades do Rust" autofocus><button onclick="runPanelSearch()">Pesquisar</button></div>`);
      else if (name === 'url') panelShell(name, `<div class="panel-form"><input id="panel-input" placeholder="https://exemplo.com" autofocus><button onclick="runPanelUrl()">Analisar</button></div>`);
      else if (name === 'workspace') panelShell(name, `<div class="panel-form"><button onclick="choosePanelWorkspace()">▣ Escolher pasta do projeto</button><button onclick="createPanelWorkspace()">＋ Criar projeto</button></div><div class="panel-form"><input id="panel-input" placeholder="ou informe um caminho absoluto" autofocus><button onclick="runPanelWorkspace()">Abrir</button></div><div class="panel-form"><button class="panel-action" onclick="showCreateFile()">＋ Novo arquivo</button><button class="panel-action" onclick="showCreateDirectory()">＋ Nova pasta</button></div><div class="panel-description" style="margin-top:10px">Escolha uma pasta do usuário pelo gerenciador de arquivos. O diretório do IA Local do Zero não precisa ser usado como workspace.</div>`);
      else if (name === 'code') panelShell(name, `<div class="panel-form"><input id="panel-input" placeholder="termo ou trecho de código" autofocus><button onclick="runPanelCode()">Buscar</button></div>`);
      else if (name === 'sources') { panelShell(name, '', '<div class="panel-empty">Carregando fontes…</div>'); runPanelSources(); }
      else if (name === 'media') panelShell(name, `<div class="panel-form"><input id="media-path" placeholder="caminho local da mídia ou documento"><button onclick="inspectPanelMedia()">Inspecionar</button><button onclick="extractPanelDocument()">Extrair PDF</button></div>`, '<div class="entry"><div>▧ Documentos<small>PDF, TXT, Markdown e JSON · ativo</small></div></div><div class="entry"><div>▧ Visão<small>Imagens · adaptador pronto para backend visual</small></div></div><div class="entry"><div>◉ Áudio<small>WAV, MP3, OGG, FLAC · adaptador pronto</small></div></div><div class="entry"><div>▣ Vídeo<small>MP4, MKV, WebM, MOV · adaptador pronto</small></div></div><div class="entry"><div>✦ Geração<small>Imagem, áudio e vídeo · planejada</small></div></div>');
      document.getElementById('panel-input')?.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); ({search:runPanelSearch,url:runPanelUrl,workspace:runPanelWorkspace,code:runPanelCode}[name] || (()=>{}))(); } });
    }
function renderPanelSources(cards) {
  const target=document.getElementById('panel-result'); if(!target) return;
  target.replaceChildren();
  for(const card of cards || []) {
    const element=sourceCard(card), analyze=element.querySelector('.analyze');
    if(analyze) analyze.onclick=()=>runPanelOpen(card.source_id,card.url);
    target.append(element);
  }
  if(!target.children.length) target.append(node('div','panel-empty','Nenhuma fonte nesta sessão.'));
}
    async function runPanelSearch() { const query = document.getElementById('panel-input')?.value.trim(); if (!query) return; const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-empty">Pesquisando…</div>'; const data = await toolRequest('search_web', {query}); if (!data.ok) { target.textContent = data.error; return; } renderPanelSources(data.data.results); }
    async function runPanelUrl() { const url = document.getElementById('panel-input')?.value.trim(); if (!url) return; const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-empty">Abrindo e extraindo texto…</div>'; const data = await toolRequest('open_page', {url}); if (!data.ok) { target.textContent = data.error; return; } target.innerHTML = `<div class="source"><a href="${escapeHtml(data.data.url || url)}" target="_blank" rel="noreferrer">${escapeHtml(data.data.title || 'Página analisada')}</a><small>${escapeHtml((data.data.text || '').slice(0, 1800))}</small></div>`; }
async function runPanelWorkspace(path) {
  let value=typeof path==='string'?path:(document.getElementById('panel-input')?.value.trim() || '');
  const target=document.getElementById('panel-result'); if(!target) return;
  target.textContent='Lendo workspace…';
  if(value.startsWith('/')) { const selected=await toolRequest('set_workspace',{path:value}); if(!selected.ok) { target.textContent=selected.error; return; } value=''; }
  const data=await toolRequest('list_files',{path:value}); if(!data.ok) { target.textContent=data.error; return; }
  setWorkspaceIndicator(data.data.workspace);
  const dockFiles=document.getElementById('dock-files');
  if(dockFiles && !value) {
    dockFiles.replaceChildren();
    for(const entry of data.data.entries.slice(0,80)) {
      const row=node('div','entry'), label=node('div',null,`${entry.kind==='directory'?'▣':'▤'} ${entry.name}`);
      const button=node('button',null,entry.kind==='directory'?'›':'Abrir');
      const relative=entry.name; button.onclick=()=>entry.kind==='directory'?openDockFolder(relative):runPanelRead(relative);
      label.onclick=button.onclick; label.style.cursor='pointer';
      row.append(label,button); dockFiles.append(row);
    }
    if(!dockFiles.children.length) dockFiles.append(node('div','dock-empty','Projeto vazio. Peça ao assistente para começar um projeto ou crie um arquivo.'));
  }
  target.replaceChildren(node('div','panel-description',`Projeto ativo: ${data.data.workspace}`));
  if(value) { const up=node('button','panel-action','↑ Pasta anterior'); up.onclick=()=>runPanelWorkspace(value.split('/').slice(0,-1).join('/')); target.append(up); }
  for(const entry of data.data.entries) {
    const row=node('div','entry'), label=node('div',null,`${entry.kind==='directory'?'▣':'▤'} ${entry.name}`);
    label.append(node('small',null,`${entry.kind} · ${entry.bytes} bytes`));
    const button=node('button',null,entry.kind==='directory'?'Abrir':'Ler');
    const relative=(value?value+'/':'')+entry.name;
    button.onclick=()=>entry.kind==='directory'?openDockFolder(relative):runPanelRead(relative);
    row.append(label,button); target.append(row);
  }
  if(!data.data.entries.length) target.append(node('div','panel-empty','Diretório vazio.'));
}
async function choosePanelWorkspace() {
  const target=document.getElementById('panel-result'); if(target) target.textContent='Abrindo o gerenciador de arquivos…';
  try {
    const response=await fetch('/api/choose-directory',{cache:'no-store'}), data=await response.json();
    if(data.ok) { const input=document.getElementById('panel-input'); if(input) input.value=data.path; await runPanelWorkspace(data.path); }
    else if(!data.cancelled && target) target.textContent=data.error || 'Não foi possível escolher a pasta.';
    else if(target) target.textContent='Seleção cancelada.';
  } catch(error) { if(target) target.textContent=`Não foi possível abrir o gerenciador: ${error.message}`; }
}
async function createPanelWorkspace() {
  const value=document.getElementById('panel-input')?.value.trim(), target=document.getElementById('panel-result');
  if(!value || !value.startsWith('/')) { if(target) target.textContent='Informe um caminho absoluto para o novo projeto.'; return; }
  const data=await toolRequest('create_workspace',{path:value});
  if(!data.ok) { if(target) target.textContent=data.error; return; }
  setWorkspaceIndicator(data.data.workspace);
  await runPanelWorkspace('');
}
    function showCreateFile() { const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-form"><input id="create-file-path" placeholder="caminho, ex.: app/main.py"><textarea id="create-file-content" placeholder="conteúdo inicial"></textarea><button onclick="runCreateFile()">Criar</button></div>'; }
    function showCreateDirectory() { const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-form"><input id="create-directory-path" placeholder="caminho, ex.: app/src"><button onclick="runCreateDirectory()">Criar pasta</button></div>'; }
    function confirmMutation(action, path, signal=null) {
      return new Promise(resolve => {
        const card=node('div','inline-confirm');
        const copy=node('div'); copy.append(node('div','inline-confirm-title',action),node('div','inline-confirm-path',path || 'Esta ação irá alterar o projeto ativo.'),node('div','inline-confirm-note','A alteração só será executada depois da sua confirmação.'));
        const actions=node('div','inline-confirm-actions'); const cancel=node('button','confirm-cancel','Cancelar'); const accept=node('button','confirm-accept','Confirmar');
        const abort=()=>finish(false);
        const finish=value=>{ signal?.removeEventListener('abort',abort); card.remove(); resolve(value); };
        cancel.onclick=()=>finish(false); accept.onclick=()=>finish(true); actions.append(cancel,accept); card.append(copy,actions); chat.append(card); scrollChat();
        signal?.addEventListener('abort',abort,{once:true});
        if(signal?.aborted) finish(false);
      });
    }
    async function runCreateFile() { const path = document.getElementById('create-file-path')?.value.trim(); const content = document.getElementById('create-file-content')?.value || ''; if (!path || !(await confirmMutation('Criar este arquivo?', path))) return; const data = await toolRequest('create_file', {path, content}); document.getElementById('panel-result').innerHTML = data.ok ? `<div class="panel-empty">Arquivo criado: ${escapeHtml(data.data.path)}</div>` : `<div class="panel-empty">${escapeHtml(data.error)}</div>`; }
    async function runCreateDirectory() { const path = document.getElementById('create-directory-path')?.value.trim(); if (!path || !(await confirmMutation('Criar esta pasta?', path))) return; const data = await toolRequest('create_directory', {path}); document.getElementById('panel-result').innerHTML = data.ok ? `<div class="panel-empty">Pasta criada: ${escapeHtml(data.data.path)}</div>` : `<div class="panel-empty">${escapeHtml(data.error)}</div>`; }
async function runPanelRead(path) {
  const target=document.getElementById('panel-result');
  if(target) target.textContent='Lendo arquivo…';
  const data=await toolRequest('read_file',{path});
  if(!data.ok) { if(target) target.textContent=data.error; else notice(data.error); return; }
  setEditorFile(data.data);
  if(target) target.replaceChildren(node('strong',null,data.data.path),node('pre',null,data.data.content));
}
async function runPanelCode() {
  const query=document.getElementById('panel-input')?.value.trim(), target=document.getElementById('panel-result'); if(!query || !target) return;
  target.textContent='Buscando no código…'; const data=await toolRequest('search_files',{query});
  if(!data.ok) { target.textContent=data.error; return; }
  target.replaceChildren();
  for(const match of data.data.matches) {
    const row=node('div','entry'), label=node('div',null,match.path); label.append(node('small',null,`linha ${match.line} · ${match.text}`));
    const button=node('button',null,'Ler'); button.onclick=()=>runPanelRead(match.path); row.append(label,button); target.append(row);
  }
  if(!data.data.matches.length) target.append(node('div','panel-empty','Nenhuma ocorrência encontrada.'));
  if(data.data.truncated) target.append(node('div','panel-description','Busca parcial: o limite de leitura foi atingido.'));
}
    async function runPanelSources() { const data = await toolRequest('list_sources', {}); if (data.ok) renderPanelSources(data.data.sources); else document.getElementById('panel-result').textContent = data.error; }
    async function runPanelOpen(source_id, url) { const data = await toolRequest('open_page', {source_id, url}); if (data.ok) renderPanelSources([data.data]); }
    async function inspectPanelMedia() { const path = document.getElementById('media-path')?.value.trim(); if (!path) return; const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-empty">Inspecionando mídia…</div>'; const data = await toolRequest('inspect_media', {path}); if (!data.ok) { target.textContent = data.error; return; } target.innerHTML = `<div class="entry"><div>${escapeHtml(data.data.path)}<small>${escapeHtml(data.data.media_type)} · ${data.data.extension || 'sem extensão'} · ${data.data.bytes} bytes</small></div></div>`; }
    async function extractPanelDocument() { const path = document.getElementById('media-path')?.value.trim(); if (!path) return; const target = document.getElementById('panel-result'); target.innerHTML = '<div class="panel-empty">Extraindo documento…</div>'; const data = await toolRequest('extract_document_text', {path}); if (!data.ok) { target.textContent = data.error; return; } target.innerHTML = `<div class="source"><strong>${escapeHtml(data.data.path)}</strong><small>${escapeHtml(data.data.text.slice(0, 6000))}</small></div>`; }
async function toolRequest(tool, arguments_, showInChat=false) {
  const id=requestId('tool');
  return requestWithActivity('/api/tool-call',{tool,arguments:arguments_,request_id:id},`tool:${tool}:${id}`,toolLabels[tool] || tool,showInChat);
}
async function call(tool,arguments_) {
  if(liveActions.size) return notice('Aguarde a operação atual terminar.');
  const data=await toolRequest(tool,arguments_,true);
  data.call_fingerprint=JSON.stringify([tool,arguments_ || {}]);
  if(!data.ok) { assistantReply(data.error, 'Operação não concluída'); return data; }
  const confidence = Number((data && data.tool_call && data.tool_call.planner && data.tool_call.planner.confidence) || data?.confidence || 0);
  const meta = responseMeta(`${toolLabels[tool] || tool} · ${data.elapsed_ms} ms`, data, confidence, toolLabels[tool] || tool);
  if(tool==='search_web') assistantReply(`Encontrei ${data.data.result_count} resultado(s) para “${data.data.query}”.`,meta,data.data.results);
  else if(tool==='research_web') {
    const pages=(data.data.pages || []).filter(page => page.text);
    const saved=data.data.saved_to_corpus ? `\n\nLote salvo em ${data.data.saved_to_corpus}.` : '';
    const savedCount=data.data.saved_count != null ? ` ${data.data.saved_count} registro(s) novo(s) foram gravados.` : '';
    const answer=data.data.answer || 'As fontes foram consultadas, mas não houve síntese disponível.';
    assistantReply(pages.length ? `${answer}\n\nPesquisa: ${pages.length} fonte(s) com conteúdo em ${data.data.elapsed_ms} ms.${saved}${savedCount}` : 'A pesquisa não retornou conteúdo verificável. Esta etapa não comprova a conclusão da tarefa.',meta,pages);
  }
  else if(tool==='inspect_project') {
    const d=data.data, list=(items,empty='nenhum identificado') => (items||[]).join(', ') || empty;
    const next=(d.signals||[]).length ? `\n\nPontos para revisar:\n- ${d.signals.join('\n- ')}` : '\n\nA estrutura básica foi reconhecida; o próximo passo é ler os arquivos relevantes e executar os testes.';
    assistantReply(`${d.summary}\n\nManifestos: ${list(d.manifests)}\nEntradas: ${list(d.entrypoints)}\nTestes: ${list(d.test_files)}\nVerificações disponíveis: ${list(d.checks)}${next}`,meta);
  }
  else if(tool==='diagnose_project') {
    const d=data.data, evidence=(d.evidence||[]).map(item=>`- ${item}`).join('\n'), steps=(d.next_steps||[]).map(item=>`- ${item}`).join('\n');
    const diagnostic={kind:'diagnostics',status:d.passed?'passed':'failed',check:d.check,passed:d.passed,summary:d.summary,evidence:d.evidence||[],stdout:d.stdout||'',stderr:d.stderr||''};
    assistantReply(`${d.summary||'Falha classificada.'}${evidence?`\n\nEvidências:\n${evidence}`:''}${steps?`\n\nPróximos passos:\n${steps}`:''}`,meta,[],null,diagnostic);
  }
  else if(tool==='read_file') {
    const d=data.data, artifact={kind:'code',path:d.path,status:'observed',language:(d.path?.match(/\.([a-z0-9]+)$/i)?.[1] || 'text').toLowerCase(),content:(d.content||'').slice(0,65536),lines_after:(d.content||'').split('\n').length};
    assistantReply(`Li ${d.path}. O conteúdo foi anexado como artefato revisável no histórico.`,meta,[],null,artifact);
  }
  else if(tool==='edit_file') {
    const d=data.data;
    assistantReply(`Edição aplicada em ${d.path}. O diff e o backup ficaram registrados na timeline.`,meta,[],null,d.artifact);
  }
  else if(tool==='create_file') {
    const d=data.data;
    assistantReply(`Criei ${d.path}. O conteúdo inicial e o artefato ficaram registrados na timeline.`,meta,[],null,d.artifact);
  }
  else if(tool==='propose_repair') {
    const d=data.data;
    assistantReply(`Proposta validada para ${d.path}; nenhum arquivo foi alterado.\n\nMotivo: ${d.reason}\n\nRevise o diff estruturado e aplique com a ação de correção explícita.`,meta,[],null,d.artifact);
  }
  else if(tool==='apply_repair') {
    const d=data.data, reason=d.repair?.reason || 'não informado';
    assistantReply(`Correção aplicada em ${d.path}.\n\nMotivo: ${reason}\nBackup: ${d.backup || 'registrado pelo runtime'}\n\nA verificação do projeto será executada agora.`,meta,[],null,d.artifact);
  }
  else if(tool==='create_web_page') {
    const d=data.data;
    assistantReply(`Criei a página HTML em ${d.path}.\n\nO arquivo está no workspace e a prévia abaixo está isolada em sandbox.`,meta,[],{html:d.preview_html,path:d.path});
  }
  else if(tool==='project_checks') {
    const d=data.data || {};
    if (d.executed === false && !(d.available||[]).length) assistantReply('Ainda não há testes ou verificações configurados neste projeto. Isso não significa que a alteração esteja quebrada; significa apenas que não existe um comando de validação reconhecido para executar.',meta);
    else if (d.passed === true) assistantReply('As verificações disponíveis foram executadas e passaram.',meta);
    else {
      const diagnostic={kind:'diagnostics',status:'failed',check:d.check || 'auto',passed:false,summary:d.message || 'As verificações foram executadas, mas uma falha foi encontrada.',stdout:d.stdout||'',stderr:d.stderr||'',evidence:d.evidence||[]};
      assistantReply(`As verificações foram executadas, mas encontrei uma falha.${d.message ? `\n\n${d.message}` : ''}`,meta,[],null,diagnostic);
    }
  }
  else if(tool==='list_sources') assistantReply(`${data.data.sources.length} fonte(s) nesta sessão.`,meta,data.data.sources);
  else if(tool==='open_page') assistantReply(`${data.data.title || 'Página lida'}\n\n${(data.data.text || '').slice(0,1800)}`,meta,[data.data]);
  else assistantReply(data.data?.summary || data.data?.message || 'A operação foi concluída. Consulte os detalhes da atividade acima para ver o resultado técnico.',meta);
  return data;
}
function toolResultForAgent(tool, result, traceId=null) {
  const data = result?.data ? JSON.parse(JSON.stringify(result.data)) : null;
  if (data && typeof data.preview_html === 'string') delete data.preview_html;
  return {tool, ok: Boolean(result?.ok), data, error: result?.error || null, call_fingerprint:result?.call_fingerprint || null, trace_id: traceId || result?.trace_id || null};
}
async function continueAgent(tool, result, depth=0, traceId=null) {
  conversation.push({role:'tool', tool, content:JSON.stringify(toolResultForAgent(tool,result,traceId))});
  saveConversation();
  if (depth >= 11) {
    assistantReply('Atingi o limite de etapas desta tarefa. O resultado das ações executadas permanece registrado acima.', 'Agente local · limite de segurança');
    return result;
  }
  const id=requestId('agent-step');
  const data=await requestWithActivity('/api/chat',{messages:requestMessages(),request_id:id},`chat:${id}`,'Decidindo a próxima etapa',true);
  if(!data.ok) { assistantReply(data.error,'Continuação indisponível'); return data; }
  if(data.tool_call?.tool) {
    const request=data.tool_call;
    const currentTurn=conversation.slice(conversation.findLastIndex(item=>item.role==='user')+1);
    const observations=currentTurn.filter(item=>item.role==='tool').map(item=>{try{return JSON.parse(item.content);}catch{return {};}});
    const fingerprint=JSON.stringify([request.tool,request.arguments || {}]);
    if(observations.some(item=>item.call_fingerprint===fingerprint)) {
      assistantReply('O planejador repetiu uma chamada já executada com os mesmos argumentos. A execução foi interrompida para revisar o plano.', 'Agente · bloqueado por repetição');
      setLiveStatus({message:'Tarefa pendente: plano repetido',done:true});
      return {ok:false,agent:{status:'blocked',stop_reason:'repeated_call'}};
    }
    const confidence = Number((request && request.planner && request.planner.confidence) || data?.confidence || 0);
    assistantReply(actionIntro(request.tool, request.arguments || {}), withPlannerMeta('Próxima etapa', confidence, 'Próxima etapa'));
    // Mantém uma janela humana entre etapas encadeadas. O trabalho continua
    // assíncrono, mas o usuário consegue ler o plano e observar a transição.
    await new Promise(resolve=>setTimeout(resolve,900));
    if(toolNeedsApproval(request)) return runChatTool(request.tool,request.arguments || {},toolLabels[request.tool] || 'Executar próxima etapa',true,depth+1,data.trace_id || traceId);
    const next=await call(request.tool,request.arguments || {});
    return continueAgent(request.tool,next,depth+1,data.trace_id || traceId);
  }
  await new Promise(resolve=>setTimeout(resolve,650));
  const confidence = Number((data && data.tool_call && data.tool_call.planner && data.tool_call.planner.confidence) || data?.confidence || 0);
  assistantReply(data.text || 'A execução terminou sem uma conclusão verificável.', responseMeta(`${backendLabels[data.backend] || 'Agente local'} · ${data.elapsed_ms || 0} ms`, data, confidence, 'Agente local'),[],null,null,data.response_contract);
  if(data.agent?.status) setLiveStatus({message:({blocked:'Tarefa pendente',failed:'Execução falhou',completed:'Etapa concluída'})[data.agent.status] || data.agent.status,done:true});
  return data;
}
async function runChatTool(tool,arguments_,label,continueLoop=false,depth=0,traceId=null) {
  if(!(await confirmMutation(label+'?',arguments_.path || ''))) { assistantReply('Tudo bem — não alterei nenhum arquivo.','Ação cancelada'); return; }
  const result=await call(tool,arguments_);
  if(result?.ok && !continueLoop && (tool==='create_file' || tool==='create_web_page' || tool==='edit_file' || tool==='apply_repair')) {
    const verification=await call('project_checks',{check:'auto',path:result?.data?.path || arguments_?.path || ''});
    if(verification?.ok && verification.data?.executed!==false && verification.data?.passed===false) await call('diagnose_project',verification.data);
  }
  if(continueLoop && result) return continueAgent(tool,result,depth,traceId);
  return result;
}
function parseRepairCommand(value) {
  const match=value.match(/^\/(propor|aplicar)\s+corre[cç][aã]o\s+(?:no\s+|para\s+o\s+|para\s+)?arquivo\s+([^\s:]+)\s*\n\s*motivo\s*:\s*\n([\s\S]*?)\n\s*antigo\s*:\s*\n([\s\S]*?)\n\s*novo\s*:\s*\n([\s\S]+)$/i);
  if(!match) return null;
  return {tool:match[1].toLowerCase()==='propor'?'propose_repair':'apply_repair',arguments:{path:match[2].replace(/`/g,''),reason:match[3].trim(),old_text:match[4],new_text:match[5]}};
}
function requestMessages() {
  // Retém até aproximadamente 16k tokens de histórico. O modelo próprio usa
  // uma janela deslizante na atenção enquanto sua arquitetura é expandida.
  const recent=conversation.slice(-128);
  const latest=recent.findLastIndex(m=>m.attachments?.length);
  return recent.map((m,index)=>({role:m.role,content:m.content.slice(0,index===recent.length-1?64000:24000),attachments:index===latest ? m.attachments : []}));
}
async function chatModel() {
  const workspace=localStorage.getItem('ia-local-zero-workspace');
  if(workspace) {
    const data=await fetch('/api/runs',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({conversation_id:currentConversationId,workspace,request_id:requestId('run'),messages:requestMessages().filter(m=>m.role!=='tool').slice(-80)})}).then(r=>r.json());
    if(!data.ok) return assistantReply(data.error,'Não foi possível iniciar a tarefa');
    agentRunRef={id:data.run.id,cursor:0}; saveConversation();
    return watchAgentRun();
  }
  const id=requestId('chat');
  const data=await requestWithActivity('/api/chat',{messages:requestMessages(),request_id:id},`chat:${id}`,'Examinando a mensagem e os anexos',true);
  if(!data.ok) return assistantReply(data.error,'Resposta indisponível');
  if(data.tool_call?.tool) {
    const request=data.tool_call;
    const confidence = Number((request && request.planner && request.planner.confidence) || data?.confidence || 0);
    assistantReply(actionIntro(request.tool, request.arguments || {}), withPlannerMeta('Plano da tarefa', confidence, 'Plano da tarefa'));
    if(toolNeedsApproval(request)) return runChatTool(request.tool,request.arguments,toolLabels[request.tool] || 'Executar ação',true,0,data.trace_id);
    const result=await call(request.tool,request.arguments || {});
    return continueAgent(request.tool,result,0,data.trace_id);
  }
  const confidence = Number((data && data.tool_call && data.tool_call.planner && data.tool_call.planner.confidence) || data?.confidence || 0);
  assistantReply(data.text || 'Nenhum texto retornado.', responseMeta(`${backendLabels[data.backend] || 'Assistente local'} · ${data.elapsed_ms} ms`, data, confidence, 'Resposta'),[],null,null,data.response_contract);
}
async function watchAgentRun() {
  if(!agentRunRef || liveActions.has(agentRunRef.id)) return;
  const ref=agentRunRef, operation=ref.id;
  startAction(operation,'Tarefa no servidor',true);
  const row=liveActions.get(operation)?.row;
  const approvalAbort=new AbortController();
  const cancel=node('button',null,'Cancelar tarefa');
  const control=async (action,call_id)=>{
    const result=await fetch(`/api/runs/${operation}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action,call_id})}).then(r=>r.json());
    if(!result.ok) throw new Error(result.error);
    return result;
  };
  cancel.onclick=()=>control('cancel').then(()=>approvalAbort.abort()).catch(error=>notice(error.message));
  row?.append(cancel);
  try {
    while(agentRunRef===ref) {
      const data=await fetch(`/api/runs/${operation}`,{cache:'no-store',signal:AbortSignal.timeout(10000)}).then(r=>r.json());
      if(!data.ok) throw new Error(data.error);
      const run=data.run;
      for(const event of run.events.filter(event=>event.seq>ref.cursor)) {
        updateAction({operation,phase:'running',message:event.text});
        if(event.plan) assistantReply(event.text+'\n\n'+event.plan.map((call,index)=>`${index+1}. ${toolLabels[call.tool] || call.tool}${call.path?' · '+call.path:''}`).join('\n'),'Plano da tarefa');
        if(event.result) {
          const result=event.result, d=result.data || {};
          const artifact=d.artifact || (result.tool==='read_file'?{kind:'code',path:d.path,content:d.content,status:'observed'}:result.tool==='project_checks'?{kind:'diagnostics',status:d.passed && d.executed?'passed':'failed',check:d.check,passed:d.passed,summary:d.message || d.command,stdout:d.stdout || '',stderr:d.stderr || ''}:null);
          const cards=result.tool==='research_web'?(d.pages || []):result.tool==='search_web'?(d.results || []):[];
          assistantReply(`${toolLabels[result.tool] || result.tool}: ${result.ok ? (d.summary || d.message || d.path || 'resultado registrado') : (result.error || 'falha')}`,'Resultado da ferramenta',cards,null,artifact);
        }
        if(event.response || ['failed','blocked','interrupted','cancelled'].includes(event.status)) assistantReply(event.text,`Tarefa · ${event.status}`);
        ref.cursor=event.seq; saveConversation();
      }
      if(['completed','blocked','failed','cancelled','interrupted'].includes(run.status)) {
        updateAction({operation,phase:run.status==='completed'?'done':'error',message:run.events.at(-1)?.text || run.status},true);
        if(run.status==='interrupted' && !run.uncertain_call) {
          const resume=node('button',null,'Retomar tarefa');
          resume.onclick=async()=>{try{await control('resume');resume.remove();await watchAgentRun();}catch(error){notice(error.message);}};
          row?.append(resume);
        }
        break;
      }
      if(run.status==='awaiting_approval') {
        const call=run.pending;
        const approved=await confirmMutation(`Autorizar ${toolLabels[call.tool] || call.tool}?`,JSON.stringify(call.arguments,null,2),approvalAbort.signal);
        await control(approved?'approve':'cancel',call.id);
      } else await new Promise(resolve=>setTimeout(resolve,700));
    }
  } catch(error) {
    updateAction({operation,phase:'error',message:`Acompanhamento indisponível: ${error.message}. A tarefa pode continuar no servidor; reabra esta conversa para consultar.`},true);
  } finally {cancel.remove();liveActions.delete(operation);refreshControls();saveConversation();}
}
async function learnTopic(topic) {
  const operation=requestId('learn');
  startAction(operation,`Aprendendo: ${topic}`,true);
  const action=liveActions.get(operation);
  action.record.progress=0;
  const bar=node('progress','learning-progress'); bar.max=100; bar.value=0;
  action.row?.querySelector('.action-body')?.insertBefore(bar,action.row.querySelector('details'));
  try {
    const started=await fetch('/api/learn',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({topic})}).then(response=>response.json());
    if(!started.ok) throw new Error(started.error || 'Não foi possível iniciar.');
    let job=started.job, seen=0;
    while(job.status==='running') {
      for(const entry of job.logs.slice(seen)) updateAction({operation,phase:'learning',message:entry.message,progress:job.progress});
      seen=job.logs.length;
      await new Promise(resolve=>setTimeout(resolve,850));
      const response=await fetch(`/api/learn/${job.id}`,{cache:'no-store'});
      const data=await response.json();
      if(!data.ok) throw new Error(data.error || 'Não foi possível consultar o progresso.');
      job=data.job;
      // O cartão de atividade é atualizado em tempo real. Evitamos remontar
      // o painel inteiro de competências a cada polling para preservar o chat.
    }
    for(const entry of job.logs.slice(seen)) updateAction({operation,phase:job.status,message:entry.message,progress:job.progress});
    updateAction({operation,phase:job.status==='completed'?'done':'error',message:job.logs.at(-1)?.message || 'Aprendizado encerrado.',progress:100},true);
    if(job.status==='completed') {
      const links=(job.sources || []).filter(source=>source.url).map(source=>`- ${source.title || source.url}: ${source.url}`).join('\n');
      const verification=job.evidence?.status==='corroborated' ? 'Fontes de domínios distintos corroboram o tema; isso não é prova absoluta de todas as afirmações.' : 'Evidência provisória: as fontes ainda não foram corroboradas de forma independente.';
      const skillStatus=job.skill?.status || 'partially_known';
      const lab=job.laboratory?.status==='verified' ? 'Laboratório: exercícios executados e verificados.' : job.laboratory?.status==='partial' ? 'Laboratório: algumas competências foram praticadas; outras ainda não têm executor local seguro.' : job.laboratory?.status==='recovered' ? 'Laboratório: houve falha, mas uma etapa de recuperação foi aprovada.' : job.laboratory?.status==='practice_unavailable' ? 'Laboratório: ainda não há executor local seguro para este domínio.' : 'Laboratório: exercício falhou mesmo após recuperação; a competência permanece parcial.';
      assistantReply(`Pesquisa de “${topic}” concluída e competência registrada como “${skillStatus}”. Foram adicionados ${job.documents_added} documento(s); o chat já pode consultar esse material, mas ainda não considera o tema dominado.\n\nVerificação: ${verification}\n${lab}\n\nFontes consultadas:\n${links || 'Nenhuma fonte registrada.'}\n\nLacunas: ${(job.gaps || job.skill?.concepts?.gaps || []).join(', ') || 'avaliação ainda incompleta'}.\n\nIsso atualiza o índice e o estado do agente; não treina os pesos do modelo.`, 'Aprendizado parcial');
      if(document.body.classList.contains('training-mode')) await loadTraining();
    } else assistantReply(`Não consegui concluir o aprendizado de “${topic}”: ${job.error || 'nenhuma fonte válida'}.`, 'Aprendizado incompleto');
  } catch(error) {
    updateAction({operation,phase:'error',message:`Aprendizado interrompido: ${error.message}`},true);
    assistantReply(`Não consegui acompanhar o aprendizado: ${error.message}`, 'Aprendizado interrompido');
  } finally { liveActions.delete(operation); refreshControls(); saveConversation(); if(document.body.classList.contains('training-mode')) loadTraining(); }
}
async function learnMaterials(urls) {
  const unique=[...new Set(urls.map(url=>url.replace(/[),.;]+$/g,'')))].slice(0,8);
  const operation=requestId('material-plan');
  startAction(operation,`Preparando trilha com ${unique.length} fontes`,true);
  const action=liveActions.get(operation);
  updateAction({operation,phase:'learning',message:`Foram identificadas ${unique.length} fontes. Vou processá-las uma por vez.`,progress:0});
  updateAction({operation,phase:'done',message:'Trilha de fontes preparada; iniciando estudo sequencial.',progress:0},true);
  liveActions.delete(operation); refreshControls();
  for (let index=0; index<unique.length; index++) {
    assistantReply(`Fonte ${index+1}/${unique.length}: vou estudar e validar esta referência antes de avançar.\n${unique[index]}`,'Trilha de treinamento');
    await learnTopic(unique[index]);
    await new Promise(resolve=>setTimeout(resolve,900));
  }
}
async function send() {
  if(sending || liveActions.size) return;
  const value=input.value.trim(); if(!value && !attachments.length) return;
  if(value.length>24000) return notice('A mensagem excede 24 mil caracteres; envie o conteúdo longo como arquivo.');
  sending=true; refreshControls();
  try {
    await Promise.all(attachments.map(item=>item.ready));
    if(attachments.some(item=>item.state!=='ready')) { notice('Um anexo não terminou de carregar. Remova-o e tente novamente.'); return; }
    const material=attachments.map(item=>item.data);
    if(material.reduce((sum,item)=>sum+item.bytes,0)>ChatCore.LIMITS.totalBytes) { notice('Os anexos ultrapassam 256 KiB de texto nesta mensagem. Envie-os em mensagens separadas.'); return; }
    const tool=ChatCore.route(value,material.length>0,selectedTool);
    conversation.push({role:'user',content:value,attachments:material});
    const visible=[value,...material.map(item=>`📎 ${item.name} · ${item.files.length} arquivo(s) lido(s) · ${item.omitted} omitido(s)`) ].filter(Boolean).join('\n');
    addMessage('user',visible); saveConversation(); input.value=''; input.style.height='auto'; clearAttachments(); selectedTool=null; renderAttachments();
    // Aceita o tema e a URL em linhas separadas. Sem [\s\S], esse comando
    // caía no chat comum e terminava em milissegundos sem iniciar o estudo.
    const learning=value.match(/^\/?(?:aprenda|estude|estudar)\s+["“]?([\s\S]+?)["”]?\s*$/i);
    const compoundLearning=learning && /\s+e\s+(?:crie|criar|implemente|implementar|construa|desenvolva|corrija|use|utilize)\b/i.test(learning[1]);
    const materialUrls=[...value.matchAll(/https?:\/\/[^\s<>"'\]]+/g)].map(match=>match[0]);
    if(document.body.classList.contains('training-mode') && materialUrls.length>=2 && !material.length) await learnMaterials(materialUrls);
    else if(learning && !compoundLearning && !material.length) await learnTopic(learning[1].trim());
    else if(tool==='search_web') await call(tool,{query:value.replace(/^\/(pesquisar|pesquisa)\s+/i,'').trim()});
    else if(tool==='research_web') {
      const save=/\s--salvar\b/i.test(value);
      const query=value.replace(/^\/pesquisa\s+(guiada|profunda)\s+/i,'').replace(/\s--salvar\b/i,'').trim();
      await call(tool,{query,max_results:2,save_to_corpus:save});
    }
    else if(tool==='inspect_project') await call(tool,{max_depth:4});
    else if(tool==='open_page') await call(tool,{url:value.replace(/^\/abrir\s+/i,'').trim()});
    else if(tool==='list_files') await call(tool,{path:value.replace(/^\/arquivos\s*/i,'').trim()});
    else if(tool==='search_files') await call(tool,{query:value.replace(/^\/buscar\s+/i,'').trim()});
    else if(value === '/ajuda' || value === '/help') {
      assistantReply(
        "### Guia rápido de comandos e ferramentas locais:\n\n" +
        "- `/analisar`: Inspeciona manifestos, pontos de entrada e testes do projeto ativo.\n" +
        "- `/buscar <termo>`: Busca ocorrências de texto no código do projeto.\n" +
        "- `/arquivos [pasta]`: Lista arquivos do workspace.\n" +
        "- `/ler <arquivo>`: Lê e exibe o conteúdo do arquivo.\n" +
        "- `/pesquisa <termo>`: Pesquisa guiada na web com fontes e síntese.\n" +
        "- `/pesquisar <termo>`: Busca rápida na internet.\n" +
        "- `/aprenda <tema>`: Pesquisa e indexa conhecimento sobre um framework ou linguagem.\n" +
        "- `/testes`: Executa os testes automatizados do projeto.\n" +
        "- `/verificacoes`: Lista os verificadores disponíveis no projeto.\n" +
        "- `/fontes`: Exibe as páginas consultadas nesta sessão.\n\n" +
        "**Dicas do Editor Workspace**:\n" +
        "- Pressione `Tab` no editor para identar com 2 espaços.\n" +
        "- Pressione `Ctrl+S` (ou `Cmd+S`) para salvar alterações pendentes.",
        "Guia de uso local"
      );
    }
    else if(value === '/analisar' || value === '/inspect') await call('inspect_project',{max_depth:4});
    else if(value === '/testes' || value === '/test') await call('project_checks',{check:'auto'});
    else if(value === '/verificacoes') await call('project_checks',{check:'list'});
    else if(value.startsWith('/ler ')) await call('read_file',{path:value.slice(5).trim()});
    else if(value.startsWith('/criar pasta ')) await runChatTool('create_directory',{path:value.slice(13).trim()},'Criar pasta');
    else if(value.startsWith('/criar arquivo ')) { const parts=value.slice(15).split('\n'); await runChatTool('create_file',{path:parts.shift().trim(),content:parts.join('\n')},'Criar arquivo'); }
    else if(value.startsWith('/editar arquivo ')) { const parts=value.slice(16).split('\n---\n'), head=parts.shift().split('\n'); await runChatTool('edit_file',{path:head.shift().trim(),old_text:head.join('\n'),new_text:parts.join('\n---\n')},'Editar arquivo'); }
    else if(parseRepairCommand(value)) { const repair=parseRepairCommand(value); if(repair.tool==='apply_repair') await runChatTool(repair.tool,repair.arguments,'Aplicar correção'); else await call(repair.tool,repair.arguments); }
    else if(value==='/fontes') await call('list_sources',{});
    else await chatModel();
  } catch(error) { notice(`Falha ao preparar a mensagem: ${error.message}`); }
  finally { sending=false; refreshControls(); saveConversation(); input.focus(); }
}
function welcomeTemplate() {
  return `<div class="welcome" id="welcome">
    <div class="welcome-badge">✦ IA Local do Zero</div>
    <h1>O que vamos explorar hoje?</h1>
    <p>Converse com o modelo neural, analise código, pesquise na documentação ou trabalhe no workspace.</p>
    <div class="welcome-chips">
      <button class="welcome-chip" onclick="quickAction('inspect_project')">
        <span class="chip-icon">⌘</span>
        <span class="chip-text"><strong>Analisar projeto ativo</strong><small>Estrutura, entradas e testes</small></span>
      </button>
      <button class="welcome-chip" onclick="quickAction('research_doc')">
        <span class="chip-icon">✦</span>
        <span class="chip-text"><strong>Pesquisar documentação</strong><small>Consultar fontes na web e acervo</small></span>
      </button>
      <button class="welcome-chip" onclick="quickAction('search_code')">
        <span class="chip-icon">⌕</span>
        <span class="chip-text"><strong>Buscar no código</strong><small>Encontrar funções e classes</small></span>
      </button>
      <button class="welcome-chip" onclick="quickAction('create_page')">
        <span class="chip-icon">🌐</span>
        <span class="chip-text"><strong>Criar tela ou página web</strong><small>Gera HTML com preview isolado</small></span>
      </button>
    </div>
    <p class="welcome-note">Execução 100% no seu computador · Modelo compacto com atenção e memória local.<br>Pesquisas externas consultam fontes e enriquecem o índice local.</p>
  </div>`;
}
function quickAction(action) {
  if (action === 'inspect_project') {
    input.value = 'Analise meu projeto ativo';
    send();
  } else if (action === 'research_doc') {
    input.value = '/pesquisa ';
    input.focus();
  } else if (action === 'search_code') {
    selectTool('search_files');
    input.placeholder = 'Digite o termo para buscar no código...';
    input.focus();
  } else if (action === 'create_page') {
    input.value = 'Crie uma página de login com visual moderno';
    input.focus();
  }
}
function newChat() {
  if(sending || liveActions.size) return notice('Aguarde a operação atual terminar.');
  saveConversation(); currentConversationId=null; agentRunRef=null; conversation=[]; timeline=[]; selectedTool=null; clearAttachments(); closeTool();
  chat.innerHTML=welcomeTemplate();
  renderHistory(); input.focus();
}
function quick(prefix) { input.value=prefix; input.focus(); }
function listSources() { openTool('sources'); }
function refreshProjectDock() { loadDockRoot(); }
input.addEventListener('keydown',event=> { if(event.key==='Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); } });
input.addEventListener('input',()=> { input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,140)+'px'; });
document.addEventListener('click', event => { if (!event.target.closest('.project-selector-wrap')) closeProjectMenu(); });
document.getElementById('project-name-input')?.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); createHeaderProject(); } });
renderHistory(); refreshControls();
restoreWorkspace();
async function restoreWorkspace() {
  const saved=localStorage.getItem('ia-local-zero-workspace');
  if(!saved) {
    try {
      const catalog=await fetch('/api/projects',{cache:'no-store'}).then(response=>response.json());
      if(catalog.ok && catalog.current) setWorkspaceIndicator(catalog.current);
    } catch (_) { /* O seletor continua disponível mesmo sem sincronização inicial. */ }
    return;
  }
  const selected=await toolRequest('set_workspace',{path:saved},false);
  if(!selected.ok) {
    localStorage.removeItem('ia-local-zero-workspace');
    setWorkspaceIndicator(null);
    return;
  }
  setWorkspaceIndicator(selected.data.workspace);
  const dockFiles=document.getElementById('dock-files');
  const data=await toolRequest('list_files',{path:''},false);
  if(!data.ok || !dockFiles) return;
  dockFiles.replaceChildren();
  for(const entry of data.data.entries.slice(0,80)) {
    const row=node('div','entry'), label=node('div',null,`${entry.kind==='directory'?'▣':'▤'} ${entry.name}`);
    const button=node('button',null,entry.kind==='directory'?'›':'Abrir');
    const relative=entry.name; button.onclick=()=>entry.kind==='directory'?openDockFolder(relative):runPanelRead(relative);
    label.onclick=button.onclick; label.style.cursor='pointer'; row.append(label,button); dockFiles.append(row);
  }
  if(!dockFiles.children.length) dockFiles.append(node('div','dock-empty','Projeto vazio.'));
}
setMode(localStorage.getItem('ia-local-zero-mode') || 'chat');
