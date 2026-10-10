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
assert.equal(html.split('多维度分析').length-1,1);
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
assert.ok(!nodes.result.innerHTML.includes('LEGACY_ESSAY_TOKEN'));
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
  dividend_scenarios:[{name:'中性',annual_cash_dividend_per_share:.25,
    assumed_cash_dividend_yield_pct:5.5,reference_price:4.55}],
  historical_pb_anchor:{window_years:5,median_pb:1.15,
    median_pb_reference_price:6.31},
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
};
ctx.renderResult(report);
const newer=nodes.result.innerHTML;
assert.ok(newer.includes('投资者参考估值'));
assert.ok(newer.includes('4.55元'));
assert.ok(newer.includes('近20个交易日'));
assert.ok(newer.includes('红利指数基金大额申购限制'));
for(const forbidden of ['军师祭咖啡','齐鲁银行','腾讯','601665',
  'xueqiu.com/4780688814','跨公司例子']){
  assert.ok(!newer.includes(forbidden),'Raw KOL contents leaked: '+forbidden);
}
assert.ok(newer.includes('本股近20个交易日'));
assert.ok(!newer.includes('1366.03元'));
console.log('Dashboard UI checks passed: 4 public sections, 12 unique dimensions, hidden internal reviews and industry no-price gate.');
