/* Verificação sem pacotes npm: Node 22+ e Chrome com protocolo DevTools. */
const {spawn}=require('node:child_process');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const assert=require('node:assert/strict');
const base=process.env.IA_TEST_URL || 'http://127.0.0.1:3000';
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=> {
  const temp=await fs.mkdtemp(path.join(os.tmpdir(),'ia-chat-browser-'));
  const fixture=path.join(temp,'Perfumaria'); await fs.mkdir(fixture);
  await fs.writeFile(path.join(fixture,'package.json'),JSON.stringify({dependencies:{express:'test'},scripts:{start:'node server.js'}}));
  await fs.writeFile(path.join(fixture,'server.js'),"const app = express();\napp.get('/produtos', handler);\napp.post('/vendas', handler);\n");
  await fs.writeFile(path.join(fixture,'README.md'),'# Perfumaria\nControle de estoque e vendas.');
  await fs.mkdir(path.join(fixture,'node_modules')); await fs.writeFile(path.join(fixture,'node_modules','ignore.js'),'noticias hoje');
  const chrome=spawn(process.env.CHROME_BIN || '/usr/bin/google-chrome',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',`--user-data-dir=${path.join(temp,'profile')}`,'about:blank'],{stdio:'ignore'});
  let ws, seq=0; const pending=new Map(), errors=[], requests=[], paused=[];
  try {
    let port;
    for(let i=0;i<100;i++) { try { port=(await fs.readFile(path.join(temp,'profile','DevToolsActivePort'),'utf8')).split('\n')[0]; break; } catch (_) { await sleep(100); } }
    assert.ok(port,'Chrome não iniciou');
    const targets=await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
    ws.onmessage=event=> {
      const message=JSON.parse(event.data);
      if(message.id) { const task=pending.get(message.id); pending.delete(message.id); if(task) { clearTimeout(task.timer); message.error?task.reject(new Error(JSON.stringify(message.error))):task.resolve(message.result); } }
      if(message.method==='Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text+': '+message.params.exceptionDetails.exception?.description);
      if(message.method==='Fetch.requestPaused') paused.push(message.params.requestId);
      if(message.method==='Network.requestWillBeSent') requests.push(message.params.request);
    };
    function cdp(method,params={}) { return new Promise((resolve,reject)=> { const id=++seq; const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`CDP timeout: ${method}`));},12000); pending.set(id,{resolve,reject,timer}); ws.send(JSON.stringify({id,method,params})); }); }
    async function evaluate(expression) {
      const result=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
      if(result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
      return result.result.value;
    }
    async function waitFor(expression,label,timeout=8000) { const started=Date.now(); while(Date.now()-started<timeout) { if(await evaluate(expression)) return; await sleep(75); } let debug=''; try { debug=JSON.stringify(await evaluate("({sending,live:[...liveActions.keys()],timeline:timeline.slice(-4),last:conversation.at(-1)?.content})")); } catch (_) {} throw new Error(`Não aconteceu: ${label} ${debug}`); }
    async function screenshot(name) { const result=await cdp('Page.captureScreenshot',{format:'png'}); await fs.writeFile(path.join(temp,name),Buffer.from(result.data,'base64')); }
    await cdp('Runtime.enable'); await cdp('Page.enable'); await cdp('Network.enable');
    await cdp('Emulation.setDeviceMetricsOverride',{width:1440,height:960,deviceScaleFactor:1,mobile:false});
    await cdp('Page.navigate',{url:base});
    await waitFor("typeof send === 'function'",'interface carregar');
    await evaluate("input.value='Olá'; send()");
    await waitFor("!sending && document.querySelector('.action-message.done')",'saudação terminar');
    assert.ok(/Conversa local|Memória curada/.test(await evaluate("chat.textContent")));
    const sizes=await evaluate("(()=>{const e=document.querySelector('.message.user .bubble'); return [e.clientWidth,e.clientHeight,document.documentElement.scrollHeight,innerHeight]})()");
    assert.ok(sizes[0]>45 && sizes[1]<80,'Olá não deve virar uma letra por linha');
    assert.equal(sizes[2],sizes[3],'somente o chat deve rolar');
    await evaluate("document.getElementById('attachment-input').dataset.kind='directory';document.getElementById('attachment-input').webkitdirectory=true;");
    const doc=await cdp('DOM.getDocument'); const element=await cdp('DOM.querySelector',{nodeId:doc.root.nodeId,selector:'#attachment-input'});
    await cdp('DOM.setFileInputFiles',{nodeId:element.nodeId,files:[fixture]});
    await waitFor('attachments.length===1','pasta anexada');
    await evaluate("input.value='O que acha desse projeto de sistema pra perfumaria?'; send()");
    await waitFor("!sending && chat.textContent.includes('GET /produtos')",'análise do projeto');
    let content=await evaluate('chat.textContent');
    assert.ok(content.includes('server.js:2')); assert.ok(content.includes('Análise estática dos anexos'));
    assert.ok(content.includes('1 omitido'));
    const posts=requests.filter(r=>r.url.endsWith('/api/chat') && r.postData).map(r=>JSON.parse(r.postData));
    const lastUser=posts.at(-1).messages.at(-1);
    assert.equal(lastUser.content,'O que acha desse projeto de sistema pra perfumaria?');
    assert.equal(lastUser.attachments[0].files.length,3);
    assert.ok(!requests.some(r=>r.postData && r.postData.includes('"tool":"search_web"')),'anexo não pode disparar pesquisa');
    await evaluate("input.value='E os testes desse projeto?'; send()");
    await waitFor("!sending && document.querySelectorAll('.action-message.done').length===3",'continuação');
    assert.ok((await evaluate("conversation.at(-1).content")).includes('teste funcional'));
    await screenshot('project-review.png');
    const id=await evaluate('currentConversationId');
    await evaluate(`newChat(); loadConversation(${JSON.stringify(id)})`);
    assert.equal(await evaluate("document.querySelectorAll('.action-message.done').length"),3);
    assert.ok((await evaluate('chat.textContent')).includes('server.js:2'));
    await cdp('Network.setBlockedURLs',{urls:[`${base}/api/chat`]});
    await evaluate("input.value='Uma nova pergunta';send()");
    await waitFor("!sending && document.querySelector('.action-message.error')",'erro de rede finalizar');
    assert.equal(await evaluate("document.querySelector('.send').disabled"),false);
    await cdp('Network.setBlockedURLs',{urls:[]});
    // O canal de eventos pode falhar; a resposta principal ainda encerra o cartão.
    await cdp('Network.setBlockedURLs',{urls:[`${base}/api/activity*`]});
    await evaluate("input.value='Olá';send()");
    await waitFor("!sending && timeline.at(-2)?.state==='done'",'resposta sem polling');
    await cdp('Network.setBlockedURLs',{urls:[]});
    await evaluate("window.__originalFetch = window.fetch; window.fetch = (url, options={}) => { if (!String(url).includes('/api/tool-call')) return window.__originalFetch(url, options); return new Promise((resolve, reject) => { const signal=options.signal; if (signal?.aborted) return reject(new DOMException('Aborted','AbortError')); signal?.addEventListener('abort', () => reject(new DOMException('Aborted','AbortError')), {once:true}); }); }");
    const deadlineStart=Date.now();
    await evaluate("void requestWithActivity('/api/tool-call',{},'timeout-test','Timeout de teste',true)");
    await waitFor("!liveActions.has('timeout-test') && timeline.some(item=>item.state==='error')",'limite de 10 segundos',13000);
    const deadlineElapsed=Date.now()-deadlineStart;
    assert.ok(deadlineElapsed>=9500 && deadlineElapsed<12500,`deadline observado: ${deadlineElapsed} ms`);
    assert.ok((await evaluate("timeline.find(item=>item.title==='Timeout de teste')?.log")).includes('10 s'));
    assert.equal(await evaluate("document.querySelector('.send').disabled"),false);
    await evaluate("window.fetch = window.__originalFetch");
    await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);
    await screenshot('mobile.png');
    assert.deepEqual(errors,[],'não deve haver exceções JavaScript');
    console.log('PASSOU: saudação, layout, pasta real, roteamento, continuação, histórico, falha de rede, falha dos eventos, timeout de 10 s e tela estreita.');
    console.log(`Capturas: ${temp}`);
  } finally {
    if(ws) ws.close(); chrome.kill('SIGTERM');
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
