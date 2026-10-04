/* Brasa · bancada: trilho, painéis, terminal seguro, atividade, gadgets, paleta e tema.
   Carregado depois de app.js; usa as funções globais dele (toolRequest, setMode, newChat,
   toggleWorkspaceExplorer, runPanelRead, quickAction, selectTool…) sem alterá-las.
   O terminal só executa perfis do runtime (contracts/terminal_run, project_checks,
   process_*, leitura e busca): nunca shell livre. */
(() => {
  'use strict';

  /* ------------------------------------------------------------ utilidades */
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const store = {
    get(key, fallback) { try { const v = localStorage.getItem('brasa-' + key); return v === null ? fallback : JSON.parse(v); } catch (_) { return fallback; } },
    set(key, value) { try { localStorage.setItem('brasa-' + key, JSON.stringify(value)); } catch (_) { /* armazenamento indisponível */ } },
  };
  const escapeHtml = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (name, cls = 'ico sm') => `<svg class="${cls}"><use href="#i-${name}"/></svg>`;
  const normalize = (text) => String(text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const narrow = (px) => window.matchMedia(`(max-width: ${px}px)`).matches;
  const workspacePath = () => { try { return localStorage.getItem('ia-local-zero-workspace') || ''; } catch (_) { return ''; } };
  const baseName = (path) => String(path || '').replace(/\/+$/, '').split('/').pop() || '';
  const clock = (ms) => new Date(ms || Date.now()).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const bytes = (n) => (n == null ? '' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

  // O runtime executa uma ferramenta por vez: as chamadas da bancada entram numa fila,
  // e uma colisão com o chat (ferramenta já em execução) é tentada de novo algumas vezes.
  let toolQueue = Promise.resolve();
  async function callTool(name, args) {
    try {
      if (typeof window.toolRequest === 'function') return await window.toolRequest(name, args, false);
      const response = await fetch('/api/v1/tools/call', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ tool: name, arguments: args }) });
      return await response.json();
    } catch (error) {
      return { ok: false, error: String(error && error.message || error) };
    }
  }
  function tool(name, args = {}) {
    const run = toolQueue.then(async () => {
      for (let attempt = 0; ; attempt++) {
        const result = await callTool(name, args);
        if (result.ok || attempt >= 4 || !/ferramenta em execu/i.test(result.error || '')) return result;
        await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
      }
    });
    toolQueue = run.catch(() => {});
    return run;
  }
  async function getJson(url, timeout = 6000) {
    const started = performance.now();
    const response = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(timeout) });
    const data = await response.json();
    return { data, ok: response.ok, ms: Math.round(performance.now() - started) };
  }
  function toast(message, kind = 'info', ms = 3200) {
    const stack = $('#toast-stack');
    if (!stack) return;
    const item = document.createElement('div');
    item.className = `toast ${kind}`;
    item.textContent = message;
    stack.append(item);
    setTimeout(() => item.remove(), ms);
  }

  /* ------------------------------------------------------------ tema */
  function applyTheme(theme, accent) {
    const root = document.documentElement;
    if (theme) { root.dataset.theme = theme; store.set('theme', theme); }
    if (accent) {
      if (accent === 'brasa') delete root.dataset.accent; else root.dataset.accent = accent;
      store.set('accent', accent);
    }
    const dark = root.dataset.theme === 'dark';
    $('#rail-theme use')?.setAttribute('href', dark ? '#i-sun' : '#i-moon');
    $('#sb-theme use')?.setAttribute('href', dark ? '#i-moon' : '#i-sun');
    const label = $('#sb-theme-text'); if (label) label.textContent = dark ? 'Carvão' : 'Papel';
    $('meta[name="theme-color"]')?.setAttribute('content', dark ? '#0e1210' : '#161b18');
  }
  const toggleTheme = () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

  /* ------------------------------------------------------------ trilho, lateral e contexto */
  function toggleSidebar() {
    if (narrow(720)) { window.toggleSidebar?.(); syncRail(); return; }
    const collapsed = document.body.classList.toggle('sidebar-collapsed');
    store.set('sidebar-collapsed', collapsed);
    syncRail();
  }
  function dockVisible() {
    if (narrow(1050)) return document.body.classList.contains('chat-context-open');
    return !document.body.classList.contains('workspace-explorer-collapsed');
  }
  function showDock(tab) {
    const current = store.get('dock-tab', 'files');
    if (dockVisible() && current === tab && document.activeElement?.closest?.('.rail')) {
      window.toggleWorkspaceExplorer?.();  // segundo clique no mesmo ícone recolhe
      syncRail();
      return;
    }
    if (!dockVisible()) window.toggleWorkspaceExplorer?.();
    store.set('dock-tab', tab);
    for (const name of ['files', 'gadgets']) {
      const active = name === tab;
      $(`#dock-tab-${name}`)?.setAttribute('aria-selected', String(active));
      const view = $(`#dock-view-${name}`); if (view) view.hidden = !active;
    }
    if (tab === 'gadgets') Gadgets.refresh();
    syncRail();
  }
  function syncRail() {
    const body = document.body;
    const set = (id, on) => $(id)?.setAttribute('aria-pressed', String(Boolean(on)));
    const sidebarOpen = narrow(720) ? $('#sidebar')?.classList.contains('open') : !body.classList.contains('sidebar-collapsed');
    const dockTab = store.get('dock-tab', 'files');
    set('#rail-chats', sidebarOpen);
    set('#rail-files', dockVisible() && dockTab === 'files');
    set('#rail-gadgets', dockVisible() && dockTab === 'gadgets');
    set('#rail-terminal', body.classList.contains('bench-open') && Bench.tab === 'terminal');
    set('#rail-activity', body.classList.contains('bench-open') && Bench.tab === 'activity');
    set('#rail-training', body.classList.contains('training-mode'));
    set('#header-terminal', body.classList.contains('bench-open'));
  }

  /* ------------------------------------------------------------ bancada inferior */
  const Bench = {
    tab: store.get('bench-tab', 'terminal'),
    open(tab) {
      document.body.classList.add('bench-open');
      store.set('bench-open', true);
      this.show(tab || this.tab);
    },
    close() {
      document.body.classList.remove('bench-open', 'bench-max');
      store.set('bench-open', false);
      syncRail();
    },
    toggle(tab) {
      const open = document.body.classList.contains('bench-open');
      if (open && (!tab || tab === this.tab)) this.close(); else this.open(tab);
    },
    show(tab) {
      this.tab = tab;
      store.set('bench-tab', tab);
      for (const name of ['terminal', 'activity', 'process']) {
        $(`#bench-tab-${name}`)?.setAttribute('aria-selected', String(name === tab));
        const view = $(`#bench-view-${name}`); if (view) view.hidden = name !== tab;
      }
      if (tab === 'terminal') { Terminal.greet(); setTimeout(() => $('#term-input')?.focus(), 0); }
      if (tab === 'activity') { Activity.markSeen(); requestAnimationFrame(() => { const feed = $('#activity-feed'); if (feed) feed.scrollTop = feed.scrollHeight; }); }
      if (tab === 'process') requestAnimationFrame(() => { const out = $('#process-output'); if (out) out.scrollTop = out.scrollHeight; });
      syncRail();
    },
    clear() {
      if (this.tab === 'terminal') Terminal.clear();
      else if (this.tab === 'activity') Activity.clear();
      else { const out = $('#process-output'); if (out) out.replaceChildren(); }
    },
    maximize() { document.body.classList.toggle('bench-max'); },
    initResize() {
      const handle = $('#bench-resize');
      const saved = store.get('bench-height', null);
      if (saved) document.documentElement.style.setProperty('--bench-h', `${saved}px`);
      if (!handle) return;
      const setHeight = (h) => {
        const height = Math.round(Math.max(140, Math.min(window.innerHeight * 0.75, h)));
        document.documentElement.style.setProperty('--bench-h', `${height}px`);
        store.set('bench-height', height);
      };
      handle.addEventListener('pointerdown', (event) => {
        event.preventDefault();
        handle.setPointerCapture(event.pointerId);
        handle.classList.add('dragging');
        document.body.classList.remove('bench-max');
        const bottom = $('#bench').getBoundingClientRect().bottom;
        const move = (e) => setHeight(bottom - e.clientY);
        const up = () => { handle.classList.remove('dragging'); handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', up); };
        handle.addEventListener('pointermove', move);
        handle.addEventListener('pointerup', up);
      });
      handle.addEventListener('keydown', (event) => {
        const current = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--bench-h'), 10) || 280;
        if (event.key === 'ArrowUp') { setHeight(current + 24); event.preventDefault(); }
        if (event.key === 'ArrowDown') { setHeight(current - 24); event.preventDefault(); }
      });
    },
  };

  /* ------------------------------------------------------------ terminal seguro */
  const CHECK_ALIASES = [
    [/^npm (run )?test$/, 'npm-test'], [/^npm run build$/, 'npm-build'], [/^npm run (check|lint)$/, 'npm-check'],
    [/^(python3? -m )?pytest\b.*$/, 'pytest'], [/^python3? -m unittest\b.*$/, 'unittest'], [/^cargo test\b.*$/, 'cargo-test'],
    [/^go test\b.*$/, 'go-test'], [/^node --test\b.*$/, 'node-test'], [/^make test$/, 'auto'],
  ];
  const COMMAND_ALIASES = [
    [/^(npm (run )?(dev|start)|yarn dev|pnpm dev|bun dev)$/, 'dev'], [/^git diff --stat$/, 'git diff'], [/^(dir|ll|la)$/, 'ls'],
    [/^(type|more|less|head|bat) /, 'cat '], [/^(rg|ag|ack) /, 'grep '], [/^grep -r[a-z]* /, 'grep '],
  ];
  const HELP = [
    ['help', 'lista os comandos'],
    ['clear', 'limpa o terminal (Ctrl+L)'],
    ['pwd', 'mostra o projeto ativo'],
    ['ls [pasta]', 'lista uma pasta do workspace'],
    ['tree [pasta]', 'árvore de arquivos (profundidade 2)'],
    ['cat <arquivo> [ini] [fim]', 'mostra um arquivo (ou um intervalo de linhas)'],
    ['open <arquivo>', 'abre o arquivo no editor'],
    ['find <padrão>', 'procura arquivos por nome (* e ?)'],
    ['grep <texto>', 'procura texto nos arquivos'],
    ['inspect', 'resumo do projeto: manifestos, entradas, testes'],
    ['git status | git diff', 'estado e resumo das alterações'],
    ['checks', 'lista as verificações reconhecidas'],
    ['test [verificação]', 'roda testes/verificações (ex.: test pytest)'],
    ['dev | stop | ps', 'servidor de desenvolvimento (perfil auto-dev)'],
    ['history', 'comandos anteriores'],
    ['theme [claro|escuro]', 'alterna o tema'],
  ];
  const COMMAND_NAMES = ['help', 'clear', 'pwd', 'ls', 'tree', 'cat', 'open', 'find', 'grep', 'inspect', 'git status', 'git diff', 'checks', 'test', 'dev', 'stop', 'ps', 'history', 'theme'];

  const Terminal = {
    history: store.get('term-history', []),
    cursor: null,
    greeted: false,
    busy: false,
    out() { return $('#term-output'); },
    print(text, cls = '') {
      const out = this.out(); if (!out) return;
      for (const line of String(text ?? '').split('\n')) {
        const row = document.createElement('div');
        row.className = `term-line ${cls}`.trim();
        row.textContent = line;
        out.append(row);
      }
      this.scroll();
    },
    html(markup, cls = '') {
      const out = this.out(); if (!out) return;
      const row = document.createElement('div');
      row.className = `term-line ${cls}`.trim();
      row.innerHTML = markup;
      out.append(row);
      this.scroll();
    },
    scroll() { const out = this.out(); if (out) out.scrollTop = out.scrollHeight; },
    clear() { this.out()?.replaceChildren(); },
    prompt() { const path = workspacePath(); return `brasa <span class="w">${escapeHtml(baseName(path) || '~')}</span> ❯`; },
    refreshPrompt() { const p = $('#term-prompt'); if (p) p.innerHTML = this.prompt(); },
    greet() {
      if (this.greeted) return;
      this.greeted = true;
      this.print('Brasa · terminal seguro do workspace', 'hl');
      this.print('Executa perfis do runtime — git, testes, leitura e busca de arquivos, servidor dev. Sem shell livre.', 'dim');
      this.print('Digite help para ver os comandos. Tab completa, ↑/↓ percorrem o histórico.', 'dim');
    },
    remember(command) {
      if (!command || this.history[this.history.length - 1] === command) return;
      this.history.push(command);
      this.history = this.history.slice(-200);
      store.set('term-history', this.history);
    },
    async run(raw) {
      let command = String(raw || '').trim();
      if (!command) return;
      Bench.open('terminal');
      this.greet();
      this.remember(command);
      this.html(`${this.prompt()} ${escapeHtml(command)}`, 'cmd');
      for (const [pattern, replacement] of COMMAND_ALIASES) if (pattern.test(command)) { command = command.replace(pattern, replacement); break; }
      for (const [pattern, check] of CHECK_ALIASES) if (pattern.test(command)) { command = `test ${check}`; break; }
      const [head, ...rest] = command.split(/\s+/);
      const arg = rest.join(' ');
      this.busy = true;
      $('#bench-view-terminal')?.classList.add('term-running');
      try {
        await this.dispatch(head.toLowerCase(), rest, arg, command);
      } catch (error) {
        this.print(`erro: ${error && error.message || error}`, 'err');
      } finally {
        this.busy = false;
        $('#bench-view-terminal')?.classList.remove('term-running');
        this.scroll();
      }
    },
    async dispatch(head, rest, arg, command) {
      switch (head) {
        case 'help': case 'ajuda': case '?':
          this.html(`<div class="term-table">${HELP.map(([c, d]) => `<span class="w" style="color:var(--term-blue)">${escapeHtml(c)}</span><span style="color:var(--term-dim)">${escapeHtml(d)}</span>`).join('')}</div>`);
          this.print('Comandos conhecidos como npm test, pytest, cargo test e npm run dev são traduzidos para o perfil seguro equivalente.', 'dim');
          return;
        case 'clear': case 'cls': this.clear(); return;
        case 'history': this.history.slice(-30).forEach((c, i) => this.print(`${String(this.history.length - Math.min(30, this.history.length) + i + 1).padStart(4)}  ${c}`, 'dim')); return;
        case 'pwd': this.print(workspacePath() || 'Nenhum projeto selecionado. Use o seletor de projeto no topo.', workspacePath() ? '' : 'warn'); return;
        case 'theme': case 'tema':
          applyTheme(/escur|dark/.test(arg) ? 'dark' : /clar|light/.test(arg) ? 'light' : (document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
          this.print(`tema: ${document.documentElement.dataset.theme === 'dark' ? 'Carvão (escuro)' : 'Papel (claro)'}`, 'ok');
          return;
        case 'ls': return this.ls(arg);
        case 'tree': return this.tree(arg);
        case 'cat': return this.cat(rest);
        case 'open': case 'code': case 'edit':
          if (!arg) return this.print('uso: open <arquivo>', 'warn');
          if (typeof window.runPanelRead !== 'function') return this.print('editor indisponível nesta página', 'err');
          await window.runPanelRead(arg);
          this.print(`aberto no editor: ${arg}`, 'ok');
          return;
        case 'find': return this.find(arg);
        case 'grep': case 'search': return this.grep(arg);
        case 'inspect': return this.inspect();
        case 'git': return this.git(rest[0] || 'status');
        case 'checks': return this.checks();
        case 'test': case 'check': return this.test(rest[0] || 'auto');
        case 'dev': case 'start': return Process.start();
        case 'stop': return Process.stop();
        case 'ps': return Process.status(true);
        default: {
          const suggestion = COMMAND_NAMES.find((name) => name.startsWith(head.slice(0, 2)));
          this.print(`“${command}” não é um perfil permitido. O runtime não executa shell livre por segurança.`, 'err');
          this.print(suggestion ? `Talvez você queira: ${suggestion}. Digite help para a lista completa.` : 'Digite help para a lista de comandos.', 'dim');
        }
      }
    },
    fail(result) { this.print(result?.error || 'falha desconhecida', 'err'); return false; },
    async ls(path) {
      const result = await tool('list_files', { path: path || '', max_entries: 400 });
      if (!result.ok) return this.fail(result);
      const entries = [...(result.data.entries || [])].sort((a, b) => (a.kind === 'directory' ? 0 : 1) - (b.kind === 'directory' ? 0 : 1) || a.name.localeCompare(b.name));
      if (!entries.length) return this.print('(pasta vazia)', 'dim');
      this.html(`<div class="term-table">${entries.map((e) => e.kind === 'directory'
        ? `<span style="color:var(--term-blue)">${escapeHtml(e.name)}/</span><span style="color:var(--term-dim)">pasta</span>`
        : `<span>${escapeHtml(e.name)}${e.kind === 'symlink' ? ' ↪' : ''}</span><span style="color:var(--term-dim)">${bytes(e.bytes)}</span>`).join('')}</div>`);
      this.print(`${result.data.total_entries ?? entries.length} itens${result.data.truncated ? ' (lista truncada)' : ''}`, 'dim');
    },
    async tree(path) {
      const result = await tool('list_tree', { path: path || '', max_depth: 2, max_entries: 300 });
      if (!result.ok) return this.fail(result);
      const entries = result.data.entries || [];
      for (const entry of entries) {
        const indent = '  '.repeat(Math.max(0, (entry.depth || 1) - 1));
        this.print(`${indent}${entry.kind === 'directory' ? '▸ ' + entry.name + '/' : '  ' + entry.name}`, entry.kind === 'directory' ? 'info' : '');
      }
      this.print(`${entries.length} itens${result.data.truncated ? ' (truncado)' : ''}`, 'dim');
    },
    async cat(rest) {
      const [path, start, end] = rest;
      if (!path) return this.print('uso: cat <arquivo> [linha_inicial] [linha_final]', 'warn');
      const args = { path };
      if (start) { args.start_line = Number(start); args.end_line = Number(end || Number(start) + 79); } else args.max_bytes = 32768;
      const result = await tool('read_file', args);
      if (!result.ok) return this.fail(result);
      const first = result.data.start_line || 1;
      const lines = String(result.data.content || '').split('\n');
      if (lines.length && lines[lines.length - 1] === '') lines.pop();
      const width = String(first + lines.length).length;
      const out = this.out();
      const block = document.createElement('div');
      block.className = 'term-line';
      block.textContent = lines.map((line, i) => `${String(first + i).padStart(width)} │ ${line}`).join('\n');
      out.append(block);
      this.print(`${result.data.path} · ${result.data.total_lines ? result.data.total_lines + ' linhas' : bytes(result.data.bytes)}${result.data.truncated ? ' · trecho parcial (use cat arquivo início fim)' : ''}`, 'dim');
    },
    async find(pattern) {
      if (!pattern) return this.print('uso: find <padrão>   ex.: find *.py', 'warn');
      const result = await tool('find_paths', { pattern: /[*?]/.test(pattern) ? pattern : `*${pattern}*`, max_results: 100 });
      if (!result.ok) return this.fail(result);
      const matches = result.data.matches || [];
      matches.forEach((m) => this.print(`${m.kind === 'directory' ? m.path + '/' : m.path}`, m.kind === 'directory' ? 'info' : ''));
      this.print(`${matches.length} resultado(s) em ${result.data.scanned} itens${result.data.truncated ? ' (truncado)' : ''}`, 'dim');
    },
    async grep(query) {
      if (!query) return this.print('uso: grep <texto>', 'warn');
      const result = await tool('search_files', { query: query.replace(/^["']|["']$/g, ''), max_results: 60 });
      if (!result.ok) return this.fail(result);
      const matches = result.data.matches || [];
      for (const m of matches) this.html(`<span style="color:var(--term-violet)">${escapeHtml(m.path)}</span><span style="color:var(--term-dim)">:${m.line}:</span> ${escapeHtml(String(m.text || '').trim())}`);
      this.print(matches.length ? `${matches.length} ocorrência(s)` : 'nenhuma ocorrência', matches.length ? 'dim' : 'warn');
    },
    async inspect() {
      this.print('inspecionando o projeto…', 'dim');
      const result = await tool('inspect_project', { max_depth: 3 });
      if (!result.ok) return this.fail(result);
      const d = result.data;
      this.print(d.summary || 'inspeção concluída', 'ok');
      const rows = [['workspace', d.workspace], ['arquivos', `${d.file_count ?? '?'} em ${d.directory_count ?? '?'} pastas`],
        ['manifestos', (d.manifests || []).join(', ')], ['entradas', (d.entrypoints || []).join(', ')],
        ['verificações', (d.checks || []).join(', ')], ['testes', (d.test_files || []).slice(0, 6).join(', ')], ['sinais', (d.signals || []).join(', ')]];
      this.html(`<div class="term-table">${rows.filter((r) => r[1]).map(([k, v]) => `<span style="color:var(--term-blue)">${k}</span><span>${escapeHtml(v)}</span>`).join('')}</div>`);
    },
    async git(sub) {
      const operation = /^diff/.test(sub) ? 'git_diff_stat' : 'git_status';
      const result = await tool('terminal_run', { operation });
      if (!result.ok) return this.fail(result);
      const d = result.data;
      if (d.exit_code && /not a git repository/i.test(d.stderr || '')) { StatusBar.git(null); return this.print('Este workspace não é um repositório git.', 'warn'); }
      const lines = String(d.stdout || '').split('\n').filter(Boolean);
      if (operation === 'git_status') {
        StatusBar.git(lines.length);
        if (!lines.length) return this.print('árvore de trabalho limpa ✓', 'ok');
        for (const line of lines) {
          const code = line.slice(0, 2);
          const cls = /\?\?/.test(code) ? 'dim' : /D/.test(code) ? 'err' : /A/.test(code) ? 'ok' : 'warn';
          this.print(line, cls);
        }
        this.print(`${lines.length} arquivo(s) alterado(s)`, 'dim');
      } else {
        lines.forEach((line) => this.html(escapeHtml(line).replace(/(\++)/g, '<span style="color:var(--term-green)">$1</span>').replace(/(-+)$/g, '<span style="color:var(--term-red)">$1</span>')));
        if (!lines.length) this.print('sem alterações', 'ok');
      }
      if (d.stderr && !d.passed) this.print(d.stderr.trim(), 'err');
    },
    async checks() {
      const result = await tool('project_checks', { check: 'list' });
      if (!result.ok) return this.fail(result);
      const available = result.data.available || [];
      if (!available.length) return this.print('nenhuma verificação reconhecida neste workspace', 'warn');
      this.html(`<div class="term-table">${available.map((c) => `<span style="color:var(--term-blue)">test ${escapeHtml(c)}</span><span style="color:var(--term-dim)">${escapeHtml((result.data.locations?.[c] || []).join(', '))}</span>`).join('')}</div>`);
      Gadgets.cache.checks = available;
    },
    async test(check) {
      this.print(`rodando verificação: ${check}…`, 'dim');
      const started = performance.now();
      const result = await tool('project_checks', { check });
      if (!result.ok) return this.fail(result);
      const runs = Array.isArray(result.data.results) ? result.data.results : [result.data];
      for (const run of runs) {
        if (run.command) this.print(`$ ${String(run.command).slice(0, 220)}${String(run.command).length > 220 ? '…' : ''}`, 'dim');
        if (run.stdout) this.print(String(run.stdout).trimEnd());
        if (run.stderr) this.print(String(run.stderr).trimEnd(), run.passed ? 'dim' : 'err');
        if (run.executed === false) this.print(run.summary || 'verificação não executada', 'warn');
        else this.print(`${run.passed ? '✓' : '✗'} ${run.check || check} · ${run.summary || (run.passed ? 'aprovada' : 'falhou')} · ${run.elapsed_ms ?? Math.round(performance.now() - started)} ms`, run.passed ? 'ok' : 'err');
      }
      Gadgets.lastCheck = { check, passed: runs.every((r) => r.passed), at: Date.now() };
      Gadgets.renderProject();
    },
    complete(input) {
      const value = input.value;
      const matches = COMMAND_NAMES.filter((name) => name.startsWith(value.toLowerCase()));
      if (matches.length === 1) input.value = matches[0] + ' ';
      else if (matches.length > 1) this.print(matches.join('   '), 'dim');
    },
    bind() {
      const form = $('#term-form'), input = $('#term-input');
      if (!form || !input) return;
      form.addEventListener('submit', (event) => { event.preventDefault(); const value = input.value; input.value = ''; this.cursor = null; this.run(value); });
      input.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
          if (!this.history.length) return;
          event.preventDefault();
          this.cursor = this.cursor == null ? this.history.length : this.cursor;
          this.cursor = Math.max(0, Math.min(this.history.length, this.cursor + (event.key === 'ArrowUp' ? -1 : 1)));
          input.value = this.history[this.cursor] || '';
        } else if (event.key === 'Tab') { event.preventDefault(); this.complete(input); }
        else if (event.key === 'l' && event.ctrlKey) { event.preventDefault(); this.clear(); }
      });
      $('#term-output')?.addEventListener('click', () => { if (!window.getSelection()?.toString()) input.focus(); });
    },
  };

  /* ------------------------------------------------------------ servidor de desenvolvimento */
  const Process = {
    id: store.get('process-id', null),
    cursors: { stdout: 0, stderr: 0 },
    polling: false,
    out() { return $('#process-output'); },
    append(text, cls = '') {
      const out = this.out(); if (!out || !text) return;
      out.querySelector('.process-empty')?.remove();
      const row = document.createElement('div');
      row.className = `term-line ${cls}`.trim();
      row.textContent = text.replace(/\n$/, '');
      out.append(row);
      out.scrollTop = out.scrollHeight;
    },
    setState(state, extra = '') {
      const label = $('#process-state');
      const running = state === 'running' || state === 'starting';
      if (label) label.innerHTML = `<span class="state-dot ${running ? 'ok' : state === 'failed' ? 'err' : ''}"></span>${escapeHtml(state ? `${state}${extra}` : 'Nenhum processo')}`;
      const start = $('#process-start'), stop = $('#process-stop');
      if (start) start.disabled = running;
      if (stop) stop.disabled = !running;
    },
    async start() {
      Terminal.print('iniciando servidor de desenvolvimento (perfil auto-dev)…', 'dim');
      const result = await tool('process_start', { profile: 'auto-dev' });
      if (!result.ok) { Terminal.print(result.error || 'não foi possível iniciar', 'err'); return; }
      this.id = result.data.process_id; store.set('process-id', this.id);
      this.cursors = { stdout: 0, stderr: 0 };
      Terminal.print(`processo ${this.id} · ${result.data.command || ''}`, 'ok');
      Terminal.print('acompanhe a saída na aba “Servidor dev”', 'dim');
      this.append(`$ ${result.data.command || 'auto-dev'}`, 'dim');
      this.setState('running');
      this.poll();
    },
    async stop() {
      if (!this.id) { Terminal.print('nenhum processo iniciado nesta sessão', 'warn'); return; }
      const result = await tool('process_stop', { process_id: this.id });
      if (!result.ok) { Terminal.print(result.error, 'err'); return; }
      Terminal.print(`processo ${this.id} encerrado`, 'ok');
      this.setState(result.data.state || 'stopped');
    },
    async status(verbose) {
      if (!this.id) { if (verbose) Terminal.print('nenhum processo iniciado nesta sessão', 'dim'); return null; }
      const result = await tool('process_status', { process_id: this.id, stdout_cursor: this.cursors.stdout, stderr_cursor: this.cursors.stderr, wait_ms: verbose ? 0 : 1200 });
      if (!result.ok) { if (verbose) Terminal.print(result.error, 'err'); this.setState(null); return null; }
      const d = result.data;
      if (d.stdout?.text) this.append(d.stdout.text);
      if (d.stderr?.text) this.append(d.stderr.text, 'warn');
      this.cursors = { stdout: d.stdout?.cursor ?? this.cursors.stdout, stderr: d.stderr?.cursor ?? this.cursors.stderr };
      const url = d.readiness && (d.readiness.url || d.readiness.local_url);
      this.setState(d.state, url ? ` · ${url}` : '');
      if (verbose) Terminal.print(`${d.process_id} · ${d.state}${d.exit_code != null ? ` · saída ${d.exit_code}` : ''}${url ? ` · ${url}` : ''} · ${Math.round((d.elapsed_ms || 0) / 1000)} s`, d.state === 'running' ? 'ok' : 'dim');
      return d;
    },
    async poll() {
      if (this.polling) return;
      this.polling = true;
      try {
        for (;;) {
          const d = await this.status(false);
          if (!d || !['running', 'starting'].includes(d.state)) break;
        }
      } finally { this.polling = false; }
    },
  };

  /* ------------------------------------------------------------ atividade do agente */
  const Activity = {
    cursor: 0,
    unseen: 0,
    recent: [],
    timer: null,
    markSeen() { this.unseen = 0; this.badge(); },
    badge() {
      const text = this.unseen ? String(Math.min(this.unseen, 99)) : '';
      const a = $('#bench-activity-count'), b = $('#rail-activity-badge');
      if (a) a.textContent = text; if (b) b.textContent = text;
    },
    clear() { $('#activity-feed')?.replaceChildren(); },
    render(event) {
      const feed = $('#activity-feed'); if (!feed) return;
      feed.querySelector('.activity-empty')?.remove();
      const status = String(event.status || event.phase || '').toLowerCase();
      const cls = /done|completed|success/.test(status) ? 'done' : /error|fail/.test(status) ? 'error' : /block/.test(status) ? 'blocked' : '';
      const mark = cls === 'done' ? '✓' : cls === 'error' ? '✗' : cls === 'blocked' ? '!' : '•';
      const row = document.createElement('div');
      row.className = `activity-item ${cls}`;
      const detail = event.detail && event.detail !== event.title ? ` <small>${escapeHtml(String(event.detail).slice(0, 160))}</small>` : '';
      row.innerHTML = `<span class="activity-time">${clock(event.timestamp_ms)}</span><span class="activity-mark">${mark}</span>`
        + `<span class="activity-text">${escapeHtml(event.title || event.kind || 'evento')}${detail}</span><span class="activity-ms">${event.elapsed_ms != null ? event.elapsed_ms + ' ms' : ''}</span>`;
      row.title = `${event.kind || ''} · ${event.task_id || ''}`;
      const stick = feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 30;
      feed.append(row);
      while (feed.children.length > 400) feed.firstElementChild.remove();
      if (stick) feed.scrollTop = feed.scrollHeight;
    },
    async poll() {
      try {
        const { data } = await getJson(`/api/events?after_seq=${this.cursor}`, 4000);
        const events = Array.isArray(data?.events) ? data.events : [];
        const fresh = this.cursor === 0 ? events.slice(-80) : events;
        for (const event of fresh) this.render(event);
        if (events.length) {
          this.cursor = Math.max(this.cursor, ...events.map((e) => Number(e.seq) || 0));
          this.recent = [...this.recent, ...fresh].slice(-12);
          const visible = document.body.classList.contains('bench-open') && Bench.tab === 'activity';
          if (!visible && this.initialized) this.unseen += events.length;
          this.badge();
          if (store.get('dock-tab', 'files') === 'gadgets') Gadgets.renderActivity();
        }
        this.initialized = true;
      } catch (_) { /* runtime fora do ar: o indicador de status mostra */ }
      const fast = document.body.classList.contains('bench-open') || document.body.querySelector('.live-status.busy');
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.poll(), document.hidden ? 15000 : fast ? 1500 : 5000);
    },
  };

  /* ------------------------------------------------------------ barra de status */
  const StatusBar = {
    async health() {
      const dot = $('#sb-runtime-dot'), text = $('#sb-runtime-text');
      try {
        const { data, ok, ms } = await getJson('/api/health', 4000);
        if (!ok || !data.ok) throw new Error('indisponível');
        dot?.classList.remove('err'); dot?.classList.add('ok');
        if (text) text.textContent = `Runtime ${data.version || ''} · ${ms} ms`;
        Gadgets.cache.runtime = { ok: true, version: data.version, ms };
      } catch (_) {
        dot?.classList.remove('ok'); dot?.classList.add('err');
        if (text) text.textContent = 'Runtime fora do ar';
        Gadgets.cache.runtime = { ok: false };
      }
      setTimeout(() => this.health(), document.hidden ? 60000 : 20000);
    },
    async model() {
      try {
        const { data } = await getJson('/api/v1/dialogue/providers', 6000);
        const path = data?.own_checkpoint?.checkpoint || '';
        const parts = path.split('/').filter(Boolean);
        const label = parts.length >= 2 ? `${parts[parts.length - 3] || ''}/${parts[parts.length - 2]}`.replace(/^\//, '') : (data?.mode || 'modelo');
        const el = $('#sb-model-text'); if (el) el.textContent = label || 'modelo';
        $('#sb-model')?.setAttribute('title', path || 'Modelo ativo');
        Gadgets.cache.model = { label, path, mode: data?.mode, configured: data?.own_checkpoint?.configured };
      } catch (_) { const el = $('#sb-model-text'); if (el) el.textContent = 'modelo ?'; }
    },
    workspace() {
      const path = workspacePath();
      const el = $('#sb-workspace-text'); if (el) el.textContent = baseName(path) || 'sem projeto';
      $('#sb-workspace')?.setAttribute('title', path || 'Nenhum projeto selecionado');
      Terminal.refreshPrompt();
    },
    git(count) {
      const el = $('#sb-git-text'); if (!el) return;
      el.textContent = count == null ? 'sem git' : count ? `${count} alteraç${count === 1 ? 'ão' : 'ões'}` : 'git limpo';
      Gadgets.cache.git = count;
    },
    tick() { const el = $('#sb-clock-text'); if (el) el.textContent = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }); },
  };

  /* ------------------------------------------------------------ gadgets */
  const Gadgets = {
    cache: {},
    lastCheck: null,
    started: Date.now(),
    rendered: false,
    card(id, title, iconName, body, refresh) {
      return `<section class="gadget" id="${id}"><div class="gadget-head">${icon(iconName)}<h3>${title}</h3>`
        + (refresh ? `<button type="button" title="Atualizar" aria-label="Atualizar ${title}" data-refresh="${refresh}">${icon('refresh')}</button>` : '')
        + `</div><div class="gadget-body">${body}</div></section>`;
    },
    skeleton() {
      const root = $('#gadgets'); if (!root || this.rendered) return;
      this.rendered = true;
      root.innerHTML = [
        this.card('g-system', 'Sistema', 'cpu', '<div class="gadget-empty">Consultando…</div>', 'system'),
        this.card('g-project', 'Projeto', 'folder', '<div class="gadget-empty">Consultando…</div>', 'project'),
        this.card('g-git', 'Git', 'branch', '<div class="gadget-empty">Clique em atualizar para ver as alterações.</div>', 'git'),
        this.card('g-activity', 'Atividade recente', 'activity', '<div class="gadget-empty">Sem eventos ainda.</div>'),
        this.card('g-skills', 'Competências', 'cap', '<div class="gadget-empty">Consultando…</div>', 'skills'),
        this.card('g-session', 'Sessão', 'clock', ''),
        this.card('g-notes', 'Notas rápidas', 'note', '<textarea class="gadget-notes" id="gadget-notes" placeholder="Ideias, decisões, próximos passos… (salvo neste navegador)"></textarea><div class="gadget-foot" id="gadget-notes-foot"></div>'),
      ].join('');
      root.addEventListener('click', (event) => {
        const button = event.target.closest('[data-refresh]');
        if (button) this[`render${button.dataset.refresh[0].toUpperCase()}${button.dataset.refresh.slice(1)}`]?.(true);
        const run = event.target.closest('[data-run]');
        if (run) Terminal.run(run.dataset.run);
      });
      const notes = $('#gadget-notes');
      if (notes) {
        notes.value = store.get('notes', '');
        let timer;
        notes.addEventListener('input', () => {
          clearTimeout(timer);
          timer = setTimeout(() => { store.set('notes', notes.value); const foot = $('#gadget-notes-foot'); if (foot) foot.textContent = `salvo às ${clock()}`; }, 400);
        });
      }
    },
    set(id, html) { const body = $(`#${id} .gadget-body`); if (body) body.innerHTML = html; },
    async refresh() {
      this.skeleton();
      this.renderSystem(); this.renderProject(); this.renderActivity(); this.renderSkills(); this.renderSession();
      if (!this.gitLoaded) { this.gitLoaded = true; this.renderGit(); }
    },
    async renderSystem() {
      const row = (state, name, value) => `<div class="gadget-row"><span class="state-dot ${state}"></span><strong>${name}</strong><span class="value" title="${escapeHtml(value)}">${escapeHtml(value)}</span></div>`;
      let experimental = null;
      try { experimental = (await getJson('/api/v1/models/experimental', 6000)).data; } catch (_) { /* opcional */ }
      if (!this.cache.model) await StatusBar.model();
      const runtime = this.cache.runtime || {};
      const model = this.cache.model || {};
      this.set('g-system', [
        row(runtime.ok ? 'ok' : runtime.ok === false ? 'err' : '', 'Runtime', runtime.ok ? `v${runtime.version || '?'} · ${runtime.ms} ms` : runtime.ok === false ? 'fora do ar' : '…'),
        row(model.configured ? 'ok' : 'warn', 'Modelo', model.label || 'não configurado'),
        row(experimental?.loaded ? 'ok' : experimental?.enabled ? 'warn' : '', 'Lab V4', experimental ? `${experimental.qualified ? 'qualificado' : 'não qualificado'} · ${experimental.context_tokens || '?'} tokens` : 'indisponível'),
        row('ok', 'Execução', 'local · 127.0.0.1'),
      ].join(''));
    },
    async renderProject(force) {
      const path = workspacePath();
      if (!path) { this.set('g-project', '<div class="gadget-empty">Nenhum projeto selecionado.</div><div class="gadget-actions"><button type="button" onclick="toggleProjectMenu(event)">Escolher projeto</button></div>'); return; }
      if (force || !this.cache.checks || this.cache.checksFor !== path) {
        const result = await tool('project_checks', { check: 'list' });
        this.cache.checks = result.ok ? result.data.available || [] : [];
        this.cache.checksFor = path;
      }
      const checks = this.cache.checks || [];
      const last = this.lastCheck ? `<div class="gadget-row"><span class="state-dot ${this.lastCheck.passed ? 'ok' : 'err'}"></span><strong>Última verificação</strong><span class="value">${escapeHtml(this.lastCheck.check)} · ${clock(this.lastCheck.at)}</span></div>` : '';
      this.set('g-project', `<div class="gadget-row"><strong>${escapeHtml(baseName(path))}</strong><span class="value" title="${escapeHtml(path)}">${escapeHtml(path)}</span></div>${last}`
        + `<div class="gadget-actions">${checks.slice(0, 6).map((c) => `<button type="button" data-run="test ${escapeHtml(c)}">${icon('play')}${escapeHtml(c)}</button>`).join('')}`
        + `<button type="button" onclick="quickAction('inspect_project')">${icon('sparkle')}Analisar com o agente</button></div>`
        + (checks.length ? '' : '<div class="gadget-foot">Nenhuma verificação reconhecida neste projeto.</div>'));
    },
    async renderGit() {
      const result = await tool('terminal_run', { operation: 'git_status' });
      if (!result.ok) { this.set('g-git', `<div class="gadget-empty">${escapeHtml(result.error)}</div>`); return; }
      const d = result.data;
      if (/not a git repository/i.test(d.stderr || '')) { StatusBar.git(null); this.set('g-git', '<div class="gadget-empty">Este workspace não é um repositório git.</div>'); return; }
      const lines = String(d.stdout || '').split('\n').filter(Boolean);
      StatusBar.git(lines.length);
      if (!lines.length) { this.set('g-git', '<div class="gadget-row"><span class="state-dot ok"></span><strong>Árvore limpa</strong><span class="value">nenhuma alteração</span></div>'); return; }
      const items = lines.slice(0, 40).map((line) => {
        const code = line.slice(0, 2).trim() || '?';
        const cls = /\?/.test(code) ? '' : /D/.test(code) ? 'err' : /A/.test(code) ? 'ok' : 'warn';
        return `<li><span class="tag ${cls}">${escapeHtml(code)}</span><span title="${escapeHtml(line.slice(3))}">${escapeHtml(line.slice(3))}</span></li>`;
      }).join('');
      this.set('g-git', `<div class="gadget-row"><strong>${lines.length} alteraç${lines.length === 1 ? 'ão' : 'ões'}</strong></div><ul class="gadget-list">${items}</ul>`
        + `<div class="gadget-actions"><button type="button" data-run="git diff">${icon('branch')}Resumo do diff</button></div>`);
    },
    renderActivity() {
      const events = Activity.recent.slice(-6).reverse();
      if (!events.length) return;
      this.set('g-activity', `<ul class="gadget-list">${events.map((e) => {
        const status = String(e.status || e.phase || '');
        const cls = /done|complet/.test(status) ? 'ok' : /error|fail/.test(status) ? 'err' : /block/.test(status) ? 'warn' : '';
        return `<li><span class="tag ${cls}">${clock(e.timestamp_ms).slice(0, 5)}</span><span title="${escapeHtml(e.title)}">${escapeHtml(e.title || e.kind)}</span></li>`;
      }).join('')}</ul><div class="gadget-actions"><button type="button" onclick="Workbench.toggleBench('activity')">${icon('activity')}Abrir feed completo</button></div>`);
    },
    async renderSkills() {
      try {
        const { data } = await getJson('/api/v1/agent/capabilities', 8000);
        const s = data.summary || {};
        const skills = (data.skills || []).slice().sort((a, b) => (b.lab_progress || 0) - (a.lab_progress || 0)).slice(0, 4);
        this.set('g-skills', `<div class="gadget-stat"><div><b>${s.registered ?? 0}</b><span>registradas</span></div><div><b>${s.with_reference_lab_checks ?? 0}</b><span>com laboratório</span></div><div><b>${s.mastered ?? 0}</b><span>dominadas</span></div></div>`
          + skills.map((k) => `<div style="margin-top:9px"><div class="gadget-row" style="min-height:18px"><strong>${escapeHtml(k.topic)}</strong><span class="value">${Math.round((k.lab_progress || 0) * 100)}%</span></div><div class="gadget-meter"><i style="width:${Math.round((k.lab_progress || 0) * 100)}%"></i></div></div>`).join(''));
      } catch (_) { this.set('g-skills', '<div class="gadget-empty">Registro de competências indisponível.</div>'); }
    },
    renderSession() {
      const chat = $('#chat');
      const users = chat ? chat.querySelectorAll('.message.user').length : 0;
      const answers = chat ? chat.querySelectorAll('.message.assistant').length : 0;
      const actions = chat ? chat.querySelectorAll('.action-message').length : 0;
      const minutes = Math.max(1, Math.round((Date.now() - this.started) / 60000));
      const saved = $$('#history-list .history-item').length;
      this.set('g-session', `<div class="gadget-stat"><div><b>${users}</b><span>pedidos</span></div><div><b>${answers}</b><span>respostas</span></div><div><b>${actions}</b><span>ações</span></div></div>`
        + `<div class="gadget-foot">${saved} conversa(s) salvas · sessão aberta há ${minutes} min</div>`);
    },
  };

  /* ------------------------------------------------------------ paleta de comandos */
  const Palette = {
    items: [],
    filtered: [],
    index: 0,
    actions() {
      const call = (name, ...args) => () => window[name]?.(...args);
      const list = [
        { group: 'Ações', title: 'Nova conversa', icon: 'plus', run: call('newChat') },
        { group: 'Ações', title: 'Escrever mensagem', icon: 'chat', run: () => $('#message')?.focus() },
        { group: 'Ações', title: 'Analisar o projeto com o agente', icon: 'sparkle', run: call('quickAction', 'inspect_project') },
        { group: 'Ações', title: 'Pesquisar na internet', icon: 'search', run: () => { window.selectTool?.('search_web'); $('#message')?.focus(); } },
        { group: 'Ações', title: 'Buscar no código', icon: 'code', run: () => { window.selectTool?.('search_files'); $('#message')?.focus(); } },
        { group: 'Ações', title: 'Escolher projeto…', icon: 'folder', run: () => setTimeout(() => window.toggleProjectMenu?.(new Event('click')), 0) },
        { group: 'Terminal', title: 'Abrir terminal', icon: 'terminal', keys: 'Ctrl `', run: () => Bench.open('terminal') },
        { group: 'Terminal', title: 'Rodar testes do projeto', icon: 'check', run: () => Terminal.run('test') },
        { group: 'Terminal', title: 'Git status', icon: 'branch', run: () => Terminal.run('git status') },
        { group: 'Terminal', title: 'Iniciar servidor de desenvolvimento', icon: 'play', run: () => { Bench.open('process'); Terminal.run('dev'); } },
        { group: 'Terminal', title: 'Parar servidor de desenvolvimento', icon: 'stop', run: () => Terminal.run('stop') },
        { group: 'Terminal', title: 'Ver atividade do agente', icon: 'activity', run: () => Bench.open('activity') },
        { group: 'Painéis', title: 'Mostrar arquivos do projeto', icon: 'folder', run: () => showDock('files') },
        { group: 'Painéis', title: 'Mostrar painel de controle', icon: 'dashboard', run: () => showDock('gadgets') },
        { group: 'Painéis', title: 'Alternar painel de contexto', icon: 'panel-right', keys: 'Ctrl Shift E', run: () => { window.toggleWorkspaceExplorer?.(); syncRail(); } },
        { group: 'Painéis', title: 'Alternar lista de conversas', icon: 'panel-left', keys: 'Ctrl B', run: toggleSidebar },
        { group: 'Painéis', title: document.body.classList.contains('training-mode') ? 'Voltar ao chat' : 'Abrir treinamento', icon: 'cap', run: () => { window.setMode?.(document.body.classList.contains('training-mode') ? 'chat' : 'training'); syncRail(); } },
        { group: 'Painéis', title: 'Laboratório cognitivo', icon: 'flask', run: call('openExperimentalLab') },
        { group: 'Painéis', title: 'Revisar trajetórias de treino', icon: 'note', run: () => { window.setMode?.('training'); window.openWorkflowReview?.(); syncRail(); } },
        { group: 'Aparência', title: 'Tema claro (Papel)', icon: 'sun', run: () => applyTheme('light') },
        { group: 'Aparência', title: 'Tema escuro (Carvão)', icon: 'moon', run: () => applyTheme('dark') },
        { group: 'Aparência', title: 'Destaque: Brasa (laranja)', icon: 'sparkle', run: () => applyTheme(null, 'brasa') },
        { group: 'Aparência', title: 'Destaque: Mar (verde-azulado)', icon: 'sparkle', run: () => applyTheme(null, 'mar') },
        { group: 'Aparência', title: 'Destaque: Violeta', icon: 'sparkle', run: () => applyTheme(null, 'violeta') },
      ];
      for (const button of $$('#history-list .history-item')) {
        const title = button.textContent.trim();
        if (title) list.push({ group: 'Conversas', title, icon: 'chat', run: () => button.click() });
      }
      return list;
    },
    score(query, text) {
      if (!query) return 1;
      const q = normalize(query), t = normalize(text);
      const at = t.indexOf(q);
      if (at >= 0) return 100 - at;
      let position = 0, score = 0;
      for (const ch of q) {
        const found = t.indexOf(ch, position);
        if (found < 0) return 0;
        score += found === position ? 3 : 1;
        position = found + 1;
      }
      return score;
    },
    highlight(text, query) {
      if (!query) return escapeHtml(text);
      const t = normalize(text), q = normalize(query), at = t.indexOf(q);
      if (at < 0) return escapeHtml(text);
      return escapeHtml(text.slice(0, at)) + '<mark>' + escapeHtml(text.slice(at, at + query.length)) + '</mark>' + escapeHtml(text.slice(at + query.length));
    },
    filter() {
      const raw = $('#palette-input').value;
      const list = $('#palette-list');
      if (raw.trim().startsWith('>')) {
        const command = raw.trim().slice(1).trim();
        this.filtered = [{ group: 'Terminal', title: command ? `Executar: ${command}` : 'Digite um comando de terminal…', icon: 'terminal', run: () => command && Terminal.run(command) }];
      } else {
        const query = raw.trim();
        this.filtered = this.items.map((item) => ({ item, score: Math.max(this.score(query, item.title), this.score(query, item.group) * 0.5) + (item.group === 'Conversas' ? -0.5 : 0) }))
          .filter((x) => x.score > 0).sort((a, b) => (query ? b.score - a.score : 0)).map((x) => x.item).slice(0, 60);
        // Reagrupa mantendo a ordem do melhor resultado de cada grupo (um cabeçalho por grupo).
        const groups = new Map();
        for (const item of this.filtered) { if (!groups.has(item.group)) groups.set(item.group, []); groups.get(item.group).push(item); }
        this.filtered = [...groups.values()].flat();
        this.query = query;
      }
      this.index = 0;
      let group = '';
      list.innerHTML = this.filtered.map((item, i) => {
        const header = item.group !== group ? `<li class="palette-group" role="presentation">${escapeHtml(group = item.group)}</li>` : '';
        return `${header}<li class="palette-item" role="option" id="palette-opt-${i}" data-index="${i}" aria-selected="${i === 0}">${icon(item.icon)}<span>${this.highlight(item.title, raw.trim().startsWith('>') ? '' : this.query)}</span>${item.keys ? `<small>${escapeHtml(item.keys)}</small>` : ''}</li>`;
      }).join('') || '<li class="palette-group">Nada encontrado</li>';
      $('#palette-input').setAttribute('aria-activedescendant', this.filtered.length ? 'palette-opt-0' : '');
    },
    move(delta) {
      if (!this.filtered.length) return;
      this.index = (this.index + delta + this.filtered.length) % this.filtered.length;
      $$('#palette-list .palette-item').forEach((el) => el.setAttribute('aria-selected', String(Number(el.dataset.index) === this.index)));
      const active = $(`#palette-opt-${this.index}`);
      active?.scrollIntoView({ block: 'nearest' });
      $('#palette-input').setAttribute('aria-activedescendant', active ? active.id : '');
    },
    execute(i = this.index) {
      const item = this.filtered[i];
      this.close();
      if (item) setTimeout(() => item.run(), 0);
    },
    open(query = '') {
      const dialog = $('#palette'); if (!dialog) return;
      this.items = this.actions();
      if (!dialog.open) dialog.showModal();
      const input = $('#palette-input');
      input.value = query;
      this.filter();
      input.focus();
      input.select();
    },
    close() { const dialog = $('#palette'); if (dialog?.open) dialog.close(); },
    toggle(query) { if ($('#palette')?.open) this.close(); else this.open(query); },
    bind() {
      const input = $('#palette-input'), list = $('#palette-list'), dialog = $('#palette');
      if (!input || !list || !dialog) return;
      input.addEventListener('input', () => this.filter());
      input.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown') { event.preventDefault(); this.move(1); }
        else if (event.key === 'ArrowUp') { event.preventDefault(); this.move(-1); }
        else if (event.key === 'Enter') { event.preventDefault(); this.execute(); }
      });
      list.addEventListener('click', (event) => { const item = event.target.closest('.palette-item'); if (item) this.execute(Number(item.dataset.index)); });
      list.addEventListener('mousemove', (event) => {
        const item = event.target.closest('.palette-item');
        if (item && Number(item.dataset.index) !== this.index) { this.index = Number(item.dataset.index); this.move(0); }
      });
      dialog.addEventListener('click', (event) => { if (event.target === dialog) this.close(); });
    },
  };

  /* ------------------------------------------------------------ atalhos e inicialização */
  function bindShortcuts() {
    document.addEventListener('keydown', (event) => {
      const mod = event.ctrlKey || event.metaKey;
      if (!mod) return;
      const key = event.key.toLowerCase();
      if (key === 'k' && !event.shiftKey) { event.preventDefault(); Palette.toggle(); }
      else if (event.code === 'Backquote' || key === '`' || key === "'" && event.code === 'Backquote') { event.preventDefault(); Bench.toggle('terminal'); }
      else if (key === 'j' && !event.shiftKey) { event.preventDefault(); Bench.toggle(); }
      else if (key === 'b' && !event.shiftKey && !event.target.closest?.('#stage-editor')) { event.preventDefault(); toggleSidebar(); }
      else if (key === 'e' && event.shiftKey) { event.preventDefault(); window.toggleWorkspaceExplorer?.(); syncRail(); }
    }, true);
  }
  function watchWorkspace() {
    const label = $('#header-workspace-label');
    if (label) new MutationObserver(() => { StatusBar.workspace(); Gadgets.cache.checks = null; if (store.get('dock-tab', 'files') === 'gadgets') Gadgets.renderProject(); }).observe(label, { childList: true, characterData: true, subtree: true });
    window.addEventListener('storage', (event) => { if (event.key === 'ia-local-zero-workspace') StatusBar.workspace(); });
    new MutationObserver(syncRail).observe(document.body, { attributes: true, attributeFilter: ['class'] });
    // No celular, tocar fora do menu lateral o fecha.
    document.addEventListener('click', (event) => {
      const sidebar = $('#sidebar');
      if (!narrow(720) || !sidebar?.classList.contains('open')) return;
      if (event.target.closest('#sidebar, #mobile-sidebar-toggle')) return;
      sidebar.classList.remove('open');
      syncRail();
    });
    const sidebar = $('#sidebar'); if (sidebar) new MutationObserver(syncRail).observe(sidebar, { attributes: true, attributeFilter: ['class'] });
  }
  function init() {
    applyTheme(document.documentElement.dataset.theme || 'light', store.get('accent', null));
    if (store.get('sidebar-collapsed', false) && !narrow(720)) document.body.classList.add('sidebar-collapsed');
    Bench.initResize();
    Terminal.bind();
    Palette.bind();
    bindShortcuts();
    watchWorkspace();
    showDockInitial();
    if (store.get('bench-open', false)) Bench.open(store.get('bench-tab', 'terminal'));
    StatusBar.workspace();
    StatusBar.health();
    StatusBar.model();
    StatusBar.tick(); setInterval(() => StatusBar.tick(), 30000);
    setInterval(() => { if (store.get('dock-tab', 'files') === 'gadgets' && dockVisible()) Gadgets.renderSession(); }, 30000);
    Activity.poll();
    if (Process.id) Process.status(false).then((d) => d && ['running', 'starting'].includes(d.state) && Process.poll());
    syncRail();
  }
  function showDockInitial() {
    const tab = store.get('dock-tab', 'files');
    for (const name of ['files', 'gadgets']) {
      $(`#dock-tab-${name}`)?.setAttribute('aria-selected', String(name === tab));
      const view = $(`#dock-view-${name}`); if (view) view.hidden = name !== tab;
    }
    if (tab === 'gadgets') Gadgets.refresh();
  }

  window.Workbench = {
    toggleSidebar, showDock, syncRail, toggleTheme, applyTheme,
    toggleBench: (tab) => Bench.toggle(tab), showBench: (tab) => Bench.show(tab), clearBench: () => Bench.clear(), maximizeBench: () => Bench.maximize(),
    run: (command) => Terminal.run(command), togglePalette: (query) => Palette.toggle(query), toast,
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
