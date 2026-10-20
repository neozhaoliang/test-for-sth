/* The visible analysis progress indicator is the button spinner, NOT
 * a standalone "状态: running" element. Exercise the async POST/poll path,
 * including failures, a slow backend, duplicate clicks and terminal cleanup.
 */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const filename=process.argv[2];
if(!filename)throw new Error('Pass the extracted embedded WebUI script');
const script=fs.readFileSync(filename,'utf8');
const prefix=script.split("document.getElementById('submitBtn').addEventListener")[0];
const documentSource=fs.readFileSync('web/app.py','utf8');
const css=fs.readFileSync('web/research_desk.css','utf8');
assert.ok(!documentSource.includes('<div id="status"'));
assert.ok(!documentSource.includes("getElementById('status')"));
assert.ok(documentSource.includes('<span class="submit-btn-spinner" aria-hidden="true">'));
assert.ok(documentSource.includes('id="analysisError"'));
assert.ok(css.includes('animation:analyze-button-spin'));
assert.ok(css.includes('#submitBtn.is-loading'));
assert.ok(css.includes('@media(prefers-reduced-motion:reduce)'));

const nodes={};
const btnClassSet=new Set();
function mockElement() {
  return {
    disabled:false,hidden:false,checked:false,value:'',
    textContent:'',innerHTML:'',listeners:{},attributes:{},
    classList:{
      add(x){btnClassSet.add(x)},
      remove(x){btnClassSet.delete(x)},
      contains(x){return btnClassSet.has(x)},
      toggle(x,yes){
        if(yes)btnClassSet.add(x);else btnClassSet.delete(x);
      },
    },
    setAttribute(name,value){this.attributes[name]=String(value)},
    getAttribute(name){return this.attributes[name]},
    querySelector(selector){
      if(selector==='.submit-btn-text')return nodes.btnLabel;
      return null;
    },
    addEventListener(type,fn){this.listeners[type]=fn},
  };
}
const button=mockElement();
nodes.submitBtn=button;
nodes.btnLabel=mockElement();
nodes.btnLabel.textContent='分析';
nodes.stockCode=mockElement();
nodes.stockCode.value='SZ002594';
nodes.researchMode=mockElement();
nodes.researchMode.value='live';
nodes.asOfDate=mockElement();
nodes.saveSnapshot=mockElement();
nodes.result=mockElement();
nodes.analysisError=mockElement();
nodes.analysisError.hidden=true;

let currentTimerId=0;
const timers=new Map();
const queued=[];
const fetchCalls=[];
function fakeFetch(url,options){
  fetchCalls.push({url,options});
  if(!queued.length)throw Error('Unexpected fetch: '+url);
  const value=queued.shift();
  if(value instanceof Error)return Promise.reject(value);
  return Promise.resolve({
    ok:value.ok!==false,
    status:value.status||200,
    json:async()=>value.body,
  });
}
const doc={
  getElementById(id){
    if(!nodes[id])nodes[id]=mockElement();
    return nodes[id];
  },
  querySelectorAll(){return []},
  createElement(){
    let text='';
    return {set textContent(v){text=String(v)},
      get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;')
        .replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;')}};
  },
};
const ctx=vm.createContext({
  document:doc,
  console,
  Date,
  fetch:fakeFetch,
  setTimeout(fn,ms){
    assert.equal(ms,2000);
    const id=++currentTimerId;
    timers.set(id,fn);
    return id;
  },
  clearTimeout(id){timers.delete(id)},
  publishedReports:[],
});
vm.runInContext(prefix,ctx,{timeout:8000});
// Rendering a 100KB stock report has separate tests; use a spy here.
vm.runInContext('renderResult = report => publishedReports.push(report)',ctx);
const submit=vm.runInContext('submitAnalysis',ctx);

const reply=(body,ok=true,status=200)=>queued.push({body,ok,status});
async function fireTimer() {
  assert.ok(timers.size>0,'an active job should have a polling timer');
  const [id,fn]=timers.entries().next().value;
  timers.delete(id);
  fn();
  // The timer invokes an async function without returning its promise.
  await new Promise(resolve=>setImmediate(resolve));
}
function busy(expected) {
  assert.equal(button.disabled,expected);
  assert.equal(button.classList.contains('is-loading'),expected);
  assert.equal(button.getAttribute('aria-busy'),String(expected));
  assert.equal(nodes.btnLabel.textContent,expected?'分析中':'分析');
}
function errorIs(message){
  assert.equal(nodes.analysisError.hidden,!message);
  if(message)assert.ok(nodes.analysisError.textContent.includes(message));
}

(async()=>{
  nodes.researchMode.value='historical';
  await submit();
  busy(false);
  errorIs('历史模式必须选择截止日期');
  assert.equal(fetchCalls.length,0);

  nodes.researchMode.value='live';
  reply({task_id:'job1'});
  const running=submit();
  busy(true);  // spinner begins before waiting for POST
  await running;
  errorIs('');
  assert.equal(fetchCalls.length,1);
  assert.equal(fetchCalls[0].url,'/api/analyze');
  assert.equal(JSON.parse(fetchCalls[0].options.body).stock_code,'SZ002594');

  // A second click cannot launch an orphan API job.
  await submit();
  assert.equal(fetchCalls.length,1);
  busy(true);

  reply({status:'pending'});
  await fireTimer();
  busy(true);
  reply({status:'running'});
  await fireTimer();
  busy(true);
  errorIs('');
  assert.equal(timers.size,1);

  const report={stock_code:'SZ002594',summary:{stance:'neutral'}};
  reply({status:'done',result:report});
  await fireTimer();
  busy(false);
  assert.equal(timers.size,0);
  assert.equal(ctx.publishedReports.length,1);
  assert.equal(ctx.publishedReports[0].stock_code,'SZ002594');
  errorIs('');

  reply({task_id:'job2'});
  await submit();
  busy(true);
  reply({status:'failed',error:'数据源超时'});
  await fireTimer();
  busy(false);
  errorIs('分析失败：数据源超时');

  reply({detail:'股票代码无效'},false,400);
  await submit();
  busy(false);
  errorIs('提交失败：股票代码无效');
  assert.equal(timers.size,0);

  queued.push(new Error('Network disconnected'));
  await submit();
  busy(false);
  errorIs('Network disconnected');

  reply({task_id:'job3'});
  await submit();
  busy(true);
  for(let attempt=0;attempt<9;attempt++){
    queued.push(new Error('Temporary outage'));
    await fireTimer();
    busy(true);
  }
  queued.push(new Error('Still unavailable'));
  await fireTimer();
  busy(false);
  errorIs('无法获取分析进度');
  assert.equal(timers.size,0);

  reply({task_id:'job4'});
  await submit();
  busy(true);
  reply({status:'done',result:null});
  await fireTimer();
  busy(false);
  errorIs('没有返回报告');

  nodes.stockCode.value='';
  await submit();
  busy(false);
  errorIs('请输入股票名称或代码');
  console.log('Analyze button spinner checks passed: no status row, immediate loading, pending/running, done, failure, retry and duplicate click.');
})().catch(err=>{console.error(err);process.exitCode=1});
