/* Regression checks for dashboard charts, no duplicate twelve-dimension view,
 * and no universal ROE-based target-price calculator.
 */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const file=process.argv[2];
if(!file)throw new Error('Please pass extracted web/app.py script');
const source=fs.readFileSync(file,'utf8');
const prefix=source.split("document.getElementById('submitBtn').addEventListener")[0];
assert.ok(!prefix.includes('function calculateTwoStageValuation('));
assert.ok(!prefix.includes('function renderValuationLab('));
assert.ok(prefix.includes('function renderIndustryValuationLab('));
const nodes={};
const mockDocument={
  // Tooltips are attached to actual SVG nodes in browsers. The VM fixture
  // intentionally has none, so DOM querying should return an empty NodeList.
  querySelectorAll(){return [];},
  createElement(){
    let text='';
    return {set textContent(value){text=String(value);},
      get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;')
        .replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');}};
  },
  getElementById(id){
    if(!nodes[id])nodes[id]={value:'',textContent:'',innerHTML:'',listeners:{},
      addEventListener(type,handler){this.listeners[type]=handler;}};
    return nodes[id];
  },
};
const ctx=vm.createContext({document:mockDocument,console,Date});
vm.runInContext(prefix,ctx,{timeout:8000});
const report={
  as_of:'2026-10-09',stock_code:'601717',stock_name:'示例公司',research_mode:'live',
  valuation:{nav_per_share:13.27,pb:1.02,roe_pct:7.02},
  realtime_quote:{latest_price:13.35},
  profitability_trend:{periods:[
    {period:'2024-12-31',roe_pct:15,gross_margin_pct:30,net_margin_pct:9},
    {period:'2025-12-31',roe_pct:18.88,gross_margin_pct:31,net_margin_pct:10},
    {period:'2026-06-30',roe_pct:7.02,gross_margin_pct:28,net_margin_pct:8}]},
  valuation_model:{status:'insufficient_evidence',
    route:{primary:'owner_fcfe'},
    valuation:{status:'insufficient_evidence',missing:['annual_cash_flow_per_share']},
    evidence_gate:{missing:['annual_cash_flow_per_share']},interactive_inputs:null},
  candidates:[],summary:{dimension_scores:[]},dividend_chart:[],fundamentals:{facts:{}},
};
assert.ok(ctx.renderProfitabilityTrendChart(report.profitability_trend).includes('<svg'));
assert.ok(ctx.renderDashboardCharts(report).includes('数据概览与维度图谱'));
// The four original dashboard KPIs are now in the investment conclusion,
// together with historical PE/PB percentiles, like a broker quote row.
const headlineReport={
  ...report,
  as_of:'2026-10-20',
  realtime_quote:{latest_price:82.94,quote_time:'2026-10-20'},
  valuation:{pb:3.17,roe_pct:4.86,valuation_as_of:'2026-10-16'},
  valuation_history:{
    as_of:'2026-10-20',
    pe_percentiles:[{years:5,percentile:39.6}],
    pb_percentiles:[{years:5,percentile:1.1}],
  },
  fundamentals:{facts:{cash_to_profit_ratio:null,finance_period:'2026-03-31'}},
  summary:{stance:'neutral',thesis_summary:'公司的估值与股东回报需要放在一起评估。',
    dimension_scores:[]},
};
const marketHtml=ctx.renderVerdictMarketStrip(headlineReport);
assert.ok(marketHtml.includes('aria-label="投资结论行情与关键指标"'));
assert.equal((marketHtml.match(/class="verdict-market-metric /g)||[]).length,6);
for(const expected of [
  '82.94 元','2026-10-20','市净率 PB','3.17 倍',
  'F10 披露ROE','4.86%','2026-10-16',
  '现金流/归母净利','暂缺','2026-03-31',
  'PE','39.6%','PB','1.1%','历史5年分位',
]){
  assert.ok(marketHtml.includes(expected),'Missing investment KPI: '+expected);
}
assert.ok(marketHtml.includes('verdict-market-price'));
assert.ok(marketHtml.includes('verdict-market-valuation'));
assert.ok(marketHtml.includes('verdict-market-profit'));
assert.ok(marketHtml.includes('verdict-market-missing'));
assert.ok(marketHtml.includes('verdict-market-mid'));
assert.ok(marketHtml.includes('verdict-market-low'));

ctx.renderResult(headlineReport);
const rendered=nodes.result.innerHTML;
const conclusion=rendered.indexOf('<h3>投资结论</h3>');
const kpis=rendered.indexOf('class="verdict-market-strip"');
const stance=rendered.indexOf('class="verdict-line"');
const thesis=rendered.indexOf('公司的估值与股东回报');
const dashboard=rendered.indexOf('id="report-charts"');
assert.ok(conclusion>=0 && conclusion<kpis && kpis<stance && stance<thesis);
assert.ok(thesis<dashboard);
assert.equal((rendered.match(/class="verdict-market-strip"/g)||[]).length,1);
assert.ok(!ctx.renderDashboardCharts(headlineReport).includes('primary-kpis'),
  'no duplicate quote or financial KPI row in the later dashboard');
assert.ok(!ctx.renderDashboardCharts(headlineReport).includes('82.94 元'));

const css=fs.readFileSync('web/research_desk.css','utf8');
assert.ok(css.includes('#result #report-verdict .verdict-market-strip'));
assert.ok(css.includes('grid-template-columns:repeat(3,minmax(0,1fr))'));
assert.ok(css.includes('grid-template-columns:repeat(2,minmax(0,1fr))'));
assert.ok(css.includes('.verdict-market-price'));
assert.ok(css.includes('.verdict-market-low'));
assert.ok(css.includes('.stock-ytd-position-meta'));

// Display all dates independently rather than attaching the latest quote
// date to an older F10 ROE or an even older quarterly cash-flow figure.
const dates=['2026-10-20','2026-10-16','2026-03-31'];
for(const date of dates)assert.ok(marketHtml.includes(date));
const missingHistory=JSON.parse(JSON.stringify(headlineReport));
delete missingHistory.valuation_history;
const sparseKpis=ctx.renderVerdictMarketStrip(missingHistory);
assert.ok(sparseKpis.includes('历史分位暂缺'));
assert.ok(!sparseKpis.includes('39.6%'));
assert.ok(!sparseKpis.includes('1.1%'));

assert.ok(ctx.renderChipPositionChart({
    status:'contextualized',position_52w_pct:12.5,
    holder_change_pct:20,holder_period:'2026-09-30',
    holder_context:'low_price_more_holders_neutral',
  }).includes('低位增户：不自动扣分'));
assert.ok(ctx.renderChipPositionChart({
    status:'contextualized',position_52w_pct:89,
    holder_change_pct:20,holder_period:'2026-09-30',
    holder_context:'high_price_more_holders_watch',
  }).includes('高位分散：待核验'));
const aligned=ctx.renderChipPositionChart({
  status:'contextualized',
  position_52w_pct:40,
  price_holder_series:[
    {period:'2026-06-30',price_date:'2026-06-30',holders:100000,price:10},
    {period:'2026-09-30',price_date:'2026-09-29',holders:120000,price:12},
  ],
});
assert.ok(aligned.includes('上涨伴随明显增户，筹码分散风险上升'));
assert.ok(aligned.includes('区间股价'));
assert.ok(aligned.includes('+20.00%'));
assert.ok(aligned.includes('股东户数'));
assert.ok(aligned.includes('2026-09-29'));
assert.ok(aligned.includes('股东人数（万户，左轴）'));
assert.ok(aligned.includes('股价（元，右轴）'));
assert.ok(aligned.includes('股东人数（万户）'));
assert.ok(aligned.includes('股价（元）'));
assert.ok(aligned.includes('10.00'));
assert.ok(aligned.includes('12.00'));
assert.ok(aligned.includes('实际收盘价'));
assert.ok(!aligned.includes('<table'));
assert.ok(!aligned.includes('chip-holder-table'));
assert.ok(!aligned.includes('<th>统计截止日'));
assert.ok(!aligned.includes('首期=100'));
assert.ok(aligned.includes('10.00 万户'));
// Values stay as real absolute measurements; no 100-index transformation.
assert.ok(aligned.includes('左轴显示股东人数（万户），右轴显示股价（元）'));
assert.ok(!aligned.includes('52周低位'));  // genuine history replaces gauge

// The user's real report: price down 25.09%, holder accounts up 81.37%.
// This is a dispersion warning, NOT a sign of healthier shareholder structure.
const reported=ctx.renderChipPositionChart({
  price_holder_series:[
    {period:'2024-09-30',holders:100000,price:100},
    {period:'2025-03-31',holders:145000,price:82},
    {period:'2025-06-30',holders:200000,price:70},
    {period:'2026-06-30',holders:181370,price:74.91},
  ],
});
assert.ok(reported.includes('-25.09%'));
assert.ok(reported.includes('+81.37%'));
assert.ok(reported.includes('股价下跌、股东户数大增，筹码趋于分散'));
assert.ok(reported.includes('这不能解释为筹码改善'));
assert.ok(reported.includes('最明显的一段是'));
assert.ok(reported.includes('2024-09-30 至 2025-03-31'));
assert.ok(!reported.includes('区间股价变化'));
assert.ok(!reported.includes('chip-history-summary'));

// Price DOWN while holders DOWN is possible concentration, not proof of absorption.
const downAndFewer=ctx.explainHolderPriceMovement([
  {period:'2025-12-31',price:100,holders:10000},
  {period:'2026-06-30',price:70,holders:7000},
]);
assert.equal(downAndFewer.status,'improve');
assert.ok(downAndFewer.headline.includes('筹码集中迹象'));
assert.ok(downAndFewer.explanation.includes('不能证明抛压已经结束'));

// Rally with growing holder count is a dispersion warning.
const upAndMore=ctx.explainHolderPriceMovement([
  {period:'2025-12-31',price:100,holders:10000},
  {period:'2026-06-30',price:145,holders:14800},
]);
assert.equal(upAndMore.status,'risk');
assert.ok(upAndMore.headline.includes('上涨伴随明显增户'));

// Rally with fewer holders suggests concentration, but no inferred institution.
const upAndFewer=ctx.explainHolderPriceMovement([
  {period:'2025-12-31',price:100,holders:10000},
  {period:'2026-06-30',price:125,holders:8100},
]);
assert.equal(upAndFewer.status,'improve');
assert.ok(upAndFewer.explanation.includes('不能据此断定是机构持续吸筹'));

// A price move without a meaningful holder-count change is neutral.
const flatCounts=ctx.explainHolderPriceMovement([
  {period:'2025-12-31',price:100,holders:10000},
  {period:'2026-06-30',price:90,holders:10200},
]);
assert.equal(flatCounts.status,'neutral');
assert.ok(flatCounts.headline.includes('暂未出现明显筹码信号'));

// A drawdown with increasing accounts cannot be mislabeled as "improving".
const downAndMore=ctx.explainHolderPriceMovement([
  {period:'2025-12-31',price:100,holders:10000},
  {period:'2026-06-30',price:80,holders:14000},
]);
assert.equal(downAndMore.status,'risk');

const insufficientHistory=ctx.renderChipPositionChart({
  status:'insufficient_data',price_holder_series:[],
});
assert.ok(insufficientHistory.includes('尚未取得至少两个'));
const f10Legacy={
  ...report,
  stock_code:'SH601899',
  as_of:'2026-10-14',
  research_mode:'live',
  fundamentals:{facts:{holder_count_series:[
    {period:'2026-06-30',holders:1318365,price:25.14},
    {period:'2026-03-31',holders:1252002,price:32.72},
    {period:'2025-12-31',holders:584983,price:34.47},
  ]}},
};
const legacyChipHtml=ctx.renderChipPositionChart(undefined,f10Legacy);
assert.ok(legacyChipHtml.includes('<svg'));
assert.ok(legacyChipHtml.includes('131.84'));
assert.ok(legacyChipHtml.includes('125.20'));
assert.ok(legacyChipHtml.includes('58.50'));
assert.ok(!legacyChipHtml.includes('<table'));
assert.ok(!legacyChipHtml.includes('统计截止日'));
assert.ok(legacyChipHtml.includes('同花顺F10'));
assert.ok(legacyChipHtml.includes('F10参考股价'));
assert.ok(legacyChipHtml.includes('不是独立行情接口核验的收盘价'));
assert.ok(!legacyChipHtml.includes('已披露股东户数与对应统计截止日之前最近交易日的实际收盘价'));
assert.ok(!ctx.renderDashboardCharts(f10Legacy).includes('131.84'));
ctx.renderResult(f10Legacy);
assert.ok(nodes.result.innerHTML.includes('131.84'));
assert.ok(nodes.result.innerHTML.includes('id="report-shareholder-evidence"'));
assert.ok(!nodes.result.innerHTML.includes('id="report-dimension-chip_flow"'));
// Historical replay must never backfill as-of charts from a live F10 webpage.
const historicalChip=ctx.renderChipPositionChart(undefined,{
  ...f10Legacy,research_mode:'historical',
});
assert.ok(!historicalChip.includes('131.84'));
assert.ok(historicalChip.includes('本次报告缺少已核验的股价与股东户数配对数据'));


// Reader-facing market commentary should be prose, not an LLM review log.
const longHypotheses=[
  {mechanism:'若科技ETF集中发行而红利ETF供给有限，市场配置资金可能更向科技集中，使红利相对抗跌来自资金分配差异。',
   conditions:'ETF持仓和相对表现均发生变化，仍需检验投资者到底买了什么。'},
  {mechanism:'若跨境资金未流回国内，公募高成交仍可能是存量基金内部换仓，不能证明已经有更多投资资金。',
   conditions:'需要观察公募申赎与持仓分解。'},
  {mechanism:'若股息率相对国债收益率有吸引力，红利资产可能受到配置需求支撑。',
   conditions:'需要对比国债收益率和实际派息。'},
  {mechanism:'若游资量化交易变得活跃，短线换手会放大红利资产的波动。',
   conditions:'需要核对真实的换手和流动性。'},
].map(p=>({...p,evidence_status:'private_research_hypothesis'}));
const whyHtml=ctx.renderInvestmentStyleStrip({style_context:{
  status:'observed',style_regime:'dividend_leading',
  style_observed_as_of:'2026-10-14',causal_drivers:longHypotheses,
  dividend_index_return_pct:0.03,growth_index_mean_return_pct:-9.69,
  causal_knowledge_status:'distilled_hypotheses',
}});
assert.equal((whyHtml.match(/class="style-cause-item"/g)||[]).length,3);
assert.ok(whyHtml.includes('为什么会这样？'));
assert.ok(whyHtml.includes('资金配置'));
assert.ok(whyHtml.includes('利率与分红'));
assert.ok(whyHtml.includes('短线交易'));
assert.ok(!whyHtml.includes('<ol'));
assert.ok(!whyHtml.includes('<details'));
for(const internalHeading of ['成立条件、反向解释与核验办法','成立条件：','可能的反向解释：','需要核验：']){
  assert.ok(!whyHtml.includes(internalHeading));
}
assert.equal((whyHtml.match(/并不代表已证实/g)||[]).length,1);
assert.ok(whyHtml.includes('科技成长回撤，红利相对抗跌'));
// An empty set of hypotheses must be explicit rather than invented.
const whyEmpty=ctx.renderInvestmentStyleStrip({style_context:{
  status:'observed',style_regime:'mixed',
  causal_knowledge_status:'no_matching_distilled_knowledge',causal_drivers:[],
}});
assert.ok(whyEmpty.includes('背后的资金流向还缺少可靠证据'));


// Each support panel must be INSIDE its own dimension, directly AFTER
// its analysis prose. It must not also appear in the generic chart grid
// or as a standalone trailing corporate-capital chapter.
const orderlyKeys=['management','fundamentals','rd','chip_flow','price_position',
 'cycle_position','policy_geopolitics','retail_sentiment','shareholder_returns',
 'growth_elasticity','a_share_structure','risk_quality'];
const orderedReport={
 ...report,
 summary:{stance:'neutral',thesis_summary:'公司优秀不等于价格便宜。',
   dimension_scores:[],dimension_analyses:Object.fromEntries(
     orderlyKeys.map(k=>[k,'TEXT_FOR_'+k.toUpperCase()+'_END']))},
 chip_price_context:{price_holder_series:[
   {period:'2025-12-31',holders:584983,price:34.47},
   {period:'2026-03-31',holders:1252002,price:32.72},
   {period:'2026-06-30',holders:1318365,price:25.14},
 ]},
 dividend_chart:[{year:'2025',dividend_per_10:4,buyback_per_10:1,
    total_per_10:5,yield_pct:4}],
 shareholder_capital_summary:{
   completed_holder_sales:[{actor:'模拟大股东',disclosed_at:'2026-06-01',
     shares:250000,average_sale_price_yuan:29,
     source_url:'https://example.com/company_sale'}],
   equity_issuances:[{disclosed_at:'2026-04-10',shares:100000,
     document_title:'向特定对象发行股票结果公告',
     source_url:'https://example.com/private_placement'}],
   corporate_buybacks:[{status:'completed',disclosed_at:'2026-04-11',
     actual_amount_yuan:10000000,source_url:'https://example.com/buyback'},
     {status:'plan_only',disclosed_at:'2026-05-01',
     actual_amount_yuan:1000000,source_url:'https://example.com/plan'}],
 },
};
ctx.renderResult(orderedReport);
const ordered=nodes.result.innerHTML;
const stockPart=ordered.split('id="report-dimension-chip_flow"')[1]
  .split('id="report-dimension-price_position"')[0];
const returnPart=ordered.split('id="report-dimension-shareholder_returns"')[1]
  .split('id="report-dimension-growth_elasticity"')[0];
const overviewPart=ordered.split('id="report-charts"')[1]
  .split('id="report-dimensions"')[0];
assert.ok(stockPart.indexOf('TEXT_FOR_CHIP_FLOW_END') <
  stockPart.indexOf('股东人数与同期股价走势'));
assert.ok(stockPart.indexOf('股东人数与同期股价走势') <
  stockPart.indexOf('aria-label="股东人数'));
assert.ok(stockPart.includes('131.84'));
assert.ok(stockPart.includes('25.14'));
assert.ok(returnPart.indexOf('TEXT_FOR_SHAREHOLDER_RETURNS_END') <
  returnPart.indexOf('历年分红与回购'));
assert.ok(returnPart.includes('年度分红与回购柱状图'));
assert.ok(returnPart.includes('回购、股东减持与股权融资公告记录'));
assert.ok(returnPart.includes('模拟大股东'));
assert.ok(returnPart.includes('250,000'));
assert.ok(returnPart.includes('公司定向增发'));
assert.ok(returnPart.includes('10,000,000 元'));
assert.ok(returnPart.includes('待实施，实际回购规模暂缺'));
assert.ok(returnPart.includes('https://example.com/company_sale'));
assert.ok(returnPart.includes('https://example.com/private_placement'));
assert.ok(returnPart.includes('https://example.com/buyback'));
assert.ok(!overviewPart.includes('年度分红与回购柱状图'));
assert.ok(!overviewPart.includes('股东人数（万户'));
assert.ok(!overviewPart.includes('模拟大股东'));
assert.equal((ordered.match(/aria-label="年度分红与回购柱状图"/g)||[]).length,1);
assert.equal((ordered.match(/aria-label="股东人数（万户/g)||[]).length,1);
assert.equal((ordered.match(/id="report-capital"/g)||[]).length,1);
assert.ok(!ordered.includes('href="#report-capital"'));

// Dividend-chart fallback must render the dated source rows adjacent to
// shareholder-returns prose, without inventing payouts where none exist.
const datedFallback={
 ...orderedReport,dividend_chart:[],
 dividend_history:[{announce_date:'2026-09-01',dividend_per_10_shares:1.4,
   progress:'已实施'}],
 shareholder_capital_summary:{},
 buyback_history:[{announce_date:'2026-08-12',progress:'回购完成',
   actual_amount:'3000000元'}],
};
ctx.renderResult(datedFallback);
const fallbackReturns=nodes.result.innerHTML.split(
  'id="report-dimension-shareholder_returns"')[1].split(
  'id="report-dimension-growth_elasticity"')[0];
assert.ok(fallbackReturns.includes('2026-09-01'));
assert.ok(fallbackReturns.includes('每10股现金分红 1.4 元'));
assert.ok(fallbackReturns.includes('2026-08-12'));
assert.ok(fallbackReturns.includes('减持与定增：本次没有返回'));
assert.ok(!fallbackReturns.includes('id="report-capital"'));

const dividends=ctx.renderDividendChart([{year:'2025',dividend_per_10:4,
  buyback_per_10:1,total_per_10:5,yield_pct:4}]);
assert.ok(dividends.includes('回购不是现金分红'));
assert.ok(dividends.includes('非股息率'));
const keys=['management','fundamentals','rd','chip_flow','price_position',
  'cycle_position','policy_geopolitics','retail_sentiment','shareholder_returns',
  'growth_elasticity','a_share_structure','risk_quality'];
const analyses=Object.fromEntries(keys.map(k=>[k,'UNIQUE_DETAIL_'+k.toUpperCase()+'_END']));
report.summary={
  stance:'neutral',company_quality_stance:'bullish',current_odds_stance:'neutral',
  lynch_category:'cyclical',confidence:.7,
  dimension_analyses:analyses,dimension_scores:[],
  thesis_summary:'DIRECT_CONCLUSION_ONCE',
  core_counter_evidence:'INTERNAL_COUNTER_TOKEN',
  invalidation_condition:'INTERNAL_INVALIDATION_TOKEN',
  risk_notes:'INTERNAL_RISK_TOKEN',
};
report.research_quality={total_dimensions:12,coverage:.5,warnings:['INTERNAL_QUALITY_TOKEN']};
report.validation={ok:true,warnings:[{message:'INTERNAL_CONTRACT_TOKEN'}]};
report.review={duplicate_factors:[{message:'INTERNAL_REVIEW_TOKEN'}]};
report.research_profile={archetype:'cyclical',industry:'煤炭',label:'CYCLE',readiness:.5};
report.prompt_version='INTERNAL_PROMPT_TOKEN';
report.evidence=[{label:'INTERNAL_LEDGER_TOKEN'}];
report.candidates=[{user_nickname:'INTERNAL_CANDIDATE_TOKEN',wilson_score:0.5}];
ctx.renderResult(report);
const html=nodes.result.innerHTML;
assert.equal(html.split('<h3>投资结论</h3>').length-1,1);
assert.equal(html.split('DIRECT_CONCLUSION_ONCE').length-1,1);
assert.equal(html.split('<h3>多维度分析</h3>').length-1,1);
keys.forEach(k=>assert.equal(html.split('UNIQUE_DETAIL_'+k.toUpperCase()+'_END').length-1,1));
for(const forbidden of [
  '证据质量','报告合同校验通过','研究审查',
  '与结论相悖的最强证据','如果这个判断错了，会是因为','风险提示',
  'INTERNAL_COUNTER_TOKEN','INTERNAL_INVALIDATION_TOKEN','INTERNAL_RISK_TOKEN',
  'INTERNAL_QUALITY_TOKEN','INTERNAL_CONTRACT_TOKEN','INTERNAL_REVIEW_TOKEN',
  'INTERNAL_PROMPT_TOKEN','INTERNAL_LEDGER_TOKEN','INTERNAL_CANDIDATE_TOKEN',
  '证据账本','重点证据准备度'
]){
  assert.ok(!html.includes(forbidden),'Private/internal field leaked to UI: '+forbidden);
}
assert.ok(html.includes('当前可用估值数据较少'));
assert.ok(!html.includes('稳态模型隐含合理价格'));
// Older report versions stored twelve duplicated paragraphs in thesis_summary;
// do not repeat them when detailed analyses are present.
report.summary.thesis_summary='LEGACY_ESSAY_TOKEN ' + '长篇概述。'.repeat(130);
ctx.renderResult(report);
assert.equal(nodes.result.innerHTML.split('LEGACY_ESSAY_TOKEN').length-1,1);
keys.forEach(k=>assert.equal(
  nodes.result.innerHTML.split('UNIQUE_DETAIL_'+k.toUpperCase()+'_END').length-1,1));
// No per-dimension content in old cached report? Do not mirror thesis in a
// fake "研究分析" block immediately below the conclusion.
report.summary.dimension_analyses={};
report.summary.thesis_summary='ONE_CLEAR_VERDICT';
ctx.renderResult(report);
const older=nodes.result.innerHTML;
assert.equal(older.split('ONE_CLEAR_VERDICT').length-1,1);
assert.ok(!older.includes('<h3>研究分析</h3>'));

// Reference price bands must be rendered from data; no FCFE is fabricated.
report.valuation_model.investor_reference={
  status:'indicative_reference',
  dividend_basis:{years:[2021,2024,2025],observed_median_cash_dividend_per_share:.25,
    median_implemented_dividend_yield_pct:4.70},
  return_decomposition:{status:'illustrative_scenarios',holding_years:5,
    terminal_pb_basis:'基于历史PB的终点假设',
    scenarios:[{name:'中性',sustainable_roe_pct:8,payout_pct:50,
      retention_conversion_pct:50,growth_contribution_pct:2,
      cash_dividend_yield_pct:3,valuation_change_annualized_pct:1.5,
      illustrative_total_return_pct:6.5,required_return_pct:10,
      conditional_reference_price:5.12}]},
  historical_pb_anchor:{window_years:5,median_pb:1.15,
    median_pb_reference_price:6.31},
  earnings_power:{status:'historical_sensitivity_only',
    earnings_years:[2022,2023,2024,2025],pe_sample_months:48,
    scenarios:[{name:'历史中枢',observed_annual_eps_yuan:2.50,
      historical_pe_quantile:14.2,conditional_price_yuan:35.50,
      relative_to_market_pct:2.81}]},
};
report.valuation_model.company_view_coverage={
  status:'found_dated_hypotheses',count:2,latest_published_at:'2026-10-10',
};
report.valuation_model.style_context={
  status:'observed',style_regime:'dividend_leading',style_window:'20 trading days',
  dividend_minus_growth_ytd_pp:7.5,style_observed_as_of:'2026-10-09',
  dated_market_events:[{date:'2026-10-09',
    headline:'红利指数基金大额申购限制',source_url:'https://www.cls.cn/detail/2500609'}],
  // A legacy report must not surface this unredacted field even in UI.
  kol_style_hypotheses:[{author:'军师祭咖啡',published_at:'2026-09-22',
    claim:'齐鲁银行(SH601665)和腾讯的跨公司例子',
    source_url:'https://xueqiu.com/4780688814/410221558'}],
  investment_principles:[{id:'position_and_trading',principle:'高位关注回撤'}],
  stock_20d_pct:4.3,stock_20d_as_of:'2026-10-09',
  stock_ytd_close_high:39.55,stock_ytd_close_high_date:'2026-02-27',
  stock_ytd_latest_close:29.92,stock_ytd_close_as_of:'2026-10-09',
  stock_ytd_close_drawdown_pct:-24.35,
  stock_ytd_close_basis:'unadjusted_daily_close',
};
ctx.renderResult(report);
const newer=nodes.result.innerHTML;
assert.ok(newer.includes('投资者长期回报情景'));
assert.ok(newer.includes('5.12元'));
assert.ok(newer.includes('留存转化'));
assert.ok(newer.includes('公司历史盈利能力'));
assert.ok(newer.includes('35.50元'));
assert.ok(newer.includes('历史估值观点'));
assert.ok(newer.includes('2026-10-10'));
assert.ok(!newer.includes('要求股息率'));
assert.ok(newer.includes('近20个交易日'));
assert.ok(newer.includes('红利指数基金大额申购限制'));
for(const forbidden of ['军师祭咖啡','齐鲁银行','腾讯','601665',
  'xueqiu.com/4780688814','跨公司例子']){
  assert.ok(!newer.includes(forbidden),'Raw KOL contents leaked: '+forbidden);
}
assert.ok(newer.includes('距年内最高收盘价'));
assert.ok(newer.includes('24.35%'));
assert.ok(newer.includes('2026-02-27'));
assert.ok(!newer.includes('本股近20个交易日'));
assert.ok(!newer.includes('1366.03元'));
report.realtime_quote={
  latest_price:775.29,change_pct:null,volume:null,date:'2026-10-09',
  quote_mode:'verified_previous_close',
};
report.summary.stance='bearish';
report.summary.current_odds_stance='bearish';
report.summary.company_quality_stance='bullish';
report.position_risk={
  regime:'bearish_concurrence',
  risk_signals:[
    {family:'valuation'}, {family:'foreign_policy'},
    {family:'cash_conversion'},{family:'market_style'},
  ],
};
ctx.renderResult(report);
const verdict=nodes.result.innerHTML;
assert.ok(verdict.includes('最近可核验收盘价 775.29元'));
assert.ok(verdict.includes('不是盘中实时价'));
assert.ok(!verdict.includes('成交量 null'));
assert.ok(!verdict.includes('涨跌幅 null%'));
assert.ok(!verdict.includes('当前风险共振'));
assert.ok(!verdict.includes('十二个维度等权决定立场'));
report.realtime_quote={latest_price:0};
ctx.renderResult(report);
assert.ok(!nodes.result.innerHTML.includes('最新价 0'));
assert.ok(nodes.result.innerHTML.includes('有效股价暂缺'));

console.log('Dashboard UI checks passed: 4 public sections, 12 unique dimensions, hidden internal reviews and industry no-price gate.');


// Regression for the eight-report illustration: actual values in 10k accounts
// and real yuan axes, directly above bars. The old detail table is gone.
const fullHolderSample=[
  ['2025-02-28',452287,15.35],['2025-03-31',369956,18.12],
  ['2025-06-30',335659,19.50],['2025-09-30',529769,29.44],
  ['2025-12-31',584983,34.47],['2026-02-28',1108965,39.55],
  ['2026-03-31',1252002,32.72],['2026-06-30',1318365,25.14],
].map(([period,holders,price])=>({period,holders,price}));
const fullHolderSvg=ctx.renderHolderChart(fullHolderSample,'ths_f10');
assert.ok(fullHolderSvg.startsWith('<svg'));
for(const count of ['45.23','37.00','33.57','52.98','58.50','110.90','125.20','131.84'])
  assert.ok(fullHolderSvg.includes(count),'missing marked raw count '+count);
assert.ok(fullHolderSvg.includes('F10参考股价 39.55 元'));
assert.ok(fullHolderSvg.includes('F10参考股价 25.14 元'));
assert.ok(fullHolderSvg.includes('2026-02-28'));
assert.ok(fullHolderSvg.includes('2026-06-30'));
assert.ok(fullHolderSvg.includes('股东人数（万户）'));
assert.ok(fullHolderSvg.includes('股价（元）'));
assert.ok(!fullHolderSvg.includes('首期=100'));
assert.ok(!fullHolderSvg.includes('<table'));
assert.ok(!fullHolderSvg.includes('统计截止日'));
assert.ok(!fullHolderSvg.includes('data-tip="undefined'));
