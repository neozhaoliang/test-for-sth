/* Investor-facing figures must be rendered from actual report data,
   with consistent units, explicit periods, and no guessed observations. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=process.argv[2];
if(!path)throw new Error('Pass the extracted embedded dashboard JS');
const source=fs.readFileSync(path,'utf8');
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
    if(!nodes[id])nodes[id]={innerHTML:'',textContent:'',value:'',addEventListener(){}};
    return nodes[id];
  }
};
const ctx=vm.createContext({document,console,Date});
vm.runInContext(prefix,ctx,{timeout:8000});
assert.equal(typeof ctx.renderVerdictFigures,'function');
assert.equal(typeof ctx.collectVerdictFigures,'function');

const sparse={
  stock_code:'SH601088',stock_name:'中国神华',as_of:'2026-10-19',
  realtime_quote:{latest_price:51.5},
  summary:{stance:'neutral',thesis_summary:'公司盈利好，但估值要小心。',dimension_analyses:{}},
  valuation:{},valuation_model:{route:{primary:'owner_fcfe'},
    valuation:{status:'insufficient_evidence'}},
  fundamentals:{facts:{}},
  dividend_chart:[],
};
assert.equal(ctx.renderVerdictFigures(sparse),'','missing data must not create chart placeholders');

const report=JSON.parse(JSON.stringify(sparse));
report.fundamentals={
  concentration_period:'2025-12-31',
  facts:{
    finance_period:'2026-06-30',
    net_profit:28770000000,
    operating_cash_flow:54664000000,
    cash_to_profit_ratio:1.9,
    top5_customer_pct:14.6,
    overseas_revenue_pct:null,
  },
};
report.valuation={pb:2.477,valuation_as_of:'2026-10-15'};
report.valuation_history={
  as_of:'2026-10-15',
  current_pe_ttm:20.69,current_pb:2.477,
  pe_percentiles:[{years:5,percentile:99.9,observations:1097}],
  pb_percentiles:[{years:5,percentile:99.4,observations:1097}],
};
report.dividend_chart=[
  {year:'2023',dividend_per_10:25.5,buyback_per_10:0,total_per_10:25.5},
  {year:'2024',dividend_per_10:22.6,buyback_per_10:0,total_per_10:22.6},
  {year:'2025',dividend_per_10:20.1,buyback_per_10:0,total_per_10:20.1},
];
report.profitability_trend={
  periods:[
    {period:'2019-12-31',debt_ratio_pct:23.31},
    {period:'2026-06-30',debt_ratio_pct:40.27},
  ]
};
report.valuation_model.investor_reference={
  as_of:'2026-10-19',
  historical_pb_anchor:{
    window_years:5,median_pb:1.779,median_pb_reference_price:36.99
  }
};
report.summary.thesis_summary=
  '公司现金流和净利润都很好。分红稳定，但估值已经处于高位。' +
  '同时资产负债率上升，客户结构也值得看看。按现在价格谈投资回报。';
const charts=ctx.collectVerdictFigures(report);
assert.equal(charts.length,6,'six sourced types: cash, valuation, dividend, leverage, customers, PB reference');
const html=ctx.renderVerdictFigures(report);
assert.ok(html.includes('先看关键数字'));
assert.ok(html.includes('利润与经营现金流'));
assert.ok(html.includes('546.64亿元'));
assert.ok(html.includes('287.70亿元'));
assert.ok(html.includes('2026-06-30'));
assert.ok(html.includes('经营现金流／净利润：1.90倍'));
assert.ok(html.includes('估值处于历史什么位置'));
assert.ok(html.includes('99.9%'));
assert.ok(html.includes('99.4%'));
assert.ok(html.includes('20.69 倍'));
assert.ok(html.includes('按公告年度归集'));
assert.ok(html.includes('25.50'));
assert.ok(html.includes('20.10'));
assert.ok(html.includes('资产负债率前后比较'));
assert.ok(html.includes('23.31%'));
assert.ok(html.includes('40.27%'));
assert.ok(html.includes('前五大客户营收占比'));
assert.ok(html.includes('14.60%'));
assert.ok(!html.includes('海外收入占比'),'unknown overseas exposure must not be drawn');
assert.ok(html.includes('现价与历史PB参考价'));
assert.ok(html.includes('36.99元'));
assert.ok(html.includes('51.50元'));
assert.ok(html.includes('不是内在价值或推荐买入价'));
assert.equal((html.match(/class="verdict-figure"/g)||[]).length,6);

// A chart belongs beside the investment conclusion, not far below it.
ctx.renderResult(report);
let page=nodes.result.innerHTML;
const figures=page.indexOf('class="verdict-figures"');
const thesis=page.indexOf('公司现金流和净利润都很好');
const dashboard=page.indexOf('id="report-charts"');
assert.ok(figures>0&&thesis>figures&&dashboard>thesis);
assert.equal((page.match(/class="verdict-figures"/g)||[]).length,1);

// Do not invent PE percentiles or cash flow bars from a single available value.
const partial=JSON.parse(JSON.stringify(report));
delete partial.fundamentals.facts.operating_cash_flow;
partial.valuation_history.pe_percentiles=[];
partial.dividend_chart=[];
partial.profitability_trend.periods=[];
delete partial.valuation_model.investor_reference;
const smaller=ctx.renderVerdictFigures(partial);
assert.ok(!smaller.includes('利润与经营现金流'));
assert.ok(!smaller.includes('每10股现金分红'));
assert.ok(!smaller.includes('资产负债率前后比较'));
assert.ok(!smaller.includes('现价与历史PB参考价'));
assert.ok(smaller.includes('市净率 PB'),'a real PB percentile remains visible');
assert.ok(!smaller.includes('市盈率 PE'),'no fabricated PE percentile');

// Only emit price sensitivity bars when the Python model has supplied
// explicit historical EPS×PE conditional scenarios.
const scenarioReport=JSON.parse(JSON.stringify(report));
scenarioReport.valuation_model.investor_reference.earnings_power={
  status:'historical_sensitivity_only',
  market_price_yuan:51.5,
  pe_as_of:'2026-10-15',
  scenarios:[
    {name:'历史较弱',conditional_price_yuan:32.80},
    {name:'历史中枢',conditional_price_yuan:42.60},
    {name:'历史较强',conditional_price_yuan:50.14}
  ]
};
const scenarioHtml=ctx.renderVerdictFigures(scenarioReport);
assert.ok(scenarioHtml.includes('历史盈利 × PE 条件价格'));
assert.ok(scenarioHtml.includes('32.80元'));
assert.ok(scenarioHtml.includes('42.60元'));
assert.ok(scenarioHtml.includes('50.14元'));
assert.equal((scenarioHtml.match(/class="verdict-figure"/g)||[]).length,7);
scenarioReport.valuation_model.investor_reference.earnings_power.status='insufficient_history';
assert.ok(!ctx.renderVerdictFigures(scenarioReport).includes('历史盈利 × PE 条件价格'));

// Negative operating cash flow must not be hidden or shown as positive.
const cashNegative=JSON.parse(JSON.stringify(report));
cashNegative.fundamentals.facts.operating_cash_flow=-12000000000;
cashNegative.fundamentals.facts.cash_to_profit_ratio=-0.42;
const negativeHtml=ctx.renderVerdictFigures(cashNegative);
assert.ok(negativeHtml.includes('-120.00亿元'));
assert.ok(negativeHtml.includes('负值柱长按绝对金额画'));
assert.ok(negativeHtml.includes('#c65f54'));

// Escape untrusted dates/labels even though values are strict numeric.
const hostile=JSON.parse(JSON.stringify(report));
hostile.fundamentals.concentration_period='<svg onload=alert(1)>';
const safe=ctx.renderVerdictFigures(hostile);
assert.ok(!safe.includes('<svg onload=alert(1)>'));
assert.ok(safe.includes('&lt;svg'));

console.log('Verdict figures checks passed: source-only cash, valuation, dividends, debt, customers, reference prices, graceful partial data.');
