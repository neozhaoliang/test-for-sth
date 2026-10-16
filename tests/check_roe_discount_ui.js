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
assert.ok(Math.abs(priced.steady-30)<1e-9);
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
assert.ok(html.includes('class="lab-layout roe-discount-layout"'));
assert.ok(html.includes('class="roe-discount-controls"'));
assert.ok(html.includes('class="lab-result roe-discount-result"'));
assert.ok(html.includes('aria-live="polite"'));
assert.ok(html.indexOf('roe-discount-controls')<html.indexOf('roe-discount-result'));
// Each label, slider and output must share the same row and use the proper
// label/for relationship; not a separate heading above a full-width slider.
for(const [key,label] of [
  ['roe','正常化ROE'], ['growth','长期权益增长率'],
  ['discount','投资者要求收益率／贴现率'],
]){
  const row=html.match(new RegExp('<div class="slider-row roe-discount-slider-row">'+
    '<label for="roe-'+key+'">([^<]+)</label>'+
    '<input type="range" id="roe-'+key+'"[^>]*>'+
    '<output for="roe-'+key+'" id="roe-'+key+'-value">([^<]+)</output></div>'));
  assert.ok(row,'expected an inline labeled slider for '+key);
  assert.equal(row[1],label);
  assert.ok(row[2].endsWith('%'));
}
assert.equal((html.match(/class="slider-row roe-discount-slider-row"/g)||[]).length,3);
assert.ok(!html.includes('class="slider-top"'));
for (const removed of ['ROE依据：','增长依据：','贴现率依据：',
                      '10%只是演示输入','增长和分红受约束：',
                      'g=ROE×留存率','隐含派息率=1−g/ROE'])
  assert.ok(!html.includes(removed),'obsolete explanatory paragraph: '+removed);
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
const oldSteady=nodes['roe-steady'].textContent;
const oldBand=nodes['roe-band'].textContent;
nodes['roe-discount'].value='15';
nodes['roe-discount'].listeners.input();
assert.notEqual(nodes['roe-price'].textContent,before);
assert.notEqual(nodes['roe-steady'].textContent,oldSteady);
assert.notEqual(nodes['roe-band'].textContent,oldBand);
assert.ok(nodes['roe-payout'].textContent.endsWith('%'));
assert.ok(nodes['roe-terminal'].textContent.endsWith('%'));
assert.equal(nodes['roe-discount-value'].textContent,'15.00%');
nodes['roe-roe'].value='18.5';
nodes['roe-roe'].listeners.input();
assert.equal(nodes['roe-roe-value'].textContent,'18.50%');
assert.ok(nodes['roe-price'].textContent.endsWith('元/股'));
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
const css=fs.readFileSync('web/research_desk.css','utf8');
const rowRule=css.match(/#result\s+#roe-discount-lab\s+\.roe-discount-layout\s*\{([^}]+)\}/);
assert.ok(rowRule,'must have ROE-only layout rule overriding global single-column layout');
assert.ok(/display\s*:\s*grid/.test(rowRule[1]));
assert.ok(/grid-template-columns\s*:\s*minmax\(0,\s*\.95fr\)\s+minmax\(0,\s*1\.05fr\)/.test(rowRule[1]));
assert.ok(css.includes('#result #roe-discount-lab .roe-discount-controls'));
const sliderRule=css.match(/#result\s+#roe-discount-lab\s+\.roe-discount-controls\s+\.roe-discount-slider-row\s*\{([^}]+)\}/);
assert.ok(sliderRule,'inline ROE slider grid missing');
assert.ok(/display\s*:\s*grid/.test(sliderRule[1]));
assert.ok(/grid-template-columns\s*:\s*max-content\s+minmax\(42px,1fr\)\s+max-content/.test(sliderRule[1]));
assert.ok(/align-items\s*:\s*center/.test(sliderRule[1]));
assert.ok(css.includes('#result #roe-discount-lab .roe-discount-slider-row > input[type="range"]'));
assert.ok(css.includes('#result #roe-discount-lab .roe-discount-slider-row > output'));
assert.ok(!css.includes('#result #roe-discount-lab .roe-discount-controls .slider-top'));
assert.ok(css.includes('#result #roe-discount-lab .roe-discount-result'));
assert.ok(/@media\s*\(max-width:760px\)/.test(css));
assert.ok(/@media\s*\(max-width:420px\)/.test(css));

console.log('ROE discount lab: math, 3 sliders, live update, reset, guard passed.');
