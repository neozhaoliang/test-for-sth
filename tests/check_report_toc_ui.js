/* Native in-page anchors must correspond to the sections truly rendered. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const file=process.argv[2];
if(!file)throw new Error('Pass the extracted embedded dashboard JS');
const source=fs.readFileSync(file,'utf8');
const prefix=source.split("document.getElementById('submitBtn').addEventListener")[0];
const nodes={};
const document={
  querySelectorAll(){return [];},
  createElement(){
    let text='';
    return {set textContent(v){text=String(v);},get innerHTML(){
      return text.replaceAll('&','&amp;').replaceAll('<','&lt;')
        .replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');
    }};
  },
  getElementById(id){
    if(!nodes[id])nodes[id]={
      innerHTML:'',textContent:'',value:'',listeners:{},
      addEventListener(type,fn){this.listeners[type]=fn;}
    };
    return nodes[id];
  },
};
const ctx=vm.createContext({document,console,Date});
vm.runInContext(prefix,ctx,{timeout:8000});
assert.equal(typeof ctx.renderReportToc,'function');

const fields=['management','fundamentals','rd','chip_flow','price_position',
  'cycle_position','policy_geopolitics','retail_sentiment','shareholder_returns',
  'growth_elasticity','a_share_structure','risk_quality'];
const base={
  stock_code:'SH600000',stock_name:'模拟公司',as_of:'2026-10-13',
  research_mode:'live',realtime_quote:{latest_price:12.3},
  summary:{stance:'neutral',thesis_summary:'以基本面判断价值。',
    dimension_scores:[],dimension_analyses:{}},
  valuation:{nav_per_share:5},valuation_model:{
    route:{primary:'owner_fcfe'},valuation:{status:'insufficient_evidence'},
    interactive_inputs:null
  },fundamentals:{facts:{}},dividend_chart:[],
};

function verify(html){
  assert.ok(html.indexOf('id="report-toc"') < html.indexOf('id="report-verdict"'));
  const hrefs=[...html.matchAll(/href="#(report-[A-Za-z0-9-]+|industryValuationLab|roe-discount-lab)"/g)].map(x=>x[1]);
  const ids=[...html.matchAll(/id="(report-[A-Za-z0-9-]+|industryValuationLab|roe-discount-lab)"/g)].map(x=>x[1]);
  assert.equal(ids.length,new Set(ids).size,'target IDs must be unique');
  for(const id of hrefs){
    assert.equal(ids.filter(x=>x===id).length,1,'dead TOC link '+id);
  }
  assert.equal(hrefs.length,new Set(hrefs).size,'duplicate TOC link');
  return hrefs;
}

ctx.renderResult(base);
let html=nodes.result.innerHTML, hrefs=verify(html);
assert.ok(html.includes('<nav class="report-toc"'));
assert.ok(html.includes('href="#report-verdict"'));
assert.ok(html.includes('href="#report-charts"'));
assert.ok(html.includes('href="#industryValuationLab"'));
assert.ok(!html.includes('href="#report-dimensions"'));
assert.ok(!html.includes('href="#report-capital"'));
assert.ok(!html.includes('href="#report-investor-reference"'));
assert.ok(!html.includes('href="#roe-discount-lab"'));
assert.ok(!html.includes('toc-subsections'));
assert.equal(hrefs.length,3);

// Deep-link into all twelve investment dimensions, and conditionally displayed modules.
const large=JSON.parse(JSON.stringify(base));
large.summary.dimension_analyses=Object.fromEntries(fields.map(k=>[k,'该维度详细分析']));
large.shareholder_capital_summary={
  corporate_buybacks:[{status:'implemented',shares:123,disclosed_at:'2026-10-01'}]
};
large.valuation_model.investor_reference={
  dividend_basis:{years:[2024,2025],observed_median_cash_dividend_per_share:.3}
};
ctx.renderRoeDiscountLab=()=>'<div id="roe-discount-lab">ROE calculator mock</div>';
ctx.activateRoeDiscountLab=()=>{};
ctx.renderResult(large);
html=nodes.result.innerHTML;
hrefs=verify(html);
for(const id of ['report-capital','report-dimensions','report-investor-reference','roe-discount-lab'])
  assert.ok(hrefs.includes(id),'missing main section '+id);
for(const key of fields)
  assert.ok(hrefs.includes('report-dimension-'+key),'missing detail link '+key);
assert.ok(html.includes('展开 12 项分析子目录'));
assert.ok(html.includes('href="#report-dimension-management"'));
assert.ok(html.includes('href="#report-dimension-risk_quality"'));

// Changing a report replaces the whole nav rather than preserving stale links.
delete ctx.renderRoeDiscountLab;
ctx.renderResult(base);
html=nodes.result.innerHTML;
verify(html);
assert.ok(!html.includes('href="#roe-discount-lab"'));
assert.ok(!html.includes('href="#report-capital"'));

console.log('Report TOC checks passed: dynamic chapters, 12 subsection links, unique anchors, missing section gates, report refresh.');
