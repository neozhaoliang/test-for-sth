/* ETF/issuer header regression test; runs without a browser or API access. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web/app.py'), 'utf8');
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
renderers.renderEtfResult(etf);
assert.equal(metricCount(sink.innerHTML), 1);
assert(sink.innerHTML.includes('最近交易日收盘价'));
assert(sink.innerHTML.includes('1.716 元'));
assert(sink.innerHTML.includes('2026-06-30'));
assert(sink.innerHTML.indexOf('etf-latest-top-ten') <
  sink.innerHTML.indexOf('report-verdict'), 'top ten must precede narrative');
assert((sink.innerHTML.match(/持仓[0-9]+/g) || []).length >= 10);
assert(sink.innerHTML.includes('600109'), 'must include tenth holding');
etf.etf_data.etf_price_position = {};
renderers.renderEtfResult(etf);
assert(sink.innerHTML.includes('暂缺'));
console.log('ETF price-only and latest top-ten checks passed: current, dated close, 10 holdings, legacy and stock.');


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
  console.log('Legacy ETF task projection UI recovery passed: price, ten holdings, no issuer charts.');
})().catch(error => { console.error(error); process.exitCode=1; });
