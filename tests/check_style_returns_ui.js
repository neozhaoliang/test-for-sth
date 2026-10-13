/* Market index percentages should be plain aligned numbers, not overlapping SVG bars. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync(process.argv[2],'utf8');
const prefix=src.split("document.getElementById('submitBtn').addEventListener")[0];
const ctx=vm.createContext({Date,console,document:{}});
vm.runInContext(prefix,ctx,{timeout:8000});

const base={
  status:'observed',
  style_regime:'dividend_leading',
  style_window:'20 trading days',
  style_observed_as_of:'2026-10-12',
  dividend_minus_growth_ytd_pp:9.71,
  dividend_index_return_pct:.03,
  growth_index_mean_return_pct:-5.69,
  dated_market_events:[],
  stock_20d_pct:null,
};
let markup=ctx.renderInvestmentStyleStrip({style_context:base});
assert.ok(markup.includes('上证红利'));
assert.ok(markup.includes('科创50／创业板平均'));
assert.ok(markup.includes('>+0.03%</strong>'));
assert.ok(markup.includes('>-5.69%</strong>'));
assert.ok(markup.includes('style-return-up'));
assert.ok(markup.includes('style-return-down'));
assert.ok(markup.includes('style-return-row'));
assert.ok(markup.includes('涨跌差9.71个百分点'));
assert.ok(markup.includes('不是相同的比较口径'));
assert.ok(!markup.includes('<svg'));
assert.ok(!markup.includes('<rect'));
assert.ok(!markup.includes('可对照上图'));

let same=ctx.renderInvestmentStyleStrip({style_context:{
  ...base,dividend_minus_growth_ytd_pp:5.72,
}});
assert.ok(!same.includes('不是相同的比较口径'));
let missing=ctx.renderInvestmentStyleStrip({style_context:{
  ...base,dividend_index_return_pct:null,
}});
assert.ok(!missing.includes('style-return-list'));
assert.ok(!missing.includes('>+0.00%</strong>'));
assert.equal(ctx.renderInvestmentStyleStrip({style_context:{status:'insufficient_data'}}),'');
console.log('Market returns UI checks passed: plain percentages, alignment classes, sign colors, source disagreement guard, no SVG.');
