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
assert.equal((panel.match(/type="range"/g)||[]).length,5);
assert.ok(panel.includes('恢复初始参数'));
assert.ok(context.renderProfitabilityTrendChart(report.profitability_trend).includes('<svg'));
assert.ok(context.renderDashboardCharts(report).includes('数据概览与维度图谱'));
['roe','payout','efficiency','discount','safety'].forEach(key => {
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
assert.ok(nodes['lab-note'].textContent.includes('不适用'));
const missing={...report,valuation:{}};
assert.equal(context.pickValuationDefaults(missing).nav,null);
assert.ok(context.renderValuationLab(missing).includes('模型假设') === false ||
          context.pickValuationDefaults(missing).payout.source.includes('核验'));
console.log('Dashboard UI checks passed: defaults, five sliders, SVG charts, discount sensitivity, invalid-state guard.');
