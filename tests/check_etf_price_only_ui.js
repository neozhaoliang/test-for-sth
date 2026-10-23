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
etf.summary.sections.holdings = '不应重复展示的持仓名单唯一测试标记';
renderers.renderEtfResult(etf);
assert(!sink.innerHTML.includes('不应重复展示的持仓名单唯一测试标记'),
  'do not repeat the top-ten table holdings in an additional prose section');
assert.equal(metricCount(sink.innerHTML), 3,
  'ETF report must show price, IOPV reference premium and turnover beside the name');
assert(sink.innerHTML.includes('ETF现价'));
assert(sink.innerHTML.includes('1.567 元'));
assert(sink.innerHTML.includes('溢价率'));
assert(sink.innerHTML.includes('暂无数据'),
  'premium must remain unknown without comparable IOPV');
assert(!sink.innerHTML.includes('缺少可比较的IOPV'));
assert(!sink.innerHTML.includes('来源未提供报价日期'));
assert(!sink.innerHTML.includes('二级市场成交'));
assert(sink.innerHTML.includes('成交额'));
assert(!sink.innerHTML.includes('近12个月分红率'),
  'ETFs without verified payouts must not show a dividend-yield card');
assert(!sink.innerHTML.includes('可供分配利润'),
  'do not show a blank official distributable-profit box');
assert(!sink.innerHTML.includes('最近每份分红'));
assert(sink.innerHTML.includes('class="etf-hero-row"'));
assert(etfCss.includes('grid-template-columns:minmax(250px,1fr) minmax(410px,1.28fr)'),
  'ETFs display fund name left and price/IOPV/turnover KPI cards right');
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
assert(!sink.innerHTML.includes('前十大重仓：究竟买了什么'),
  'the top-ten list is already in its own table; no redundant narrative card');
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
  '现金流/归母净利', 'ETF一年价格区间位置']) {
  assert(!sink.innerHTML.includes(field), 'ETF headline must omit ' + field);
}

// High-dividend ETFs must not repeat the same valuation and earnings
// mechanics in three different sections, and do not acquire a fictitious
// cash yield from their index name.
const dividendReport = {
  ...etf,
  stock_name: '华泰柏瑞中证港股通高股息投资ETF(QDII)',
  etf_data: {...etf.etf_data, distribution: {},
    trading_crowding: {margin_balance_yi: 2.41, margin_change_pct: 19.3}},
  summary: {
    stance: 'neutral',
    thesis_summary: '估值和盈利分化，考虑持有但不追高。',
    sections: {
      high_positions: '海丰国际价格高位和历史PE分位值得谨慎。ETF融资余额2.41亿元，成交额3.3亿元。',
      earnings_cycle: '海丰国际盈利增速放缓，派息率上升。',
      valuation: '这句话只重复海丰国际估值较高。',
      industry_cycle: '这句话只重复海丰国际盈利增速放缓。',
      commodity_market: '这句话只重复海丰国际派息可能下调。',
    },
    risk_notes: '海丰国际估值和派息的风险，再重复一次。',
  },
};
renderers.renderEtfResult(dividendReport);
assert(sink.innerHTML.includes('价格高位和历史PE分位'));
assert(!sink.innerHTML.includes('ETF融资余额2.41亿元，成交额3.3亿元。'),
  'do not repeat observed financing numbers in the price-position chapter');
assert(sink.innerHTML.includes('盈利增速放缓，派息率上升'));
for (const duplicated of [
  '只重复海丰国际估值', '只重复海丰国际盈利', '只重复海丰国际派息',
  '海丰国际估值和派息的风险，再重复一次。',
  '近12个月分红率', '可供分配利润', '最近每份分红',
]) assert(!sink.innerHTML.includes(duplicated),
  'do not emit duplicate dividend-ETF analysis or unverified payout metric: '+duplicated);

// HK-exposed ETFs should display two-cycle evidence, but an A-share ETF
// with no HK liquidity payload should never inherit these macro assumptions.
const hkReport = {
  ...etf,
  stock_name:'华泰柏瑞中证港股通高股息投资ETF(QDII)',
  etf_data: {
    ...etf.etf_data,
    distribution:{},
    hk_liquidity: {
      exposure:'hk_equities',
      as_of:'2026-10-23',
      hkma:{status:'observed',as_of:'2026-10-22',
        hibor_1m_pct:4.1,hibor_1m_change_bp:110,
        aggregate_balance_hkd_million:54125,
        aggregate_balance_change_pct:-18.3,
        source:'香港金融管理局'},
      fed:{status:'observed',as_of:'2026-10-22',
        fed_target_upper_pct:4.25,fed_target_change_60d_bp:25,
        source:'FRED'},
      china_cpi:{status:'observed',as_of:'2026-10-15',
        yoy_pct:-0.1,source:'中国CPI'},
      china_ppi:{status:'observed',as_of:'2026-10-15',
        yoy_pct:-2.4,source:'中国PPI'},
      assessment:{
        funding_assessment:'港元及美元融资条件共同收紧。',
        china_inflation_assessment:'CPI和PPI同比均为负，盈利面临压力。',
      },
    },
  },
  summary:{stance:'neutral',thesis_summary:'中国盈利及港元利率共同影响估值。',
    sections:{},risk_notes:''},
};
renderers.renderEtfResult(hkReport);
for(const expected of [
  '港股：内地基本面与美元/港元流动性', '1个月HIBOR', '4.10%',
  '香港银行体系总结余', '541.3 亿港元', '目标利率近60日变动',
  '中国CPI同比','中国PPI同比','港元及美元融资条件共同收紧',
]) assert(sink.innerHTML.includes(expected), 'Missing HK liquidity panel: '+expected);
assert(!sink.innerHTML.includes('买股票的老木匠'));
renderers.renderEtfResult(etf);
assert(!sink.innerHTML.includes('港股：内地基本面与美元/港元流动性'));


// Test both genuine discount and an observed turnover, with amounts scaled.
etf.etf_data.distribution = {
  dividend_ttm_per_share: 0.12,
  dividend_yield_ttm_pct: 8.05,
  distributable_profit_yuan: 1057954195.22,
  latest_dividend_per_share: 0.01,
  distributable_profit_as_of: '2026-09-30',
};
etf.etf_data.fund.iopv = 1.58;
etf.etf_data.fund.iopv_premium_pct = -0.82;
etf.etf_data.fund.turnover_yuan = 148040000;
renderers.renderEtfResult(etf);
assert(sink.innerHTML.includes('溢价率'));
assert(!sink.innerHTML.includes('IOPV参考折价'));
assert(sink.innerHTML.includes('-0.82%'));
assert(sink.innerHTML.includes('1.48 亿元'));
assert(sink.innerHTML.includes('8.05%'));
assert(sink.innerHTML.includes('10.58 亿元'));
assert(sink.innerHTML.includes('0.0100 元'));
assert.equal(metricCount(sink.innerHTML),3);
etf.summary.sections.fund_structure='基金名称提示可能为指数产品，这段提示词不应出现在页面';
etf.summary.sections.market_price='IOPV为null，latest_shares为null，错误叙述';
renderers.renderEtfResult(etf);
assert(!sink.innerHTML.includes('基金名称提示可能为指数产品'));
assert(!sink.innerHTML.includes('latest_shares为null'));
etf.etf_data.fund.latest_price = null;
etf.etf_data.etf_price_position = {
  recent_close: 1.716, recent_close_as_of: '2026-10-21',
};
// A dated close must not borrow intraday IOPV or turnover.
etf.etf_data.fund.iopv = null;
etf.etf_data.fund.iopv_premium_pct = null;
etf.etf_data.fund.turnover_yuan = null;
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
assert.equal(metricCount(sink.innerHTML), 3);
assert(sink.innerHTML.includes('最近交易日收盘价'));
assert(sink.innerHTML.includes('指数被动高位承接风险'));
assert(sink.innerHTML.includes('高估值分位'));
assert(!sink.innerHTML.includes('指数编制和季度调样公告'));
assert(!sink.innerHTML.includes('基金名称提示，非合同核实'));
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
assert(sink.innerHTML.includes('不能判断高位股实际占比'),
  'missing ten-stock data must NOT be rendered as verified zero high-price holdings');
console.log('ETF price-only and latest top-ten checks passed: current, dated close, 10 holdings, zero coverage vs missing, legacy and stock.');


// Broad-based ETFs use actual benchmark evidence, NOT commodity cycle
// boilerplate; quote data and ETF financing remain independent.
const broadReport = {
  ...etf,
  stock_code:'SH510300',
  stock_name:'华泰柏瑞沪深300ETF',
  etf_data: {
    ...etf.etf_data,
    distribution: {},
    broad_market: {
      profile: {family:'沪深300',index_code:'000300'},
      benchmark_price: {
        status:'observed',as_of:'2026-10-23',
        ytd_pct:26.3,return_20d_pct:5.8,return_60d_pct:16.2,
        ytd_max_drawdown_pct:-11.4,price_position_52w_pct:83.2,
        recent5_vs_prior20_amount_ratio:1.88,
      },
      index_valuation:{status:'observed',as_of:'2026-10-22',
                       pe_ttm:16.3,pe_3y_percentile_pct:82.2},
      constituent_changes:{
        recent_entries:[{
          name:'高位样本',code:'688256',included_on:'2026-09-15',
          pre_inclusion_60d_return_pct:76.5,momentum_flag:true,
        }],
      },
      sentiment_observation:'近20日指数涨幅较高且成交明显放大',
    },
    macro_market:{observations:{
      china_lpr1y:{name:'中国1年LPR',value:3.1,unit:'%',
                   as_of:'2026-10-20',source:'公开数据'},
    }},
    industry_cycle:{stage:'不应显示商品周期',earnings_breadth:{}},
  },
  summary:{
    stance:'neutral',thesis_summary:'指数估值分位上升，回撤风险需要关注。',
    sections:{valuation:'指数整体估值应审慎分析。'},
    risk_notes:'指数估值风险被重复说明。',
  },
};
renderers.renderEtfResult(broadReport);
for(const must of ['沪深300：年内行情、量能与市场情绪',
  '指数年初以来','26.30%','年内最大回撤','-11.40%',
  '近5日/前20日指数成交额','1.88 倍','指数PE近3年分位',
  '宏观政策与流动性观察','中国1年LPR','指数调样与高位纳入观察',
  '高位样本','76.5%','纳入前上涨较快']) {
  assert(sink.innerHTML.includes(must), 'missing broad-market observation: '+must);
}
for(const unrelated of ['行业周期与前十大盈利广度',
  '不应显示商品周期','宏观、流动性与产品价格：最新可核实数据',
  '重仓股、宏观与估值风险分析','近12个月分红率',
  '指数估值风险被重复说明。']) {
  assert(!sink.innerHTML.includes(unrelated), 'broad ETF has unrelated legacy sections: '+unrelated);
}
assert.equal(metricCount(sink.innerHTML),3);
assert(sink.innerHTML.includes('指数成交额与ETF自身成交额不同'));
assert(sink.innerHTML.indexOf('etf-broad-market') <
       sink.innerHTML.indexOf('etf-unified-evidence'));

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
