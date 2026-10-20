/* Figures follow the paragraph they explain. No figure gallery ahead of prose,
   and no invented observations or unsourced valuation scenarios. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const filename=process.argv[2];
if(!filename)throw new Error('Pass the extracted embedded dashboard JS');
const source=fs.readFileSync(filename,'utf8');
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
      innerHTML:'',textContent:'',value:'',addEventListener(){}
    };
    return nodes[id];
  },
};
const ctx=vm.createContext({document,console,Date});
vm.runInContext(prefix,ctx,{timeout:8000});
assert.equal(typeof ctx.collectVerdictFigures,'function');
assert.equal(typeof ctx.assignVerdictFiguresToParagraphs,'function');
assert.equal(typeof ctx.renderThesisWithInlineChipEvidence,'function');
const inline=report=>ctx.renderThesisWithInlineChipEvidence(
  report.summary.thesis_summary,report).html;
const chartCount=html=>(html.match(/class="verdict-figure(?:\s[^"]+)?"/g)||[]).length;

const sparse={
  stock_code:'SH601088',stock_name:'中国神华',as_of:'2026-10-19',
  research_mode:'live',
  realtime_quote:{latest_price:51.5},
  summary:{stance:'neutral',thesis_summary:'公司盈利好，但估值要小心。',
    dimension_scores:[],dimension_analyses:{}},
  valuation:{},valuation_model:{route:{primary:'owner_fcfe'},
    valuation:{status:'insufficient_evidence'}},
  fundamentals:{facts:{}},dividend_chart:[],
};
assert.equal(ctx.collectVerdictFigures(sparse).length,0);
assert.equal(chartCount(inline(sparse)),0);

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
  ],
};
report.valuation_model.investor_reference={
  as_of:'2026-10-19',
  historical_pb_anchor:{
    window_years:5,median_pb:1.779,median_pb_reference_price:36.99,
  },
};
const paragraphs=[
  '经营现金流和净利润都很好。前五大客户的营收占比也不算高。',
  '现在市盈率与市净率的历史估值分位已经很高。',
  '现金分红连年增加，这能给股东带来实实在在的现金。',
  '但公司收购资产以后，资产负债率也在上升。',
  '历史PB中位参考价要和当前股价放在一起对照。',
  '所以我不会只看当期利润就买，仍要考虑后面的煤价。',
];
report.summary.thesis_summary=paragraphs.join('\n\n');
const slots=ctx.assignVerdictFiguresToParagraphs(paragraphs,report);
assert.equal(JSON.stringify(slots.map(slot=>slot.map(fig=>fig.id))),
  JSON.stringify([['cash','customers'],['valuation'],['dividends'],['debt'],['price_refs'],[]]),
  'a real fact chart should be assigned only to the paragraph discussing that fact');
const html=inline(report);
assert.ok(html.startsWith('<p class="verdict-summary">'),'narrative must come first');
assert.ok(!html.includes('先看关键数字'),'no upfront chart gallery');
assert.equal(chartCount(html),6);
assert.ok(html.includes('546.64亿元'));
assert.ok(html.includes('287.70亿元'));
assert.ok(html.includes('2026-06-30'));
assert.ok(html.includes('经营现金流／净利润：1.90倍'));
assert.ok(html.includes('99.9%'));
assert.ok(html.includes('99.4%'));
assert.ok(html.includes('20.69 倍'));
assert.ok(html.includes('按公告年度归集'));
assert.ok(html.includes('25.50'));
assert.ok(html.includes('20.10'));
assert.ok(html.includes('23.31%'));
assert.ok(html.includes('40.27%'));
assert.ok(html.includes('14.60%'));
assert.ok(!html.includes('海外收入占比'),'unknown exposure must not be drawn');
assert.ok(html.includes('36.99元'));
assert.ok(html.includes('51.50元'));
assert.ok(html.includes('不是内在价值或推荐买入价'));
for(const [i,expectedTitle] of [
  [0,'利润与经营现金流'],
  [1,'估值处于历史什么位置'],
  [2,'每10股现金分红（元）'],
  [3,'资产负债率前后比较'],
  [4,'现价与历史PB参考价'],
]){
  const paragraph=html.indexOf(paragraphs[i]);
  const chart=html.indexOf(expectedTitle);
  const next=html.indexOf(paragraphs[i+1]);
  assert.ok(paragraph>=0&&paragraph<chart&&chart<next,
    expectedTitle+' must follow its paragraph and precede the next paragraph');
}
assert.equal((html.match(/上段文字对应的财务与估值图表/g)||[]).length,5);

// Render the actual report page: no separate gallery before all the text.
ctx.renderResult(report);
const page=nodes.result.innerHTML;
assert.ok(page.indexOf(paragraphs[0])<page.indexOf('利润与经营现金流'));
assert.ok(page.indexOf('利润与经营现金流')<page.indexOf(paragraphs[1]));
assert.ok(page.indexOf(paragraphs[4])<page.indexOf('现价与历史PB参考价'));
assert.ok(page.indexOf('id="report-charts"')>page.indexOf(paragraphs[5]));
assert.equal(chartCount(page.split('id="report-charts"')[0]),6);

// Data-rich report with one unrelated paragraph must not dump unused charts.
const unrelated=JSON.parse(JSON.stringify(report));
unrelated.summary.thesis_summary='公司与管理层谈判进展顺利，目前没有更新经营判断。';
assert.equal(chartCount(inline(unrelated)),0);
const single=JSON.parse(JSON.stringify(report));
single.summary.thesis_summary='经营现金流与净利润需要一起看，公司估值分位也偏高。';
assert.equal(chartCount(inline(single)),2,'at most two related figures under a paragraph');

const repeated=JSON.parse(JSON.stringify(report));
repeated.summary.thesis_summary=[
  '经营现金流比净利润还要多。','现金流和净利润的关系值得继续跟踪。',
].join('\n\n');
assert.equal(chartCount(inline(repeated)),1,'no chart duplication across repeated topics');

// History-only EPS×PE scenarios appear with their specific interpretation,
// never as standalone invented prices.
const scenarioReport=JSON.parse(JSON.stringify(report));
scenarioReport.valuation_model.investor_reference.earnings_power={
  status:'historical_sensitivity_only',
  market_price_yuan:51.5,
  pe_as_of:'2026-10-15',
  scenarios:[
    {name:'历史较弱',conditional_price_yuan:32.80},
    {name:'历史中枢',conditional_price_yuan:42.60},
    {name:'历史较强',conditional_price_yuan:50.14},
  ],
};
scenarioReport.summary.thesis_summary=
  paragraphs.slice(0,5).concat('用历史EPS和PE组合得到三档条件价格，需要和现价对照。').join('\n\n');
const scenarioHtml=inline(scenarioReport);
assert.ok(scenarioHtml.includes('历史盈利 × PE 条件价格'));
assert.ok(scenarioHtml.includes('32.80元'));
assert.ok(scenarioHtml.includes('42.60元'));
assert.ok(scenarioHtml.includes('50.14元'));
assert.equal(chartCount(scenarioHtml),7);
// Regression based on the user's Shenhua screenshot: narrative paragraph,
// related chart, next paragraph, related chart. Do not create one gallery.
const shenhuaParagraphs=[
  '中国神华煤电路港航一体化。2025年报前五大客户只占营收14.6%，2026年上半年经营现金流546.64亿元，是净利润的1.90倍。',
  '问题在于买入价格。PE(TTM)20.69倍，PB2.477倍，五年分位99.4%，估值处在历史高位。',
  '分红从2.55元降到2.01元，同时为了收购资产，资产负债率从23.31%抬到40.27%。',
  '拿三种历史参照价格比较：PB回到五年中位对应36.99元；用历史较强每股盈利3.503元配14.31倍PE，对应50.14元。',
  '公司业务不错，但能否买入还要看价格。'
];
const shenhua=JSON.parse(JSON.stringify(scenarioReport));
shenhua.summary.thesis_summary=shenhuaParagraphs.join('\n\n');
const grouped=ctx.assignVerdictFiguresToParagraphs(shenhuaParagraphs,shenhua);
assert.equal(JSON.stringify(grouped.map(row=>row.map(item=>item.id))),
  JSON.stringify([['cash','customers'],['valuation'],['dividends','debt'],
    ['earnings_power','price_refs'],[]]));
const reading=inline(shenhua);
assert.equal(chartCount(reading),7);
for(let i=0;i<shenhuaParagraphs.length-1;i++){
  const here=reading.indexOf(shenhuaParagraphs[i]);
  const next=reading.indexOf(shenhuaParagraphs[i+1]);
  assert.ok(here>=0&&next>here,'all source paragraphs preserved in order');
  if(grouped[i].length){
    const chart=reading.indexOf(grouped[i][0].html,here);
    assert.ok(here<chart&&chart<next,'supporting chart must immediately follow matching paragraph '+i);
  }
}

scenarioReport.valuation_model.investor_reference.earnings_power.status='insufficient_history';
assert.ok(!inline(scenarioReport).includes('历史盈利 × PE 条件价格'));

const partial=JSON.parse(JSON.stringify(report));
delete partial.fundamentals.facts.operating_cash_flow;
partial.valuation_history.pe_percentiles=[];
partial.dividend_chart=[];
partial.profitability_trend.periods=[];
delete partial.valuation_model.investor_reference;
const smaller=inline(partial);
assert.ok(!smaller.includes('利润与经营现金流'));
assert.ok(!smaller.includes('每10股现金分红'));
assert.ok(!smaller.includes('资产负债率前后比较'));
assert.ok(!smaller.includes('现价与历史PB参考价'));
assert.ok(smaller.includes('市净率 PB'));
assert.ok(!smaller.includes('市盈率 PE'));

const cashNegative=JSON.parse(JSON.stringify(report));
cashNegative.fundamentals.facts.operating_cash_flow=-12000000000;
cashNegative.fundamentals.facts.cash_to_profit_ratio=-0.42;
const negativeHtml=inline(cashNegative);
assert.ok(negativeHtml.includes('-120.00亿元'));
assert.ok(negativeHtml.includes('经营现金流为负数，数值保留负号'));
assert.ok(negativeHtml.includes('verdict-figure-compact'));
assert.ok(!negativeHtml.includes('verdict-track'));

// Escape all external metadata, including period labels.
const hostile=JSON.parse(JSON.stringify(report));
hostile.fundamentals.concentration_period='<svg onload=alert(1)>';
const safe=inline(hostile);
assert.ok(!safe.includes('<svg onload=alert(1)>'));
assert.ok(safe.includes('&lt;svg'));

console.log('Inline investment chart checks passed: paragraph-first placement, one-time matching, sources and missing data.');
