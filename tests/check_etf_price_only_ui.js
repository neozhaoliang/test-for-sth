/* ETF/issuer header regression test; runs without a browser or API access. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/app.py'), 'utf8');
const etfCss = fs.readFileSync(path.join(__dirname, '../web/research_desk.css'), 'utf8');
assert(etfCss.includes('#result .etf-whole-report-card > .etf-block'),
  'all ETF chapters must be styled as subsections inside the single card');
assert(etfCss.includes('#result .etf-whole-report-card > .report-header-inline.report-company-card.report-etf-card'),
  'ETF identity header must also lose its own outer border/shadow');
const from = source.indexOf('function verdictMetric(');
const to = source.indexOf("document.getElementById('submitBtn').addEventListener", from);
assert(from >= 0 && to > from, 'header render functions must exist');

const sink = {innerHTML: ''};
const context = {
  document: {getElementById(id) {
    assert.equal(id, 'result');
    return sink;
  }},
  escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, c =>
      ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  },
  numericOrNull(value) {
    if (value == null || value === '') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  },
  verdictPercentileRow(rows) {
    return rows?.find(r => r && typeof r.percentile === 'number') || null;
  },
  stanceLabel() { return '中性'; },
};
const renderers = vm.runInNewContext(source.slice(from, to) +
  '\n({renderVerdictMarketStrip,renderEtfResult,isEtfInstrument,renderResult})', context);

function metricCount(html) {
  return (html.match(/class="verdict-market-metric /g) || []).length;
}

const stock = {
  stock_name: '贵州茅台', stock_code: 'SH600519',
  realtime_quote: {latest_price: 1698, quote_time: '2026-10-22'},
  valuation: {pb: 7.2, roe_pct: 28},
  fundamentals: {facts: {cash_to_profit_ratio: 1.1}},
  valuation_history: {
    pe_percentiles: [{years: 5, percentile: 50}],
    pb_percentiles: [{years: 5, percentile: 55}],
  },
};
const stockHeader = renderers.renderVerdictMarketStrip(stock);
assert.equal(metricCount(stockHeader), 6, 'normal stocks retain all six metrics');
assert(stockHeader.includes('市净率 PB'));
assert(stockHeader.includes('F10 披露ROE'));

const legacyEtf = {
  stock_name: '黄金股ETF永赢', stock_code: 'SH517520',
  realtime_quote: {latest_price: 1.567, quote_time: '2026-10-22'},
};
const legacyHeader = renderers.renderVerdictMarketStrip(legacyEtf);
assert.equal(renderers.isEtfInstrument(legacyEtf), true);
assert.equal(metricCount(legacyHeader), 1);
assert(legacyHeader.includes('1.567 元'));
for (const field of ['市净率 PB', 'F10 披露ROE', '现金流/归母净利', '历史分位']) {
  assert(!legacyHeader.includes(field), 'legacy ETF must omit ' + field);
}

const etf = {
  asset_type: 'etf', stock_name: '黄金股ETF永赢',
  stock_code: 'SH517520', as_of: '2026-10-22',
  etf_data: {
    fund: {latest_price: 1.567, quote_time: '2026-10-22 14:20'},
    scope: '黄金产业股票ETF',
    portfolio: {holdings: [], as_of: '2026-Q2'},
    portfolio_stats: {},
  },
  summary: {stance: 'neutral', thesis_summary: '基金研究摘要', sections: {}},
};
renderers.renderEtfResult(etf);
assert.equal(metricCount(sink.innerHTML), 1,
  'new ETF report should have one quote box, not a six-column issuer strip');
assert(sink.innerHTML.includes('ETF现价'));
assert(sink.innerHTML.includes('1.567 元'));
assert(sink.innerHTML.includes('最新披露前十大重仓股'));
const holdingsHeaderStart = sink.innerHTML.indexOf('class="etf-holdings-heading"');
const holdingsHeaderEnd = sink.innerHTML.indexOf('</div>', holdingsHeaderStart);
const holdingsHeaderHtml = sink.innerHTML.slice(holdingsHeaderStart, holdingsHeaderEnd);
assert(holdingsHeaderStart >= 0 && holdingsHeaderHtml.includes('<h3>最新披露前十大重仓股</h3>'),
  'top-ten title must share the heading row with its disclosure metadata');
assert(holdingsHeaderHtml.includes('class="etf-source etf-holdings-meta"'),
  'source and date must be in the title row rather than below it');
assert(holdingsHeaderHtml.includes('持仓截止日：'));
assert(holdingsHeaderHtml.includes('定期报告持仓'));
assert(etfCss.includes('grid-template-columns:max-content minmax(0,1fr)'),
  'desktop top-ten heading should have a title column and right-side metadata');
assert(etfCss.includes('#result .report-etf-card .etf-holdings-heading .etf-holdings-meta'),
  'right-side disclosure metadata styling should be scoped to ETF');
assert(etfCss.includes('@media(max-width:760px)'),
  'small screens should stack title and metadata without horizontal overflow');
// The entire ETF report, including narrative, risk notes and limitations,
// must have exactly ONE outer visible card (no separate chapter cards).
assert.equal((sink.innerHTML.match(/class="etf-whole-report-card"/g)||[]).length, 1);
assert(sink.innerHTML.startsWith('<article class="etf-whole-report-card"'),
  'ETF report should start with the sole card wrapper');
assert(sink.innerHTML.trim().endsWith('</article>'),
  'full report, not just the header, must close inside the one outer card');
const reportSections=[
  'report-top', 'etf-latest-top-ten', 'etf-unified-evidence', 'report-verdict',
];
reportSections.forEach(id=>assert(sink.innerHTML.includes('id="'+id+'"'),
  'all report sections must remain visible: '+id));
assert(sink.innerHTML.includes('id="etf-unified-evidence"'),
  'one combined research panel must follow the top-ten');
assert.equal((sink.innerHTML.match(/class="etf-block" id="etf-(macro-evidence|cycle-evidence|holdings)"/g)||[]).length,0,
  'no three separate analysis cards');
assert(sink.innerHTML.indexOf('id="etf-latest-top-ten"') <
       sink.innerHTML.indexOf('id="etf-unified-evidence"'));
assert(sink.innerHTML.indexOf('id="etf-unified-evidence"') <
       sink.innerHTML.indexOf('id="report-verdict"'));

const outerCardStart = sink.innerHTML.indexOf('class="report-header-inline report-company-card report-etf-card"');
const holdingsWithinCard = sink.innerHTML.indexOf('id="etf-latest-top-ten"');
const verdictOutsideCard = sink.innerHTML.indexOf('id="report-verdict"');
assert(outerCardStart >= 0 && holdingsWithinCard > outerCardStart &&
  verdictOutsideCard > holdingsWithinCard, 'ETF identity, price and holdings must precede the separate verdict');
assert(!sink.innerHTML.slice(outerCardStart, holdingsWithinCard).includes('</section>'),
  'ETF identity card must remain open until after top-ten holdings');
assert(sink.innerHTML.slice(holdingsWithinCard, verdictOutsideCard).includes('</div></section>'),
  'top-ten holdings and outer ETF card must close together before the investment verdict');
for (const field of ['市净率 PB', 'F10 披露ROE',
  '现金流/归母净利', 'IOPV参考溢价', 'ETF一年价格区间位置']) {
  assert(!sink.innerHTML.includes(field), 'ETF headline must omit ' + field);
}

etf.etf_data.fund.latest_price = null;
etf.etf_data.etf_price_position = {
  recent_close: 1.716, recent_close_as_of: '2026-10-21',
};
etf.etf_data.portfolio.disclosure_as_of = '2026-06-30';
etf.etf_data.portfolio.holdings = Array.from({length: 10}, (_, i) => ({
  code: '600' + String(100 + i), name: '持仓' + (i + 1),
  weight_pct: 10 - i * 0.5,
}));
etf.etf_data.holding_risk={
  risk_type:'指数被动高位承接风险',
  risk_summary:'核查指数是否在股票高估值时被动加权。',
  management_style:{kind:'index',label:'可能为指数跟踪ETF',
                    source:'基金名称提示，非合同核实'},
  price_observed_weight_pct:30,
  high_price_weight_pct:12,
  high_valuation_weight_pct:8,
  watchlist:[{name:'样本',weight_pct:8,flags:['高估值分位']}],
  verification_needed:['指数编制和季度调样公告'],
  limitations:'价格高位不等于泡沫。',
};
renderers.renderEtfResult(etf);
assert.equal(metricCount(sink.innerHTML), 1);
assert(sink.innerHTML.includes('最近交易日收盘价'));
assert(sink.innerHTML.includes('指数被动高位承接风险'));
assert(sink.innerHTML.includes('高估值分位'));
assert(sink.innerHTML.includes('指数编制和季度调样公告'));
assert.equal((sink.innerHTML.match(/class="etf-whole-report-card"/g)||[]).length,1);
assert(sink.innerHTML.trim().endsWith('</article>'));
assert(sink.innerHTML.includes('1.716 元'));
assert(sink.innerHTML.includes('2026-06-30'));
assert(sink.innerHTML.indexOf('etf-latest-top-ten') <
  sink.innerHTML.indexOf('report-verdict'), 'top ten must precede narrative');
assert((sink.innerHTML.match(/持仓[0-9]+/g) || []).length >= 10);
assert(sink.innerHTML.includes('600109'), 'must include tenth holding');
etf.etf_data.etf_price_position = {};
etf.etf_data.holding_risk={
  risk_type:'指数被动高位承接风险',
  management_style:{kind:'index',label:'可能为指数跟踪ETF'},
  risk_summary:'必须进一步核实指数调样与持仓估值。',
  disclosed_holding_count:10,
  price_observed_count:0,
  valuation_observed_count:0,
  price_observed_weight_pct:0,
  valuation_observed_weight_pct:0,
  high_price_weight_pct:null,
  high_valuation_weight_pct:null,
  watchlist:[],
};
renderers.renderEtfResult(etf);
assert(sink.innerHTML.includes('暂缺'));
assert(sink.innerHTML.includes('未取得有效日线'));
assert(sink.innerHTML.includes('0/10只'));
assert(sink.innerHTML.includes('尚无法判断'));
assert(sink.innerHTML.includes('不是“高位股权重为0%”'),
  'missing ten-stock data must NOT be rendered as verified zero high-price holdings');
console.log('ETF price-only and latest top-ten checks passed: current, dated close, 10 holdings, zero coverage vs missing, legacy and stock.');


// This is the *original production failure*: /api/tasks stripped asset_type
// and etf_data, causing an ETF's narrative to be rendered as a stock chart.
// Old payloads must render ETF-only content and independently rehydrate.
(async () => {
  const oldProjected = {
    stock_code: 'SH517520',
    stock_name: '黄金股ETF永赢',
    summary: {stance: 'neutral', thesis_summary: '旧个股模板的无效结论'},
  };
  let requested = '';
  context.fetch = async url => {
    requested = url;
    return {
      ok: true,
      json: async () => ({
        asset_type: 'etf', stock_code: 'SH517520',
        stock_name: '黄金股ETF永赢',
        etf_data: {
          fund: {
            latest_price: 1.716,
            quote_label: '最近交易日收盘价',
            quote_time: '2026-10-22 收盘（非实时）',
          },
          portfolio: {
            disclosure_as_of: '2026-06-30', as_of: '2026-Q2',
            source: '公开定期报告',
            holdings: Array.from({length: 10}, (_, i) => ({
              code: '600' + String(100 + i),
              name: '真实披露重仓' + (i + 1),
              weight_pct: 9 - i * 0.5,
            })),
          },
          portfolio_stats: {top10_weight_pct: 67.5},
          etf_price_position: {},
        },
      }),
    };
  };
  renderers.renderResult(oldProjected);
  assert(sink.innerHTML.includes('ETF基金与现价'),
    'legacy stock-shaped ETF must route to ETF card, not issuer card');
  assert(sink.innerHTML.includes('最新披露前十大重仓股'));
  assert(!sink.innerHTML.includes('数据概览与维度图谱'));
  assert(!sink.innerHTML.includes('盈利质量的变化'));
  assert(!sink.innerHTML.includes('历史估值区间'));
  assert(!sink.innerHTML.includes('旧个股模板的无效结论'),
    'invalid legacy stock opinion must not masquerade as ETF research');
  await new Promise(resolve => setImmediate(resolve));
  assert(requested.includes('/api/etfs/overview?code=SH517520'),
    'legacy task must obtain independent ETF latest disclosures');
  assert(sink.innerHTML.includes('1.716 元'));
  assert(sink.innerHTML.includes('2026-06-30'));
  assert(sink.innerHTML.includes('600109'));
  assert(!sink.innerHTML.includes('市净率 PB'));
  assert.equal((sink.innerHTML.match(/class="etf-whole-report-card"/g)||[]).length,1,
    'legacy ETF must also use a single card');
  assert(sink.innerHTML.trim().endsWith('</article>'));
  console.log('Legacy ETF task projection UI recovery passed: price, ten holdings, single card, no issuer charts.');
})().catch(error => { console.error(error); process.exitCode=1; });
