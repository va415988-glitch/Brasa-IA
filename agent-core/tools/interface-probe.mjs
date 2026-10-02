// A bounded simulated DOM for the counter benchmark. No browser rendering claim.
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const fixture=JSON.parse(readFileSync(0,'utf8'));
const context=vm.createContext(Object.create(null),{codeGeneration:{strings:false,wasm:false}});
vm.runInContext(`
const rows=${JSON.stringify(fixture.elements)};
const elements=rows.map(row=>({tagName:row.tag.toUpperCase(),attrs:row.attrs,
  id:row.attrs.id||'',textContent:row.text,value:row.attrs.value||'',disabled:false,
  listeners:{},addEventListener(type,handler){this.listeners[type]=handler;},
  setAttribute(name,value){this.attrs[name]=String(value);},getAttribute(name){return this.attrs[name]??null;}}));
const document={getElementById(id){return elements.find(row=>row.id===id)||null;},
  querySelector(selector){return selector.startsWith('#')?this.getElementById(selector.slice(1)):elements.find(row=>row.tagName===selector.toUpperCase())||null;},
  querySelectorAll(selector){return elements.filter(row=>row.tagName===selector.toUpperCase());}};
let storageFailure=false;
const storage={counter:'3'};
const localStorage={getItem(key){return storage[key]??null;},setItem(key,value){if(storageFailure)throw Error('quota');storage[key]=String(value);}};
const window=globalThis;
`,context,{timeout:100});
try {
  for(const source of fixture.scripts) vm.runInContext(source,context,{timeout:100});
  const result=vm.runInContext(`
  (()=>{
    const find=pattern=>elements.find(row=>row.tagName==='BUTTON'&&pattern.test(row.textContent));
    const add=find(/adicionar|somar|aumentar|\\+/i),remove=find(/remover|diminuir|menos/i),reset=find(/zerar|reset|reiniciar/i);
    const output=elements.find(row=>row.tagName==='OUTPUT')||elements.find(row=>row.attrs['aria-live']);
    const tests=[];
    function check(name,expected){const actual=Number(output?.textContent);tests.push({name,passed:actual===expected,expected,actual});}
    function click(button){if(!button)throw Error('Ação sem botão semântico');if(!button.disabled)button.listeners.click?.({target:button});}
    check('restores saved count',3);click(add);check('adds',4);click(remove);check('removes',3);click(reset);check('resets',0);
    click(remove);check('nonnegative',0);storageFailure=true;click(add);check('storage error preserves usable state',1);
    const status=elements.find(row=>row.attrs.role==='status');
    tests.push({name:'storage error is reported',passed:Boolean(status?.textContent?.trim())});
    return JSON.stringify({executed:true,passed:tests.every(row=>row.passed),tests});
  })()
  `,context,{timeout:100});
  process.stdout.write(result);
} catch(error) {
  process.stdout.write(JSON.stringify({executed:true,passed:false,error:String(error).slice(0,500)}));
}
