const {test} = require('node:test');
const assert = require('node:assert/strict');
const {prepareFiles, route, researchQuery, isResearchSynthesisRequest, isDiagnosticAdviceRequest, eligible, LIMITS, confidenceLabel, confidenceText, formatAgentReport} = require('../runtime/static/chat-core.js');
function file(path, text='hello', extra={}) {
  const data=Buffer.from(text);
  return {name:path.split('/').at(-1),webkitRelativePath:path,size:data.length,arrayBuffer:async()=>data,...extra};
}
test('mídia binária chega como evidência somente após o leitor local responder',async()=> {
  let release, done=false;
  const result=prepareFiles([file('anexo.pdf','binary')],()=>{}, {readBinary:()=>new Promise(resolve=>{release=resolve;})})
    .then(value=>{done=true; return value;});
  await Promise.resolve(); assert.equal(done,false);
  release({files:[{path:'anexo.pdf',content:'PDF-42',assetId:'a'.repeat(64)}],warnings:[]});
  const prepared=await result;
  assert.equal(prepared.files[0].content,'PDF-42'); assert.equal(prepared.raw_bytes,6);
  assert.equal(require('../runtime/static/chat-core.js').agentAttachments([prepared])[0].assetId,'a'.repeat(64));
});
test('limites de mídia e falha do leitor são visíveis sem marcar arquivo como lido',async()=> {
  let calls=0;
  const result=await prepareFiles([file('grande.mp4','',{size:LIMITS.mediaFileBytes+1})],()=>{}, {readBinary:async()=>{calls++;}});
  assert.equal(calls,0); assert.equal(result.failed,1); assert.equal(result.files.length,0);
  assert.ok(result.warnings.length);
});
test('pasta permanece um anexo só e conteúdo dos arquivos não se torna uma ordem',()=> {
  const {agentAttachments}=require('../runtime/static/chat-core.js');
  const attachments=agentAttachments([{name:'projeto/',files:Array.from({length:20},(_,i)=>({path:`src/${i}.py`,content:'print(42)'}))}]);
  assert.equal(attachments.length,1); assert.equal(attachments[0].path,'projeto');
  assert.match(attachments[0].content,/Arquivo observado: src\/19.py/);
});
test('pergunta sobre projeto anexado não dispara busca por palavras vagas',()=> {
  for(const text of ['O que acha desse projeto de sistema pra perfumaria?','Analise agora o preço dos produtos neste código','Hoje quero entender esse projeto']) assert.equal(route(text,true),null);
});
test('comandos e escolha manual continuam disponíveis',()=> {
  assert.equal(route('/pesquisar rust',true),'search_web');
  assert.equal(route('/abrir https://example.com'),'open_page');
  assert.equal(route('/arquivos'),'list_files');
  assert.equal(route('Analise meu projeto'),null);
  assert.equal(route('notícias de hoje'),'search_web');
  assert.equal(route('teste',true,'search_files'),'search_files');
  assert.equal(route('/criar arquivo hoje.txt\nOlá'),null);
});
test('pedido natural de pesquisa com síntese usa pesquisa guiada e reduz a consulta ao assunto',()=> {
  const prompt='Pesquise a documentação oficial atual do CMake sobre FetchContent. Como posso fixar versões para tornar as dependências reproduzíveis? Traga os links consultados e separe o que a documentação confirma do que é sua recomendação. Não altere arquivos.';
  assert.equal(route(prompt),'research_web');
  assert.equal(isResearchSynthesisRequest(prompt),true);
  assert.equal(researchQuery(prompt),'site:cmake.org FetchContent FetchContent_Declare GIT_TAG commit hash URL_HASH');
  assert.equal(route('Pesquise notícias de hoje'),'search_web');
  assert.equal(route('/pesquisa guiada CMake FetchContent'),'research_web');
});
test('perguntas de diagnóstico sem pedido de inspeção seguem pela conversa comum',()=> {
  const first='Estou depurando um app que inicia normalmente, mas às vezes a janela não aparece e a porta continua ocupada. Não altere arquivos. Qual hipótese você investigaria primeiro, que informação ainda falta e qual checagem segura faria?';
  const third='Sem consultar arquivos, ferramentas ou internet: um erro só aparece depois de várias execuções, mas ainda não tenho logs. O que posso concluir, o que seria apenas hipótese e qual experimento simples ajudaria a distinguir as causas?';
  assert.equal(isDiagnosticAdviceRequest(first),true);
  assert.equal(isDiagnosticAdviceRequest(third),true);
  assert.equal(route(first), 'conversation');
  assert.equal(route(third), 'conversation');
});
test('a leitura só termina depois de obter conteúdo dos arquivos',async()=> {
  let release, done=false;
  const result=prepareFiles([file('projeto/README.md','',{arrayBuffer:()=>new Promise(resolve=>{release=resolve;})})]).then(value=>{done=true;return value;});
  await Promise.resolve(); assert.equal(done,false);
  release(Buffer.from('# Projeto real'));
  assert.equal((await result).files[0].content,'# Projeto real');
});
test('dependências, credenciais e saídas não consomem orçamento',async()=> {
  const result=await prepareFiles(['p/node_modules/a.js','p/.env','p/.git/a.md','p/target/b.rs','p/private.key','p/package-lock.json','p/src/app.ts'].map(path=>file(path)));
  assert.deepEqual(result.files.map(f=>f.path),['p/src/app.ts']); assert.equal(result.omitted,6);
});
test('prioriza manifestos e entradas antes de atingir o limite',async()=> {
  const files=Array.from({length:70},(_,i)=>file(`p/src/m${i}.js`)); files.push(file('p/package.json','{}'),file('p/README.md','# App'));
  const result=await prepareFiles(files);
  assert.equal(result.files.length,60); assert.equal(result.omitted,12);
  assert.ok(result.files.slice(0,2).some(f=>f.path==='p/package.json'));
});
test('texto permanece íntegro e o orçamento de bytes é respeitado',async()=> {
  const text='á'.repeat(30000);
  const result=await prepareFiles(Array.from({length:6},(_,i)=>file(`p/f${i}.md`,text)));
  assert.equal(result.files.length,4); assert.ok(result.bytes<=LIMITS.totalBytes);
  assert.equal(result.files[0].content,text);
});
test('etapa de planejamento informa confiança em escala curta',()=> {
  assert.deepEqual(confidenceLabel(0.9), {label:'Alta', tone:'high', score:0.9});
  assert.deepEqual(confidenceLabel(0.5), {label:'Média', tone:'mid', score:0.5});
  assert.equal(confidenceText(0.9), 'Alta · 0.90');
});
test('resposta do AgentCore omite trilha interna e não repete fontes já citadas',()=> {
  const url='https://cmake.org/cmake/help/latest/module/FetchContent.html';
  const report={
    status:'completed',
    finalText:`Fato confirmado.\n\nFonte: ${url}`,
    evidence:[{title:'FetchContent — CMake',url}],
    events:[{phase:'learn',title:'Trilha operacional registrada'}],
    inspection:{files:Array(400),manifests:[],testFiles:Array(48)},
  };
  const answer=formatAgentReport(report);
  assert.equal(answer,report.finalText);
  assert.doesNotMatch(answer,/Etapas executadas|Inspeção:|Trilha operacional|Estado do AgentCore/);
});
test('resposta do AgentCore mantém fontes ausentes da síntese e motivo de bloqueio',()=> {
  const source={title:'Manual oficial',url:'https://example.org/docs'};
  const answer=formatAgentReport({
    status:'blocked',finalText:'Não foi possível concluir.',evidence:[source],
    error:'Selecione um workspace.',
  });
  assert.match(answer,/Não foi possível concluir\./);
  assert.match(answer,/Manual oficial: https:\/\/example\.org\/docs/);
  assert.match(answer,/Motivo: Selecione um workspace\./);
});
test('resposta de build apresenta artefatos e verificação sem duplicar pontuação',()=> {
  const answer=formatAgentReport({
    status:'completed',
    finalText:'A alteração solicitada foi aplicada no workspace e a verificação passou (npm-test).',
    artifacts:[{path:'index.html'},{path:''},{path:'app.js'}],
    verification:{executed:true,passed:true,summary:'Verificação concluída.'},
  });
  assert.match(answer,/Arquivos entregues:\n\n- `index\.html`\n- `app\.js`/);
  assert.match(answer,/\*\*Verificação:\*\* aprovada\./);
  assert.doesNotMatch(answer,/concluída\.\./);
  assert.equal((answer.match(/index\.html/g) || []).length, 1);
});

test('lista de arquivos não contém quebras ou parágrafos inválidos dentro de ul',()=> {
  const {formatMarkdown}=require('../runtime/static/chat-core.js');
  const html=formatMarkdown(formatAgentReport({status:'completed',finalText:'Concluído.',
    artifacts:[{path:'converter.py'},{path:'tests/test_converter.py'}],
    verification:{executed:true,passed:true,testsExecuted:3,check:'unittest',summary:'Verificação aprovada.'}}));
  const list=html.match(/<ul[^>]*>([\s\S]*?)<\/ul>/)[1];
  assert.equal((list.match(/<li\b/g)||[]).length,2);
  assert.doesNotMatch(list,/<br>|<p>|<\/p>/);
  assert.doesNotMatch(html,/<p>[^<]*(?:<br>)?<ul|<p><div/);
  assert.match(html,/<strong>Testes:<\/strong> 3 aprovados \(unittest\)/);
  assert.doesNotMatch(html,/passou — Verificação aprovada|\\n|<li[^>]*>\s*<\/li>/);
});

test('evidências permanecem completas em detalhes recolhidos e resumo vem antes dos logs',()=> {
  const {formatMarkdown}=require('../runtime/static/chat-core.js');
  const history='\n\nExecuções observadas:\nPrimeira execução: falhou · 3 teste(s).\n```text\nRan 3 tests\nFAILED (failures=3)\n```\n\nÚltima execução: aprovada · 3 teste(s).\n```text\nRan 3 tests\nOK\n```';
  const answer=formatAgentReport({status:'completed',finalText:'Concluído.'+history,
    artifacts:[{path:'converter.py'}],verification:{executed:true,passed:true,testsExecuted:3,check:'unittest'}});
  assert.ok(answer.indexOf('Arquivos entregues:')<answer.indexOf('Execuções observadas:'));
  const html=formatMarkdown(answer);
  assert.equal((html.match(/<details class="verification-evidence">/g)||[]).length,2);
  assert.doesNotMatch(html,/<details[^>]*\bopen\b/);
  assert.match(html,/FAILED \(failures=3\)/);
  assert.match(html,/>OK<\/span>/);
  assert.equal((formatMarkdown('```python\nprint(42)\n```').match(/verification-evidence/g)||[]).length,0);
});

test('código literal e nomes com marcadores não viram ênfase ou HTML executável',()=> {
  const {formatMarkdown}=require('../runtime/static/chat-core.js');
  const html=formatMarkdown('Use `a*b*` e `$&`.\n\n```js\nconst literal = "\\n $& ___BRASA_MARKDOWN_";\n</code><img src=x onerror=alert(1)>\n```');
  assert.match(html,/<code class="inline-code">a\*b\*<\/code>/);
  assert.match(html,/<code class="inline-code">\$&amp;<\/code>/);
  assert.match(html,/\\n \$&amp; ___BRASA_MARKDOWN_/);
  assert.doesNotMatch(html,/<img\b|<em>/);
  assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);
});

test('conclusão distingue testes falhos, checks anteriores e execução ausente',()=> {
  const {agentReportMeta}=require('../runtime/static/chat-core.js');
  const failed=formatAgentReport({status:'blocked',finalText:'A correção está pendente.',
    verification:{executed:true,passed:false,testsExecuted:3,check:'unittest'}});
  assert.match(failed,/3 executados; a suíte falhou/);
  assert.doesNotMatch(failed,/3 aprovados/);
  const absent=formatAgentReport({status:'blocked',finalText:'Não foi possível executar.',
    verification:{executed:false,passed:true,testsExecuted:3}});
  assert.doesNotMatch(absent,/aprovad|\*\*Testes/);
  assert.match(formatAgentReport({status:'blocked',finalText:'Pendência na entrega.',
    verification:{executed:true,passed:true,testsExecuted:3}}),/resolução do problema ainda não foi confirmada/);
  assert.equal(agentReportMeta({status:'blocked'},true),'Brasa · Aguardando aprovação');
  assert.equal(agentReportMeta({status:'completed'}),'Brasa · Concluído');
});
test('rejeita binário, UTF-8 inválido, mídia e arquivos grandes',async()=> {
  const result=await prepareFiles([file('a.md','a\0b'),file('b.md','',{arrayBuffer:async()=>Buffer.from([255])}),file('img.png','pixels'),file('big.md','x'.repeat(65537))]);
  assert.equal(result.files.length,0); assert.equal(result.omitted,4); assert.equal(result.failed,2);
  assert.equal(eligible(file('p/Cargo.toml')),true);
});

test('formatMarkdown converte codigo em bloco com botao de copiar e elementos basicos', () => {
  const { formatMarkdown } = require('../runtime/static/chat-core.js');
  const input = "Explicação:\n```python\ndef soma(a, b):\n    return a + b\n```\nUse `soma(1, 2)` com **segurança**.";
  const html = formatMarkdown(input);
  assert.ok(html.includes('class="code-block-wrap"'));
  assert.ok(html.includes('<span class="code-block-lang">python</span>'));
  assert.ok(html.includes('onclick="copyCodeBlock(this)"'));
  assert.ok(html.includes('def soma(a, b):'));
  assert.ok(html.includes('<code class="inline-code">soma(1, 2)</code>'));
  assert.ok(html.includes('<strong>segurança</strong>'));
});
