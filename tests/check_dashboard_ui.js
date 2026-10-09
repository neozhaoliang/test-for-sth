/* Dependency-free DOM-stubbed tests for the live valuation UI.
   Run against the actual JavaScript extracted from web/app.py. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const path = process.argv[2];
if (!path) throw new Error('Pass extracted Web UI JavaScript path');
const js = fs.readFileSync(path, 'utf8');
const prefix = js.split("document.getElementById('submitBtn').addEventListener")[0];
assert.ok(prefix.includes('function renderValuationLab('));
const nodes = {};
const mockDocument = {
  createElement() {
    let text = '';
    return {
      set textContent(value) { text = String(value); },
      get innerHTML() {
        return text.replaceAll('&','&amp;').replaceAll('<','&lt;')
          .replaceAll('>','&gt;').replaceAll('"','&quot;')
          .replaceAll("'",'&#39;');
      },
    };
  },
  getElementById(id) {
    if (!nodes[id]) nodes[id] = {
      textContent:'',innerHTML:'',value:'',listeners:{},
      addEventListener(type,handler){this.listeners[type]=handler;},
    };
    return nodes[id];
  },
};
const context = vm.createContext({document:mockDocument,console,Date});
vm.runInContext(prefix, context, {timeout:4000});
const report = {
  as_of:'2026-10-09',
  valuation:{nav_per_share:13.27,pb:1.02,roe_pct:7.02,valuation_as_of:'2026-06-30'},
  profitability_trend:{periods:[
    {period:'2024-12-31',roe_pct:15,gross_margin_pct:30,net_margin_pct:9},
    {period:'2025-12-31',roe_pct:18.88,gross_margin_pct:31,net_margin_pct:10},
    {period:'2026-06-30',roe_pct:7.02,gross_margin_pct:28,net_margin_pct:8},
  ]},
  valuation_model:{assumptions:{payout_pct:51.98,required_return_pct:11}},
  realtime_quote:{latest_price:13.35},
  summary:{dimension_scores:[]},
  dividend_chart:[],fundamentals:{facts:{}},
};
const defaults=context.pickValuationDefaults(report);
assert.equal(defaults.roe.value,18.88);
assert.equal(defaults.payout.value,51.98);
assert.equal(defaults.discount.value,11);
const panel=context.renderValuationLab(report);
assert.equal((panel.match(/type="range"/g)||[]).length,7);
assert.equal(defaults.normalized.value,12);
assert.equal(defaults.terminalGrowth.value,2);
assert.ok(panel.includes('恢复初始参数'));
assert.ok(context.renderProfitabilityTrendChart(report.profitability_trend).includes('<svg'));
assert.ok(context.renderDashboardCharts(report).includes('数据概览与维度图谱'));
const lowPriceChip = context.renderChipPositionChart({
  status:'contextualized',position_52w_pct:12.5,
  holder_change_pct:20,holder_period:'2026-09-30',
  holder_context:'low_price_more_holders_neutral',
});
assert.ok(lowPriceChip.includes('低位增户：不自动扣分'));
const highPriceChip = context.renderChipPositionChart({
  status:'contextualized',position_52w_pct:89,
  holder_change_pct:20,holder_period:'2026-09-30',
  holder_context:'high_price_more_holders_watch',
});
assert.ok(highPriceChip.includes('高位分散：待核验'));
const capitalChart=context.renderDividendChart([{
  year:'2025',dividend_per_10:4,buyback_per_10:1,total_per_10:5,yield_pct:4,
}]);
assert.ok(capitalChart.includes('回购不是现金分红'));
assert.ok(capitalChart.includes('非股息率'));
['roe','normalized','payout','efficiency','discount','terminalGrowth','safety'].forEach(key => {
  nodes['lab-'+key] = {
    value:defaults[key].value,listeners:{},
    addEventListener(type,callback){this.listeners[type]=callback;},
  };
});
context.activateValuationLab(report);
const first=parseFloat(nodes['lab-fair-price'].textContent);
assert.ok(first>0, 'Model should price a company when NAV is available');
nodes['lab-discount'].value=13;
nodes['lab-discount'].listeners.input();
const cheaper=parseFloat(nodes['lab-fair-price'].textContent);
assert.ok(cheaper<first, 'Higher hurdle should reduce valuation');
nodes['lab-payout'].value=0;
nodes['lab-payout'].listeners.input();
assert.equal(nodes['lab-fair-price'].textContent,'暂无法定价');
assert.ok(nodes['lab-note'].textContent.includes('不能自动假设终值分红'));
const missing={...report,valuation:{},valuation_model:null,profitability_trend:null};
const fallback=context.pickValuationDefaults(missing);
assert.equal(fallback.nav,null);
assert.equal(fallback.roe.value,12);
assert.equal(fallback.payout.value,50);
assert.equal(fallback.discount.value,10);
assert.ok(fallback.roe.source.includes('模型假设'));
nodes['lab-reset'].listeners.click();
assert.equal(Number(nodes['lab-discount'].value),11);
assert.equal(Number(nodes['lab-payout'].value),51.98);
// Regression: old perpetuity model showed 1366 yuan and 179x PB.
const pathology=context.calculateTwoStageValuation(27.9,28.7,50,10,12,2,7.60);
assert.ok(pathology,'Two-stage model should have a defined finite scenario result');
assert.ok(Math.abs(pathology.price - 8.5888428)<.001);
assert.ok(Math.abs(pathology.pb - 1.13011)<.0001);
const lessPayout=context.calculateTwoStageValuation(27.9,20,50,10,12,2,7.60);
assert.ok(lessPayout.price<pathology.price, 'Reducing payout should not cause absurd value blowup');
assert.ok(pathology.terminalShare>70, 'High terminal exposure should be surfaced');
assert.equal(context.calculateTwoStageValuation(27.9,0,50,10,12,2,7.6),null);
// One detailed twelve-dimension report. Summary thesis must not repeat it.
const keys=['management','fundamentals','rd','chip_flow','price_position',
  'cycle_position','policy_geopolitics','retail_sentiment','shareholder_returns',
  'growth_elasticity','a_share_structure','risk_quality'];
const analyses=Object.fromEntries(keys.map(key=>[key,'UNIQUE_DETAIL_'+key.toUpperCase()+'_END']));
const detailHtml=context.renderDimensionAnalysisSection({
  dimension_analyses:analyses,
  thesis_summary:'DUPLICATE_OVERVIEW_TOKEN',
});
assert.ok(detailHtml.includes('十二维度详细分析'));
assert.ok(!detailHtml.includes('<details'));
assert.ok(!detailHtml.includes('DUPLICATE_OVERVIEW_TOKEN'));
keys.forEach(key=>assert.equal(detailHtml.split('UNIQUE_DETAIL_'+key.toUpperCase()+'_END').length-1,1));
const singleReport={...report,stock_name:'示例股票',stock_code:'601717',
  research_mode:'live',candidates:[],
  summary:{...report.summary,thesis_summary:'DUPLICATE_OVERVIEW_TOKEN',
    dimension_analyses:analyses},
};
context.renderResult(singleReport);
const rendered=nodes['result'].innerHTML;
assert.equal(rendered.split('十二维度详细分析').length-1,1);
assert.ok(!rendered.includes('DUPLICATE_OVERVIEW_TOKEN'));
assert.ok(!rendered.includes('逐项阅读十二维度详细分析'));
keys.forEach(key=>assert.equal(rendered.split('UNIQUE_DETAIL_'+key.toUpperCase()+'_END').length-1,1));
console.log('Dashboard UI checks passed: finite-stage DDM regression, 7 sliders, charts and single detailed analysis.');
