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
  const hrefs=[...html.matchAll(/href="#(report-[A-Za-z0-9_-]+|industryValuationLab|roe-discount-lab)"/g)].map(x=>x[1]);
  const ids=[...html.matchAll(/id="(report-[A-Za-z0-9_-]+|industryValuationLab|roe-discount-lab)"/g)].map(x=>x[1]);
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
const toc=html.split('<nav class="report-toc"',2)[1].split('</nav>',1)[0];
assert.equal((toc.match(/研究目录/g)||[]).length,2); // aria-label and one visible h3
assert.ok(toc.includes('<h3>研究目录</h3>'));
for(const obsolete of ['CONTENTS','研究导览','报告目录','个章节','toc-eyebrow','toc-count'])
  assert.ok(!toc.includes(obsolete),'old TOC label still displayed: '+obsolete);
assert.ok(html.includes('<section class="report-header-inline report-company-card"'));
assert.ok(html.includes('<h2 id="report-top">模拟公司</h2>'));
assert.ok(html.includes('<span class="report-stock-code">SH600000</span>'));
assert.ok(!html.includes('模拟公司 (SH600000)'));
assert.ok(html.includes('Live 研究'));
assert.ok(html.includes('基准日 2026-10-13'));
assert.ok(html.includes('最新价</span> <strong>12.3元</strong>'));
assert.ok(!html.includes('<p class="quote">'));
assert.ok(!html.includes('<div class="live-banner">'));
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
// A long multi-paragraph judgment must not disappear when all 12 dimensions
// are present; 400 characters was an accidental front-end hiding threshold.
const longThesis = '公司的盈利能力在高景气周期中表现优秀，但经营成绩不等于现价必然有安全边际。'.repeat(18)
  + '\n\n' + '需要进一步解释金铜美元定价、人民币折算、资本开支、股东人数变化与估值情景的关系。'
  + '\n\n' + '因此我目前对买入赔率保持谨慎，这与认可公司的长期竞争力并不矛盾。';
assert.ok(longThesis.length>400);
large.summary.thesis_summary=longThesis;
large.shareholder_capital_summary={
  corporate_buybacks:[{status:'implemented',shares:123,disclosed_at:'2026-10-01'}]
};
large.chip_price_context={
  price_holder_series:[
    {period:'2025-06-30',holders:335659,price:19.50},
    {period:'2026-02-28',holders:1108965,price:39.55},
    {period:'2026-06-30',holders:1318365,price:25.14}
  ]
};
large.valuation_model.investor_reference={
  dividend_basis:{years:[2024,2025],observed_median_cash_dividend_per_share:.3}
};
ctx.renderRoeDiscountLab=()=>'<div id="roe-discount-lab">ROE calculator mock</div>';
ctx.activateRoeDiscountLab=()=>{};
ctx.renderResult(large);
html=nodes.result.innerHTML;
hrefs=verify(html);
for(const paragraph of longThesis.split(/\n\s*\n/))
  assert.ok(html.includes(paragraph), 'every long-form thesis paragraph must remain visible');
assert.ok(!html.includes('\\n\\n'), 'paragraph breaks should not be rendered as literal slash-n');
const chipParagraphEnd=html.indexOf('股东人数变化与估值情景的关系。');
const inlineChipChart=html.indexOf('class="analysis-inline-evidence chip-analysis-evidence thesis-chip-evidence"');
const followingParagraph=html.indexOf('因此我目前对买入赔率保持谨慎');
assert.ok(chipParagraphEnd>=0&&chipParagraphEnd<inlineChipChart&&inlineChipChart<followingParagraph,
  'holder chart must appear immediately after the thesis paragraph discussing shareholder counts');
assert.equal((html.match(/class="analysis-inline-evidence chip-analysis-evidence/g)||[]).length,1,
  'holder chart must not be duplicated in the later chip-flow dimension');
assert.ok(html.includes('aria-label="股东人数（万户，左轴）与同期股价（元，右轴）实际数值走势图"'),
  'inline chart must use actual aligned holder/price data');
for(const id of ['report-dimensions','report-investor-reference','roe-discount-lab'])
  assert.ok(hrefs.includes(id),'missing main section '+id);
assert.ok(html.includes('id="report-capital"'), 'capital disclosures stay available');
assert.ok(!hrefs.includes('report-capital'), 'capital evidence is no longer a separate TOC chapter');
const returnStart=html.indexOf('id="report-dimension-shareholder_returns"');
const returnEnd=html.indexOf('id="report-dimension-growth_elasticity"');
const capitalPos=html.indexOf('id="report-capital"');
assert.ok(returnStart<capitalPos&&capitalPos<returnEnd,
  'capital records must appear directly under shareholder return analysis');
for(const key of fields)
  assert.ok(hrefs.includes('report-dimension-'+key),'missing detail link '+key);
assert.ok(html.includes('展开 12 项分析子目录'));
assert.ok(html.includes('href="#report-dimension-management"'));
assert.ok(html.includes('href="#report-dimension-risk_quality"'));

// When the main thesis has no holder-count paragraph, keep the chart directly
// below the dedicated chip-flow analysis instead of losing it or duplicating it.
const thesisWithoutChip=JSON.parse(JSON.stringify(large));
thesisWithoutChip.summary.thesis_summary='公司基本面稳健，当前估值仍需进一步观察。';
ctx.renderResult(thesisWithoutChip);
const fallbackChipHtml=nodes.result.innerHTML;
const chipDimensionStart=fallbackChipHtml.indexOf('id="report-dimension-chip_flow"');
const fallbackChipChart=fallbackChipHtml.indexOf('class="analysis-inline-evidence chip-analysis-evidence"');
assert.ok(chipDimensionStart>=0&&chipDimensionStart<fallbackChipChart,
  'without a thesis chip paragraph, chart must remain under the chip-flow analysis');
assert.equal((fallbackChipHtml.match(/class="analysis-inline-evidence chip-analysis-evidence/g)||[]).length,1,
  'fallback chart must also render exactly once');

// Changing a report replaces the whole nav rather than preserving stale links.
delete ctx.renderRoeDiscountLab;
ctx.renderResult(base);
html=nodes.result.innerHTML;
verify(html);
assert.ok(!html.includes('href="#roe-discount-lab"'));
assert.ok(!html.includes('href="#report-capital"'));

console.log('Report TOC checks passed: dynamic chapters, 12 subsection links, unique anchors, missing section gates, report refresh.');


// Historical pages keep the as-of restriction without becoming three large
// stacked heading/banner/quote blocks.
const past={...base,research_mode:'historical',as_of:'2025-06-30',
  realtime_quote:{latest_price:17.5,change_pct:-1.8,volume:167000}};
ctx.renderResult(past);
const pastHtml=nodes.result.innerHTML;
assert.ok(pastHtml.includes('<section class="report-header-inline report-company-card"'));
assert.ok(pastHtml.includes('<h2 id="report-top">模拟公司</h2>'));
assert.ok(pastHtml.includes('<span class="report-stock-code">SH600000</span>'));
assert.ok(!pastHtml.includes('模拟公司 (SH600000)'));
assert.ok(pastHtml.includes('历史研究'));
assert.ok(pastHtml.includes('截止日 2025-06-30'));
assert.ok(pastHtml.includes('截止日收盘价</span> <strong>17.5元</strong>'));
assert.ok(pastHtml.includes('涨跌幅</span> <strong>-1.8%</strong>'));
assert.ok(pastHtml.includes('成交量</span> <strong>167000</strong>'));
assert.ok(pastHtml.includes('report-historical-note'));
assert.ok(!pastHtml.includes('<div class="historical-banner">'));

assert.ok(pastHtml.includes('report-change-down'));
assert.ok(!pastHtml.includes('report-change-up'));
assert.ok(pastHtml.indexOf('<div class="report-company-identity">') <
  pastHtml.indexOf('class="report-header-detail"'));
assert.equal((pastHtml.match(/class="report-company-identity"/g)||[]).length,1);

// Screenshot regression: large 比亚迪 on the left; SZ002594 is quiet,
// unparenthesized ticker text; mode/date/quote on the right in one card.
const byd={
  ...base,
  stock_code:'SZ002594',
  stock_name:'比亚迪',
  as_of:'2026-10-20',
  realtime_quote:{
    latest_price:82.19,change_pct:-1.569,volume:14988029,
    quote_time:'2026-10-20'
  }
};
ctx.renderResult(byd);
const bydHtml=nodes.result.innerHTML;
assert.ok(bydHtml.includes('<h2 id="report-top">比亚迪</h2>'));
assert.ok(bydHtml.includes('<span class="report-stock-code">SZ002594</span>'));
assert.ok(!bydHtml.includes('比亚迪 (SZ002594)'));
assert.ok(!bydHtml.includes('(SZ002594)'));
assert.equal((bydHtml.match(/class="report-company-card"/g)||[]).length,0,
  'card contains two classes, not a separate wrapper');
assert.equal((bydHtml.match(/class="report-header-inline report-company-card"/g)||[]).length,1);
assert.ok(bydHtml.includes('Live 研究'));
assert.ok(bydHtml.includes('基准日 2026-10-20'));
assert.ok(bydHtml.includes('最新价</span> <strong>82.19元</strong>'));
assert.ok(bydHtml.includes('class="report-quote-item report-quote-change report-change-down"'));
assert.ok(bydHtml.includes('涨跌幅</span> <strong>-1.569%</strong>'));
assert.ok(bydHtml.includes('成交量</span> <strong>14988029</strong>'));
assert.ok(bydHtml.indexOf('<div class="report-company-identity">') <
  bydHtml.indexOf('<div class="report-header-detail">'));
assert.ok(bydHtml.indexOf('<div class="report-header-detail">') <
  bydHtml.indexOf('id="report-toc"'));

// Old stock providers and cached reports may already append ticker in
// parentheses. The new identity card must strip it once, not display both.
for(const named of ['比亚迪 (SZ002594)','比亚迪（SZ002594）','比亚迪（002594）']){
  ctx.renderResult({...byd,stock_name:named});
  const card=nodes.result.innerHTML;
  assert.ok(card.includes('<h2 id="report-top">比亚迪</h2>'));
  assert.equal((card.match(/class="report-stock-code"/g)||[]).length,1);
  assert.ok(!card.includes('<h2 id="report-top">比亚迪 ('));
  assert.ok(!card.includes('<h2 id="report-top">比亚迪（'));
}
ctx.renderResult({...byd,stock_name:'比亚迪（A股）'});
assert.ok(nodes.result.innerHTML.includes('<h2 id="report-top">比亚迪（A股）</h2>'),
  'meaningful parenthetical text unrelated to ticker must not be removed');

// Ticker alone, missing price, and positive share price movement must also
// retain an accessible, never-parenthesized header.
const noName={...byd,stock_name:'',realtime_quote:{latest_price:0}};
ctx.renderResult(noName);
const noNameHtml=nodes.result.innerHTML;
assert.ok(noNameHtml.includes('<h2 id="report-top">SZ002594</h2>'));
assert.ok(!noNameHtml.includes('class="report-stock-code"'));
assert.ok(noNameHtml.includes('有效股价暂缺，不能判断当前股票赔率'));
const gain={...byd,realtime_quote:{latest_price:90.1,change_pct:2.36}};
ctx.renderResult(gain);
assert.ok(nodes.result.innerHTML.includes('class="report-quote-item report-quote-change report-change-up"'));

// The style file keeps the header as one card and reflows rather than scrolling
// the entire header horizontally on smaller displays.
const headerCss=fs.readFileSync('web/research_desk.css','utf8');
assert.ok(headerCss.includes('report-header-inline.report-company-card'));
assert.ok(headerCss.includes('.report-stock-code'));
assert.ok(headerCss.includes('.report-change-down strong'));
assert.ok(headerCss.includes('@media(max-width:600px)'));

