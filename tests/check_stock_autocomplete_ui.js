/* Standalone autocomplete widget smoke + keyboard + stale response protection. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

class Node {
  constructor(tag='div') {
    this.tag=tag;this.children=[];this.listeners={};this.attributes={};
    this.hidden=true;this.value='';this.textContent='';this.id='';this.type='';
    this.className='';this.active=false;
    this.classList={toggle:(name,on)=>{this.active=on;}};
  }
  addEventListener(type,fn){this.listeners[type]=fn;}
  setAttribute(k,v){this.attributes[k]=String(v);}
  removeAttribute(k){delete this.attributes[k];}
  getAttribute(k){return this.attributes[k];}
  replaceChildren(...children){this.children=[];this.append(...children);}
  appendChild(child){this.append(child);return child;}
  append(...children){
    for(const child of children) {
      if(child.tag==='fragment')this.children.push(...child.children);
      else this.children.push(child);
    }
  }
  querySelectorAll(query){
    return query==='[role="option"]'
      ? this.children.filter(c=>c.attributes.role==='option'):[];
  }
  scrollIntoView(){}
  contains(child){return child===this||this.children.includes(child);}
  focus(){doc.activeElement=this;}
  fire(type,event={}){this.listeners[type]?.(event);}
}
const input=new Node('input'),panel=new Node('div'),button=new Node('button');
const doc={
  activeElement:input,
  getElementById(id){return ({stockCode:input,stockSuggestions:panel,submitBtn:button})[id]||null;},
  createElement(name){return new Node(name);},
  createDocumentFragment(){return new Node('fragment');},
  listeners:{},
  addEventListener(type,fn){this.listeners[type]=fn;},
};
let timerId=0;const timers=new Map();
const clock={
  setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,delay});return id;},
  clearTimeout(id){timers.delete(id);},
  flush(){const saved=[...timers.entries()];timers.clear();for(const [,v] of saved)v.fn();},
};
let called=[],submitted=0;
let responses=[];
const fetch=async(url,{signal}={})=>{
  called.push(url);
  return {ok:true,json:async()=>({items:responses})};
};
const code=fs.readFileSync('web/stock_autocomplete.js','utf8');
const context=vm.createContext({
  document:doc,fetch,AbortController,setTimeout:clock.setTimeout.bind(clock),
  clearTimeout:clock.clearTimeout.bind(clock),
  console,submitAnalysis:()=>{submitted+=1;},
});
vm.runInContext(code,context,{timeout:5000});
const settle=async()=>{
  for(let i=0;i<8;i++)await Promise.resolve();
};

(async()=>{
  // Rapid changes collapse into a single lookup.
  input.value='中';input.fire('input');
  input.value='海油';input.fire('input');
  responses=[{name:'中国海油',code:'SH600938'},
             {name:'中国石油',code:'SH601857'}];
  clock.flush();await settle();
  assert.equal(called.length,1);
  assert.ok(called[0].includes(encodeURIComponent('海油')));
  assert.equal(panel.hidden,false);
  assert.equal(input.getAttribute('aria-expanded'),'true');
  let choices=panel.querySelectorAll('[role="option"]');
  assert.equal(choices.length,2);
  assert.equal(choices[0].children[0].textContent,'中国海油');
  assert.equal(choices[0].children[1].textContent,'SH600938');

  const prevent={preventDefault(){this.prevented=true;}};
  input.fire('keydown',{key:'ArrowDown',...prevent});
  assert.equal(input.getAttribute('aria-activedescendant'),'stock-suggest-0');
  input.fire('keydown',{key:'ArrowDown',preventDefault(){}});
  assert.equal(input.getAttribute('aria-activedescendant'),'stock-suggest-1');
  input.fire('keydown',{key:'Enter',preventDefault(){}});
  assert.equal(input.value,'SH601857');
  assert.equal(panel.hidden,true);
  assert.equal(input.getAttribute('aria-expanded'),'false');
  input.fire('keydown',{key:'Enter',preventDefault(){}});
  assert.equal(submitted,1);

  // No stale HTML injection: the stock name is always assigned as text.
  input.value='中国';input.fire('input');
  responses=[{code:'SH600938',name:'<script>alert(1)</script>'}];
  clock.flush();await settle();
  choices=panel.querySelectorAll('[role="option"]');
  assert.equal(choices[0].children[0].textContent,'<script>alert(1)</script>');
  input.fire('keydown',{key:'Escape',preventDefault(){}});
  assert.equal(panel.hidden,true);

  // Composition should not initiate searches until IME is finished.
  const old=called.length;
  input.fire('compositionstart');
  input.value='海';input.fire('input');
  clock.flush();await settle();
  assert.equal(called.length,old);
  input.fire('compositionend');
  responses=[];
  clock.flush();await settle();
  assert.equal(called.length,old+1);
  assert.equal(panel.hidden,false);
  assert.ok(panel.children[0].textContent.includes('没有匹配项'));

  // Failing provider must not prevent submitting a typed six-digit code.
  input.fire('keydown',{key:'Escape',preventDefault(){}});
  input.value='600938';
  input.fire('keydown',{key:'Enter',preventDefault(){}});
  assert.equal(submitted,2);
  assert.equal(panel.hidden,true);
  console.log('Stock autocomplete UI checks passed: debounce, keyboard, IME, escape, security, fallback.');
})().catch(e=>{console.error(e);process.exitCode=1;});
