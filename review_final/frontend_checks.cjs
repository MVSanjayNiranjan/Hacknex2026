// Deterministic JavaScript/DOM simulations, explicitly NOT browser tests.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes=new Map(),listeners=new Map(),requests=[];
function node(id){if(!nodes.has(id))nodes.set(id,{textContent:'',style:{},children:[],classList:{values:new Set(),add(x){this.values.add(x)},remove(x){this.values.delete(x)},toggle(x,on){if(on)this.add(x);else this.remove(x)}},appendChild(x){this.children.push(x)},addEventListener(){},naturalWidth:1000,naturalHeight:1000,clientWidth:500,clientHeight:500,offsetLeft:12,offsetTop:0,complete:true});return nodes.get(id)}
const ctx={document:{querySelector:node,createElement:node,addEventListener(){}},window:{addEventListener(k,f){listeners.set(k,f)},removeEventListener(k){listeners.delete(k)}},setTimeout(){},setInterval(){},clearInterval(){},fetch(path){return new Promise(resolve=>requests.push({path,resolve}))},FormData:class{append(){}},console};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('static/js/app.js','utf8'),ctx);
const results=[];
async function test(name,fn){try{await fn();results.push({name,pass:true})}catch(e){results.push({name,pass:false,error:e.message})}}
const cite=(doc,id='e')=>({document_id:doc,page_number:1,document_name:doc,section:'section',evidence_id:id});
const respond=(i,detail)=>requests[i].resolve({ok:true,json:async()=>detail});
const detail=(text,box=[100,100,200,200])=>({page:{image_width:1000},elements:[{id:'e',text,bbox_px:box}]});
(async()=>{
 await test('cached image paints highlight',async()=>{const p=ctx.openSource(cite('A'));respond(0,detail('A'));await p;assert.equal(node('#viewer-highlight').style.left,'62px');assert(!node('#viewer-highlight').classList.values.has('hidden'))});
 await test('rapid citations discard stale detail',async()=>{const a=ctx.openSource(cite('A'));const b=ctx.openSource(cite('B'));respond(2,detail('B'));await b;respond(1,detail('stale A'));await a;assert.equal(node('#viewer-text').textContent,'B')});
 await test('no-coordinate citation clears highlight',async()=>{const p=ctx.openSource(cite('C','visual'));respond(3,detail('visual',null));await p;assert(node('#viewer-highlight').classList.values.has('hidden'));assert(!listeners.has('resize'))});
 await test('resize repaints cached image',async()=>{const p=ctx.openSource(cite('D'));respond(4,detail('D'));await p;node('#viewer-image').clientWidth=250;listeners.get('resize')();assert.equal(node('#viewer-highlight').style.width,'25px')});
 await test('HTML-like text uses literal textContent',async()=>{const n=vm.runInContext("el('review-node',null,'<img src=x onerror=alert(1)>')",ctx);assert.equal(n.textContent,'<img src=x onerror=alert(1)>');assert(!('innerHTML' in n))});
 await test('API handles non-JSON HTTP errors',async()=>{const p=ctx.api('/bad');requests[5].resolve({ok:false,status:413,json:async()=>{throw new Error('HTML body')}});await assert.rejects(p,/413/)});
 await test('rapid notebook switching discards stale sources',async()=>{
   vm.runInContext('renderSources=()=>{};updateCounts=()=>{};state.notebookId="old";',ctx);const old=ctx.loadDocuments();vm.runInContext('state.notebookId="new";',ctx);const newer=ctx.loadDocuments();respond(7,[{id:'new-doc'}]);await newer;respond(6,[{id:'old-doc'}]);await old;assert.equal(vm.runInContext('state.documents[0].id',ctx),'new-doc');
 });
 console.log(JSON.stringify({kind:'JavaScript/DOM simulation; not browser testing',passed:results.filter(x=>x.pass).length,failed:results.filter(x=>!x.pass).length,skipped:0,results},null,2));process.exitCode=results.some(x=>!x.pass)?1:0;
})();
