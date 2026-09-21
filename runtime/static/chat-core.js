/* Regras do compositor independentes do DOM, compartilhadas com os testes. */
(function (scope) {
  const LIMITS = { files: 60, fileBytes: 65536, totalBytes: 262144 };
  const ignored = new Set(['node_modules', '.git', '.venv', 'venv', 'target', 'dist', 'build', '__pycache__', '.ia-local-backups', 'backups']);
  const textExtension = /\.(txt|md|json|csv|py|js|jsx|ts|tsx|rs|html|css|scss|toml|yaml|yml|sql|sh|xml)$/i;
  function pathOf(file) { return file.webkitRelativePath || file.name; }
  function eligible(file) {
    const parts = pathOf(file).split('/');
    const name = parts.at(-1).toLowerCase();
    return !parts.some(part => ignored.has(part)) && !name.startsWith('.env') &&
      !/^(package-lock\.json|yarn\.lock|pnpm-lock\.yaml)$/.test(name) &&
      !/\.(pem|key|p12|pfx)$/.test(name) && file.size <= LIMITS.fileBytes &&
      (textExtension.test(name) || ['dockerfile', 'makefile', '.gitignore'].includes(name));
  }
  function priority(file) {
    const path = pathOf(file), name = path.split('/').at(-1).toLowerCase();
    const depth = path.split('/').length;
    return (/^(package\.json|cargo\.toml|pyproject\.toml|readme\.md|requirements.*\.txt)$/.test(name) ? 0 :
      /^(main|app|server|index|start)([-.])/.test(name) ? 10 : /test|spec/.test(path) ? 20 : 30) + depth;
  }
  async function prepareFiles(files, progress = () => {}) {
    const candidates = files.filter(eligible).sort((a,b) => priority(a)-priority(b) || pathOf(a).localeCompare(pathOf(b)));
    const result = []; let bytes = 0, failed = 0;
    for (const file of candidates) {
      if (result.length >= LIMITS.files) break;
      if (bytes + file.size > LIMITS.totalBytes) continue;
      try {
        const buffer = await file.arrayBuffer();
        const text = new TextDecoder('utf-8', {fatal:true}).decode(buffer);
        if (text.includes('\0')) { failed++; continue; }
        bytes += buffer.byteLength;
        result.push({path:pathOf(file), content:text, bytes:buffer.byteLength});
      } catch (_) { failed++; }
      progress(result.length, candidates.length);
    }
    return {files:result, total_files:files.length, omitted:files.length-result.length, failed, bytes};
  }
  function normalize(text) { return text.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim(); }
  function confidenceLabel(value) {
    const numeric = Number.isFinite(Number(value)) ? Number(value) : 0;
    const score = Math.max(0, Math.min(1, numeric));
    if (score >= 0.75) return {label:'Alta', tone:'high', score};
    if (score >= 0.45) return {label:'Média', tone:'mid', score};
    return {label:'Baixa', tone:'low', score};
  }
  function confidenceText(value) {
    const item = confidenceLabel(value);
    return `${item.label} · ${item.score.toFixed(2)}`;
  }
  function route(value, hasAttachments = false, manual = null) {
    if (manual) return manual;
    const text = normalize(value);
    // Comandos explícitos têm precedência; conteúdo dos anexos nunca roteia ações.
    if (/^\/abrir\s+/.test(text) || /^https?:\/\//i.test(value)) return 'open_page';
    if (/^\/pesquisa\s+(guiada|profunda)\s+/.test(text)) return 'research_web';
    if (/^\/(pesquisar|pesquisa)\s+/.test(text)) return 'search_web';
    if (/^\/?(?:analise|analisa|revise|revisar|inspecione|inspecionar)\s+(?:o\s+)?(?:meu\s+|este\s+|esse\s+)?(?:projeto|workspace)\b/.test(text)) return 'inspect_project';
    if (/^\/arquivos(?:\s|$)/.test(text)) return 'list_files';
    if (/^\/buscar\s+/.test(text)) return 'search_files';
    if (hasAttachments || text.startsWith('/')) return null;
    if (/^(pesquise|busque na internet|procure na internet)\b/.test(text) ||
        /\b(noticias?|cotacao|clima|versao atual|preco atual)\b/.test(text)) return 'search_web';
    return null;
  }
  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
  }
  function formatMarkdown(text) {
    if (!text) return '';
    const codeBlocks = [];
    let processed = String(text).replace(/```([a-zA-Z0-9_\-+]*)\r?\n([\s\S]*?)```/g, (_, lang, code) => {
      const id = `___CODE_BLOCK_${codeBlocks.length}___`;
      const cleanLang = (lang || 'código').trim().toLowerCase();
      codeBlocks.push({ id, lang: cleanLang, code: code.replace(/\r?\n$/, '') });
      return id;
    });
    processed = escapeHtml(processed);
    processed = processed.replace(/^### (.*$)/gim, '<h3 class="md-h3">$1</h3>');
    processed = processed.replace(/^## (.*$)/gim, '<h2 class="md-h2">$1</h2>');
    processed = processed.replace(/^# (.*$)/gim, '<h1 class="md-h1">$1</h1>');
    processed = processed.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    processed = processed.replace(/\*(.+?)\*/g, '<em>$1</em>');
    processed = processed.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');
    processed = processed.replace(/^>(.*$)/gim, '<blockquote class="md-blockquote">$1</blockquote>');
    const lines = processed.split('\n');
    const resultLines = [];
    let inList = false;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const listMatch = line.match(/^(\s*)(?:[-*]|\d+\.)\s+(.+)$/);
      if (listMatch) {
        if (!inList) { resultLines.push('<ul class="md-list">'); inList = true; }
        resultLines.push(`<li class="md-list-item">${listMatch[2]}</li>`);
      } else {
        if (inList) { resultLines.push('</ul>'); inList = false; }
        resultLines.push(line);
      }
    }
    if (inList) resultLines.push('</ul>');
    processed = resultLines.join('\n');
    processed = processed.replace(/\n\n+/g, '</p><p>');
    processed = processed.replace(/\n/g, '<br>');
    processed = `<p>${processed}</p>`;
    processed = processed.replace(/<p>\s*<\/p>/g, '');
    for (const block of codeBlocks) {
      const escapedCode = escapeHtml(block.code);
      const lines = escapedCode.split('\n').map((line, index) => `<span class="code-line"><span class="code-number">${index + 1}</span>${line || ' '}</span>`).join('');
      const rawAttribute = escapeHtml(block.code);
      const lineCount = block.code.split('\n').length;
      const codeHtml = `<div class="code-block-wrap"><div class="code-block-header"><div class="code-block-heading"><span class="code-block-lang">${block.lang}</span><span class="code-block-lines">${lineCount} ${lineCount === 1 ? 'linha' : 'linhas'}</span></div><div class="code-block-actions"><button type="button" class="code-copy-btn" onclick="copyCodeBlock(this)">Copiar</button><button type="button" class="code-copy-btn" onclick="toggleCodeBlock(this)">Expandir</button><button type="button" class="code-copy-btn" onclick="openCodeOverlay(this)">Foco</button></div></div><pre class="code-block-pre" data-raw="${rawAttribute}"><code>${lines}</code></pre></div>`;
      processed = processed.replace(block.id, codeHtml);
    }
    processed = processed.replace(/<p>\s*(<div class="code-block-wrap">)/g, '$1');
    processed = processed.replace(/(<\/div>)\s*<\/p>/g, '$1');
    processed = processed.replace(/<p>\s*(<h[1-3][^>]*>)/g, '$1');
    processed = processed.replace(/(<\/h[1-3]>)\s*<\/p>/g, '$1');
    processed = processed.replace(/<p>\s*(<ul[^>]*>)/g, '$1');
    processed = processed.replace(/(<\/ul>)\s*<\/p>/g, '$1');
    processed = processed.replace(/<p>\s*(<blockquote[^>]*>)/g, '$1');
    processed = processed.replace(/(<\/blockquote>)\s*<\/p>/g, '$1');
    return processed;
  }
  const api = { LIMITS, eligible, prepareFiles, route, normalize, confidenceLabel, confidenceText, escapeHtml, formatMarkdown };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else scope.ChatCore = api;
})(globalThis);
