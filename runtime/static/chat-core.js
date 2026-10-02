/* Regras do compositor independentes do DOM, compartilhadas com os testes. */
(function (scope) {
  const LIMITS = { files: 60, fileBytes: 65536, totalBytes: 262144, mediaFileBytes: 16777216, mediaTotalBytes: 67108864 };
  const ignored = new Set(['node_modules', '.git', '.venv', 'venv', 'target', 'dist', 'build', '__pycache__', '.ia-local-backups', 'backups']);
  const textExtension = /\.(txt|md|markdown|rst|json|jsonl|csv|tsv|py|js|jsx|ts|tsx|rs|html|htm|css|scss|toml|yaml|yml|sql|sh|xml)$/i;
  const researchNoise = new Set([
    'a', 'o', 'as', 'os', 'um', 'uma', 'e', 'em', 'de', 'do', 'da', 'dos', 'das',
    'que', 'como', 'qual', 'quais', 'para', 'por', 'com', 'sobre', 'no', 'na', 'nos', 'nas',
    'posso', 'podemos', 'devo', 'devemos', 'tornar', 'atual', 'atualizada', 'atualizado',
    'oficial', 'oficiais', 'documentacao', 'documentacoes', 'fonte', 'fontes', 'link', 'links',
    'cite', 'traga', 'separe', 'distinga', 'pesquise', 'pesquisar', 'busque', 'buscar', 'procure',
    'procurar', 'investigue', 'investigar', 'the', 'and', 'with', 'how', 'can', 'for', 'from',
    'into', 'latest', 'official', 'documentation', 'docs', 'sources', 'source',
  ]);
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
  function isMediaFile(file) {
    return /\.(csv|tsv|pdf|docx?|xlsx?|pptx?|od[pts]|epub|rtf|eml|ipynb|png|jpe?g|webp|gif|bmp|tiff?|pbm|pgm|wav|mp3|ogg|flac|m4a|mp4|mov|mkv|webm)$/i.test(file.name || '');
  }
  async function prepareFiles(files, progress = () => {}, options = {}) {
    const candidates = files.filter(file => eligible(file) || (options.readBinary && isMediaFile(file)))
      .sort((a,b) => priority(a)-priority(b) || pathOf(a).localeCompare(pathOf(b)));
    const result = [], media = [], warnings = []; let bytes = 0, failed = 0, rawBytes = 0;
    for (const file of candidates) {
      if (result.length >= LIMITS.files) break;
      if (options.readBinary && isMediaFile(file)) {
        try {
          if (file.size > LIMITS.mediaFileBytes || rawBytes+file.size > LIMITS.mediaTotalBytes) throw new Error('Mídia acima do limite de leitura local.');
          const receipt = await options.readBinary(file);
          const entries = receipt.files || [];
          const textBytes = entries.reduce((sum, item) => sum+new TextEncoder().encode(item.content || '').length, 0);
          if (!entries.length || bytes+textBytes > LIMITS.totalBytes) throw new Error('O texto extraído excedeu o orçamento desta mensagem.');
          result.push(...entries); media.push(receipt); bytes+=textBytes; rawBytes+=file.size;
          warnings.push(...(receipt.warnings || []));
        } catch (error) { failed++; warnings.push(`${file.name}: ${error.message}`); }
        progress(result.length, candidates.length);
        continue;
      }
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
    return {files:result, total_files:files.length, omitted:files.length-result.length, failed, bytes,
      ...(media.length ? {media, raw_bytes:rawBytes} : {}), ...(warnings.length ? {warnings} : {})};
  }
  function agentAttachments(material) {
    return material.slice(0,8).map(item => {
      const files = item.files || [];
      if (files.length === 1) return {...files[0]};
      let content = files.map(file => `[Arquivo observado: ${file.path}]\n${file.content}`).join('\n\n');
      const truncated = content.length > 110000;
      content = content.slice(0,110000) + (truncated ? '\n[Conteúdo reduzido; releia os arquivos para confirmar detalhes.]' : '');
      return {path:(item.name || 'anexo.txt').replace(/\/$/,''), content, mediaType:'text/plain', kind:'text',
        warnings:[...(item.warnings || []), ...(item.omitted ? [`${item.omitted} arquivo(s) omitido(s).`] : [])]};
    });
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
  function isWorkspaceIdentityQuestion(value) {
    const text = normalize(value);
    if (/\b(?:analise|analisar|analisa|examine|examinar|inspecione|inspecionar|revise|revisar|investigue|investigar|crie|criar|edite|editar|corrija|corrigir|implemente|implementar|altere|alterar|modifique|modificar)\b/.test(text)) return false;
    const asksIdentity = /\b(?:qual\s+(?:(?:e\s+)?(?:o\s+)?)?(?:nome\s+do\s+)?(?:workspace|projeto|pasta|diretorio)|onde\s+(?:esta|fica)|em\s+qual\s+(?:workspace|projeto|pasta|diretorio)|em\s+que\s+(?:workspace|projeto|pasta|diretorio)|(?:nome|caminho)\s+do\s+(?:workspace|projeto|pasta|diretorio))\b/.test(text);
    const namesWorkspace = /\b(?:workspace|projeto|pasta|diretorio)\b/.test(text);
    const describesSelection = /\b(?:aberto|aberta|atual|selecionado|selecionada|ativo|ativa|esta|sessao)\b/.test(text);
    const describesWorkspace = /\b(?:aberto|aberta|atual|selecionado|selecionada|ativo|ativa|esta|sessao|usa|usando|fica)\b/.test(text);
    return namesWorkspace && asksIdentity && (describesSelection || describesWorkspace || /\b(?:nome|caminho)\s+do\s+(?:workspace|projeto|pasta|diretorio)\b/.test(text));
  }
  function isProjectUnderstandingRequest(value) {
    const text = normalize(value);
    const target = /\b(?:projeto|workspace|repositorio|repo|sistema|aplicativo|arquivos?|codigo)\b/.test(text);
    const asksUnderstanding = /\b(?:o que (?:e|faz|me diz)|sobre o que|conclusao|resumo|resuma|explique|explica|descreva|descreve|como funciona|para que serve|finalidade|objetivo)\b/.test(text);
    const asksAnalysis = /\b(?:analise|analisar|analisa|examine|examinar|examina|inspecione|inspecionar|inspeciona|revise|revisar|revisa|investigue|investigar|investiga)\b/.test(text);
    const asksProjectStatus = /\b(?:estado|situacao|status|panorama)\b|\bcomo esta\b/.test(text)
      && /\b(?:atual|agora|hoje|sistema|projeto|workspace|repositorio|repo|aplicativo)\b/.test(text);
    const asksMutation = /\b(?:crie|criar|edite|editar|corrija|corrigir|implemente|implementar|alterar|altere|modifique|modificar|apague|apagar|remova|remover)\b/.test(text);
    return target && (asksUnderstanding || asksAnalysis || asksProjectStatus) && !asksMutation;
  }
  function isResearchSynthesisRequest(value) {
    const text = normalize(value);
    const naturalResearch = /^(?:pesquise|busque na internet|procure na internet|investigue)\b/.test(text);
    const asksForSynthesis = /\b(?:documentacao oficial|fontes oficiais|traga (?:os |as )?links?|cite (?:as? )?fontes?|sintetize|resuma|explique|compare|como (?:posso|podemos|devo|devemos))\b/.test(text);
    const looksLikeQuestion = /^(?:qual|quais|quanto|quantos|quantas|quem|quando|onde|como|esta|existe|ha)\b/.test(text)
      || /[?¿]/.test(value);
    const volatileFact = /\b(?:hoje|agora|atual(?:mente)?|mais recente|recentes?|ultim[oa]s?|(?:esta|nesta|nessa|na)\s+semana|(?:este|neste|esse|nesse)\s+mes|(?:este|neste|esse|nesse)\s+ano|ultimos?\s+(?:7|30)\s+dias|ultimos?\s+12\s+meses|versao|preco|custa|cotacao|lancamento|release|presidente|primeiro ministro|ceo|clima|previsao do tempo|agenda|resultado|placar)\b/.test(text);
    return (naturalResearch && asksForSynthesis) || (looksLikeQuestion && volatileFact);
  }
  function isDiagnosticAdviceRequest(value) {
    const text = normalize(value);
    const asksForDiagnosis = /\b(?:qual hipotese|que hipotese|o que posso concluir|o que seria apenas hipotese|qual experimento|que experimento|qual checagem|que checagem|como investigar|como eu investigaria)\b/.test(text);
    const explicitlyTargetsWorkspace = /\b(?:inspecione|inspecionar|analise|analisar|examine|examinar|leia|ler|abra|abrir|revise|revisar|depure|depurar|debugue|debugar)\b.{0,120}\b(?:projeto|workspace|repositorio|arquivos?|codigo|src\/|[\w.-]+\.(?:py|ts|tsx|js|jsx|rs|cpp|h))\b/.test(text);
    return asksForDiagnosis && !explicitlyTargetsWorkspace;
  }
  function isProjectFailureReport(value) {
    const text = normalize(value);
    const failure = /\b(?:erros?|falh\w*|bugs?|quebr\w*|crash\w*|nao funciona|nao carrega|nao responde|nao abre|nao salva|nao envia|travou|trava|indisponivel|traceback|exception|unexpected token|invalid json|404|500)\b/.test(text);
    const target = /\b(?:pagina|interface|tela|botao|formulario|painel|site|web|app|aplicativo|aplicacao|sistema|projeto|workspace|arquivo|codigo|frontend|backend|api|index\.html)\b/.test(text);
    const adviceOnly = /\b(?:como (?:investigar|diagnosticar)|qual hipotese|que hipotese|o que posso concluir|(?:apenas|somente|so) (?:explique|analise)|sem (?:alterar|editar|modificar|corrigir))\b/.test(text);
    return failure && target && !adviceOnly;
  }
  function route(value, hasAttachments = false, manual = null) {
    if (manual) return manual;
    const text = normalize(value);
    // Comandos explícitos têm precedência; conteúdo dos anexos nunca roteia ações.
    if (/^\/abrir\s+/.test(text) || /^https?:\/\//i.test(value)) return 'open_page';
    if (/^\/pesquisa\s+(guiada|profunda)\s+/.test(text)) return 'research_web';
    if (/^\/(pesquisar|pesquisa)\s+/.test(text)) return 'search_web';
    if (isWorkspaceIdentityQuestion(text)) return 'inspect_project';
    // A análise de intenção precisa passar pelo ciclo do agente; `inspect_project`
    // sozinho só devolve metadados e não lê evidência suficiente para concluir.
    if (isProjectUnderstandingRequest(text)) return null;
    if (isDiagnosticAdviceRequest(text)) return 'conversation';
    if (/^\/?(?:analise|analisa|revise|revisar|inspecione|inspecionar)\s+(?:o\s+)?(?:meu\s+|este\s+|esse\s+)?(?:projeto|workspace)\b/.test(text)) return 'inspect_project';
    if (/^\/arquivos(?:\s|$)/.test(text)) return 'list_files';
    if (/^\/buscar\s+/.test(text)) return 'search_files';
    if (hasAttachments || text.startsWith('/')) return null;
    if (isResearchSynthesisRequest(text)) return 'research_web';
    if (/^(pesquise|busque na internet|procure na internet)\b/.test(text) ||
        /\b(noticias?|cotacao|clima|versao atual|preco atual)\b/.test(text)) return 'search_web';
    return null;
  }
  function researchQuery(value) {
    const original = String(value || '').trim();
    const normalized = normalize(original);
    const asksForOfficialSources = /\b(?:documentacao oficial|fontes oficiais|site oficial)\b/.test(normalized);
    let query = original
      .replace(/^(?:por favor[, ]*)?(?:\/pesquisa\s+(?:guiada|profunda)\s+|pesquise|pesquisar|busque|buscar|procure|procurar|investigue|investigar)\s+/i, '')
      .replace(/\s--salvar\b/ig, '')
      .replace(/(?:^|[.;?!\n])\s*(?:traga|forne[cç]a|inclua|cite|liste|separe|distinga|não altere|nao altere|não modifique|nao modifique)\b[\s\S]*$/i, '')
      .replace(/\bcomo\s+(?:posso|podemos|devo|devemos|eu posso|eu devo)\b/ig, '')
      .replace(/[?!.,;:]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    const topic = normalized;
    let domain = '';
    if (asksForOfficialSources && /\bcmake\b/.test(topic)) domain = 'cmake.org';
    else if (asksForOfficialSources && /\bpython\b/.test(topic)) domain = 'docs.python.org';
    else if (asksForOfficialSources && /\brust\b/.test(topic)) domain = 'doc.rust-lang.org';
    else if (asksForOfficialSources && /\btypescript\b/.test(topic)) domain = 'typescriptlang.org';
    else if (asksForOfficialSources && /\bnode(?:\.js)?\b/.test(topic)) domain = 'nodejs.org';
    const words = query.split(/\s+/).filter(word => {
      const folded = normalize(word);
      return folded.length >= 3 && !researchNoise.has(folded);
    });
    query = words.join(' ') || query;
    if (asksForOfficialSources && /\bcmake\b/.test(topic) && /\bfetchcontent\b/.test(topic)
        && /\b(?:reproduz|vers|fixar|fixe|pin|pinned)\w*\b/.test(topic)) {
      query = 'site:cmake.org FetchContent FetchContent_Declare GIT_TAG commit hash URL_HASH';
    }
    if (domain && !/\bsite:/i.test(query)) query = `site:${domain} ${query}`;
    return query.slice(0, 2000);
  }
  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
  }
  function formatMarkdown(text) {
    if (!text) return '';
    let tokenPrefix = '___BRASA_MARKDOWN_';
    while (String(text).includes(tokenPrefix)) tokenPrefix += '_';
    const codeBlocks = [];
    let processed = String(text).replace(/```([a-zA-Z0-9_\-+]*)\r?\n([\s\S]*?)```/g, (_, lang, code) => {
      const id = `${tokenPrefix}BLOCK_${codeBlocks.length}___`;
      const cleanLang = (lang || 'código').trim().toLowerCase();
      codeBlocks.push({ id, lang: cleanLang, code: code.replace(/\r?\n$/, '') });
      return '\n\n' + id + '\n\n';
    });
    processed = processed.replace(/^Execuções observadas:\s*\n(?=Primeira execução:)/gm, 'Execuções observadas:\n\n');
    processed = escapeHtml(processed);
    const inline = value => {
      const codes = [];
      value = value.replace(/(`+)([^\n]*?)\1(?!`)/g, (_, ticks, code) => {
        const id = `${tokenPrefix}INLINE_${codes.length}___`;
        codes.push(code.startsWith(' ') && code.endsWith(' ') && code.trim() ? code.slice(1, -1) : code);
        return id;
      });
      value = value.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\*(.+?)\*/g, '<em>$1</em>');
      codes.forEach((code, i) => { value = value.replace(`${tokenPrefix}INLINE_${i}___`, () => `<code class="inline-code">${code}</code>`); });
      return value;
    };
    const blocks = [];
    let paragraph = [], list = [], listKind = '';
    const flushParagraph = () => {
      if (paragraph.length) blocks.push({raw: paragraph.join('\n'), html: '<p>' + paragraph.map(inline).join('<br>') + '</p>'});
      paragraph = [];
    };
    const flushList = () => {
      if (list.length) blocks.push({html: `<${listKind} class="md-list">` + list.map(value => `<li class="md-list-item">${inline(value)}</li>`).join('') + `</${listKind}>`});
      list = []; listKind = '';
    };
    const codeIds = new Set(codeBlocks.map(block => block.id));
    for (const line of processed.replace(/\r\n/g, '\n').split('\n')) {
      if (!line.trim()) { flushParagraph(); flushList(); continue; }
      if (codeIds.has(line.trim())) {
        flushParagraph(); flushList();
        const previous = blocks.at(-1);
        if (previous?.raw && /^(?:Primeira|Última) execução: [^\n]+$/.test(previous.raw)) {
          blocks.pop();
          blocks.push({html: `<details class="verification-evidence"><summary>${inline(previous.raw)}</summary>${line.trim()}</details>`});
        } else blocks.push({html: line.trim()});
        continue;
      }
      const heading = line.match(/^(#{1,3})\s+(.+)$/);
      const quote = line.match(/^&gt;\s?(.*)$/);
      if (heading || quote) {
        flushParagraph(); flushList();
        blocks.push({html: heading ? `<h${heading[1].length} class="md-h${heading[1].length}">${inline(heading[2])}</h${heading[1].length}>`
          : `<blockquote class="md-blockquote">${inline(quote[1])}</blockquote>`});
        continue;
      }
      const item = line.match(/^\s*(?:([-*])|\d+\.)\s+(.+)$/);
      if (item) {
        flushParagraph();
        const kind = item[1] ? 'ul' : 'ol';
        if (listKind && listKind !== kind) flushList();
        listKind = kind; list.push(item[2]);
      } else { flushList(); paragraph.push(line); }
    }
    flushParagraph(); flushList();
    processed = blocks.map(block => block.html).join('\n');
    for (const block of codeBlocks) {
      const escapedCode = escapeHtml(block.code);
      const lines = escapedCode.split('\n').map((line, index) => `<span class="code-line"><span class="code-number">${index + 1}</span>${line || ' '}</span>`).join('');
      const rawAttribute = escapeHtml(block.code);
      const lineCount = block.code.split('\n').length;
      const codeHtml = `<div class="code-block-wrap"><div class="code-block-header"><div class="code-block-heading"><span class="code-block-lang">${block.lang}</span><span class="code-block-lines">${lineCount} ${lineCount === 1 ? 'linha' : 'linhas'}</span></div><div class="code-block-actions"><button type="button" class="code-copy-btn" onclick="copyCodeBlock(this)">Copiar</button><button type="button" class="code-copy-btn" onclick="toggleCodeBlock(this)">Expandir</button><button type="button" class="code-copy-btn" onclick="openCodeOverlay(this)">Foco</button></div></div><pre class="code-block-pre" data-raw="${rawAttribute}"><code>${lines}</code></pre></div>`;
      processed = processed.replace(block.id, () => codeHtml);
    }
    return processed;
  }
  function formatAgentReport(report = {}, options = {}) {
    const rawAnswer = String(report.finalText || '').trim();
    const genericAnswer = /^(?:estou acompanhando\. pode me contar um pouco mais\?|a tarefa n[aã]o foi conclu[ií]da\.?|conclu[ií] a etapa\b)/i.test(rawAnswer);
    let answer = genericAnswer ? '' : rawAnswer;
    let history = '';
    const historyStart = answer.search(/\n\nExecuções observadas:\s*\n(?=Primeira execução:)/);
    if (report.verification?.executed === true && historyStart >= 0 && answer.slice(historyStart).includes('Última execução:')) {
      history = answer.slice(historyStart).trim();
      answer = answer.slice(0, historyStart).trim();
    }
    if (report.status === 'completed' && report.verification?.executed === true && report.verification.passed === true
        && /^A alteração solicitada foi aplicada no workspace e a verificação passou(?: \([^)]+\))?\.$/.test(answer)) {
      answer = 'Concluí a alteração solicitada.';
    }
    const events = Array.isArray(report.events) ? report.events : [];
    const observedFiles = [...new Set(events.filter(item => item?.kind === 'engineering.context.observed' && item.status === 'completed')
      .map(item => String(item.detail || '').trim()).filter(Boolean))].slice(0, 4);
    const concreteBlocker = [...events].reverse().find(item => item?.status === 'blocked'
      && !['acceptance.failed', 'task.blocked'].includes(item.kind)
      && typeof item.detail === 'string' && item.detail.trim());
    const noAnswerSummary = report.status === 'completed' ? 'Tarefa concluída.'
      : `A investigação não foi concluída.${observedFiles.length ? ` Arquivos lidos: ${observedFiles.join(', ')}.` : ' Nenhum arquivo foi confirmado como lido.'}`;
    const pendingEffects = [
      /\b(?:debug|build|testing)\.change\b/.test(String(report.error || '')) ? 'Nenhuma alteração em arquivo foi confirmada.' : '',
      /\b(?:debug|build|testing)\.verification\b/.test(String(report.error || '')) ? 'A alteração ainda não passou por uma verificação executada.' : '',
    ].filter(Boolean).join(' ');
    const sources = options.includeSources !== false ? [...new Map((report.evidence || [])
      .filter(item => item && item.url && !answer.includes(item.url))
      .map(item => [item.url, item])).values()]
      .slice(0, 8)
      .map(item => `- ${item.title || item.url}: ${item.url}`)
      .join('\n') : '';
    const artifacts = [...new Set((report.artifacts || [])
      .map(item => String(item?.path || '').trim()).filter(Boolean))]
      .slice(0, 8)
      .map(path => {
        const ticks = '`'.repeat(Math.max(0, ...(path.match(/`+/g) || []).map(run => run.length)) + 1);
        const padding = /^`|`$/.test(path) ? ' ' : '';
        return `- ${ticks}${padding}${path}${padding}${ticks}`;
      })
      .join('\n');
    const verification = report.verification;
    const verificationSummary = String(verification?.summary || '').replace(/[.!?]+$/, '');
    const verificationText = verification && verification.executed === true
      ? report.status !== 'completed' && verification.passed
        ? 'Os checks do projeto passaram; a resolução do problema ainda não foi confirmada.'
        : Number.isInteger(verification.testsExecuted) && verification.testsExecuted > 0
          ? `**Testes:** ${verification.testsExecuted} ${verification.passed ? (verification.testsExecuted === 1 ? 'aprovado' : 'aprovados') : (verification.testsExecuted === 1 ? 'executado; a suíte falhou' : 'executados; a suíte falhou')}${verification.check ? ` (${verification.check})` : ''}.`
          : `**Verificação:** ${verification.passed ? 'aprovada' : 'falhou'}${verification.check ? ` (${verification.check})` : ''}${verificationSummary && !/^verifica[cç][aã]o (?:aprovada|conclu[ií]da|falhou)$/i.test(verificationSummary) ? ` — ${verificationSummary}` : ''}.`
      : '';
    const parts = [
      answer || noAnswerSummary,
      !answer && concreteBlocker ? `Bloqueio observado: ${concreteBlocker.detail}` : '',
      !answer ? pendingEffects : '',
      artifacts ? `Arquivos entregues:\n\n${artifacts}` : '',
      verificationText,
      history,
      sources ? `Fontes consultadas:\n\n${sources}` : '',
      report.status !== 'completed' && report.error && !/\b(?:delivery\.summary|(?:debug|build|testing)\.(?:change|verification))\b/.test(report.error)
        ? `Motivo: ${report.error}` : '',
    ];
    return parts.filter(Boolean).join('\n\n');
  }

  function agentReportMeta(report = {}, awaitingApproval = false) {
    return 'Brasa · ' + (awaitingApproval ? 'Aguardando aprovação' : report.status === 'completed' ? 'Concluído' : 'Não concluído');
  }

  const api = { LIMITS, eligible, prepareFiles, isMediaFile, agentAttachments, route, researchQuery, isResearchSynthesisRequest, isDiagnosticAdviceRequest, isProjectFailureReport, normalize, isWorkspaceIdentityQuestion, isProjectUnderstandingRequest, confidenceLabel, confidenceText, escapeHtml, formatMarkdown, formatAgentReport, agentReportMeta };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else scope.ChatCore = api;
})(globalThis);
