const {test} = require('node:test');
const assert = require('node:assert/strict');
const {prepareFiles, route, eligible, LIMITS, confidenceLabel, confidenceText} = require('../runtime/static/chat-core.js');
function file(path, text='hello', extra={}) {
  const data=Buffer.from(text);
  return {name:path.split('/').at(-1),webkitRelativePath:path,size:data.length,arrayBuffer:async()=>data,...extra};
}
test('pergunta sobre projeto anexado não dispara busca por palavras vagas',()=> {
  for(const text of ['O que acha desse projeto de sistema pra perfumaria?','Analise agora o preço dos produtos neste código','Hoje quero entender esse projeto']) assert.equal(route(text,true),null);
});
test('comandos e escolha manual continuam disponíveis',()=> {
  assert.equal(route('/pesquisar rust',true),'search_web');
  assert.equal(route('/abrir https://example.com'),'open_page');
  assert.equal(route('/arquivos'),'list_files');
  assert.equal(route('Analise meu projeto'),'inspect_project');
  assert.equal(route('notícias de hoje'),'search_web');
  assert.equal(route('teste',true,'search_files'),'search_files');
  assert.equal(route('/criar arquivo hoje.txt\nOlá'),null);
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

