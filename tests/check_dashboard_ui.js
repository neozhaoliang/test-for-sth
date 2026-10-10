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
assert.equal(html.split('投资结论').length-1,1);
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
assert.ok(html.includes('当前不输出目标价'));
assert.ok(!html.includes('稳态模型隐含合理价格'));
console.log('Dashboard UI checks passed: 4 public sections, 12 unique dimensions, hidden internal reviews and industry no-price gate.');
