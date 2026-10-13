const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync('web/roe_discount.js','utf8');
const nodes={};
const document={getElementById(id){return nodes[id]||null;}};
const ctx=vm.createContext({console,document,escapeHtml:(x)=>String(x)});
vm.runInContext(src,ctx,{timeout:5000});

const priced=ctx.roeDiscountCompute(10,20,5,10);
assert.ok(priced);
assert.equal(priced.steady,30);
assert.equal(priced.payoutPct,75);
assert.ok(priced.finite>10&&priced.finite<30);
assert.equal(ctx.roeDiscountCompute(10,15,10,10),null);
assert.equal(ctx.roeDiscountCompute(10,15,15,20),null);
assert.ok(Math.abs(ctx.roeDiscountCompute(25,10,2,10).finite-25)<1e-8);

const model={investor_reference:{roe_discount_lab:{
 status:'interactive_assumptions',book_per_share:10,
 roe_anchor:{status:'historical_observed',years:[2023,2024,2025],
   low_pct:12,median_pct:15,high_pct:20},
 defaults:{roe_pct:20,growth_pct:5,discount_pct:null},
 parameter_basis:{roe:'完整财年历史ROE',growth:'留存收益与盈利增长'},
 payout_evidence:{status:'verified',payout_pct:75},
}}};
const html=ctx.renderRoeDiscountLab(model);
for(const id of ['roe-roe','roe-growth','roe-discount','roe-price','roe-band','roe-reset'])
  assert.ok(html.includes(id),'missing control '+id);
assert.ok(html.includes('不是根据股价测算') || html.includes('不是根据股价测算')===false);
assert.ok(html.includes('10%只是演示输入'));
assert.ok(!html.includes('双木林叔'));
assert.ok(!html.includes('陈chensir'));

for(const id of ['roe-discount-lab','roe-roe','roe-growth','roe-discount',
                 'roe-price','roe-band','roe-steady','roe-payout','roe-terminal',
                 'roe-note','roe-roe-value','roe-growth-value',
                 'roe-discount-value','roe-reset']) {
 nodes[id]={value:'',textContent:'',listeners:{},
   addEventListener(type,handler){this.listeners[type]=handler;}};
}
nodes['roe-roe'].value='20';
nodes['roe-growth'].value='5';
nodes['roe-discount'].value='10';
ctx.activateRoeDiscountLab(model);
assert.ok(nodes['roe-price'].textContent.endsWith('元/股'));
assert.ok(nodes['roe-band'].textContent.includes('—'));
const before=nodes['roe-price'].textContent;
nodes['roe-discount'].value='15';
nodes['roe-discount'].listeners.input();
assert.notEqual(nodes['roe-price'].textContent,before);
nodes['roe-growth'].value='16';
nodes['roe-growth'].listeners.input();
assert.equal(nodes['roe-price'].textContent,'—');
nodes['roe-reset'].listeners.click();
assert.equal(nodes['roe-discount'].value,10);
assert.equal(nodes['roe-growth'].value,5);
assert.equal(nodes['roe-price'].textContent,before);

const noBook=ctx.renderRoeDiscountLab({investor_reference:{
 roe_discount_lab:{status:'missing_book',book_per_share:null}
}});
assert.ok(noBook.includes('不能生成价格'));
assert.ok(!noBook.includes('roe-growth'));
console.log('ROE discount lab: math, 3 sliders, live update, reset, guard passed.');
