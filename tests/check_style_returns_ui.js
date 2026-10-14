/* Market index percentages should be plain aligned numbers, not overlapping SVG bars. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync(process.argv[2],'utf8');
const prefix=src.split("document.getElementById('submitBtn').addEventListener")[0];
const document={
  createElement(){
    let text='';
    return {
      set textContent(v){text=String(v);},
      get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;')
        .replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');},
    };
  },
};
const ctx=vm.createContext({Date,console,document});
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
assert.ok(markup.includes('科技成长回撤，红利相对抗跌'));
assert.ok(markup.includes('这段相对领先主要来自科技成长的明显回撤'));
assert.ok(markup.includes('相对抗跌不等于已经证实资金持续净流入红利'));

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
const reasons=ctx.renderInvestmentStyleStrip({style_context:{
  ...base,
  causal_knowledge_status:'distilled_hypotheses',
  causal_drivers:[
    {
      mechanism:'成长股估值过高时配置资金可能调整到红利资产。',
      conditions:'风险偏好走低且高分红能够由公司现金流覆盖。',
      alternative:'成长订单超预期时资金可能重新偏好成长。',
      verification:'比较ETF份额、机构持股及上市公司经营现金流。',
      evidence_status:'private_research_hypothesis',
    },
    {
      mechanism:'长期国债收益率变化可能提高保险资金对股息资产的相对需求。',
      conditions:'必须有对应日期的利率和险资持仓数据。',
      alternative:'经济周期下行可能导致高股息不再持续。',
      verification:'核对国债收益率、真实股息及保险持股。',
      evidence_status:'private_research_hypothesis',
    },
  ],
}});
assert.ok(reasons.includes('为什么会这样？'));
assert.ok(reasons.includes('成长股估值过高'));
assert.ok(reasons.includes('长期国债收益率变化'));
assert.ok(!reasons.includes('成立条件、反向解释与核验办法'));
assert.ok(!reasons.includes('<details'));
assert.ok(reasons.includes('class="style-cause-item"'));
assert.ok(reasons.includes('并不代表已证实资金从科技板块流向红利板块'));
assert.ok(!reasons.includes('买股票的老木匠'));
assert.ok(!reasons.includes('xueqiu.com'));
// Screenshot regression: the two index numbers are from one observed window,
// and commentary claiming technology buyers / dividend pressure cannot explain it.
const reversed=[
  '政策资金更偏科技，科技类指数基金批量发行带来被动买盘，红利类产品供给偏少；科技估值被推高，传统红利相对承压。',
  '游资和量化让红利股短期波动被放大；基本面没变，资金更愿追科技主题。',
];
const screenshotStyle={
  ...base,style_observed_as_of:'2026-10-13',
  dividend_index_return_pct:2.30,growth_index_mean_return_pct:-12.31,
  dividend_minus_growth_ytd_pp:14.61,
  causal_knowledge_status:'distilled_hypotheses',
  causal_drivers:reversed.map(mechanism=>({mechanism,
    evidence_status:'private_research_hypothesis'})),
};
const guarded=ctx.renderInvestmentStyleStrip({style_context:screenshotStyle});
assert.ok(guarded.includes('>+2.30%</strong>'));
assert.ok(guarded.includes('>-12.31%</strong>'));
assert.ok(guarded.includes('红利上涨，科技成长回撤'));
assert.ok(!guarded.includes('红利相对承压'));
assert.ok(!guarded.includes('资金更愿追科技'));
assert.ok(guarded.includes('历史观点解释的是另一个市场阶段'));
assert.equal((guarded.match(/class="style-return-row"/g)||[]).length,2);
assert.ok(ctx.styleCauseDirectionConflict(reversed[0], screenshotStyle));
assert.ok(!ctx.styleCauseDirectionConflict(
  '成长板块高估值回撤，红利股相对抗跌。',screenshotStyle));

const noKnowledge=ctx.renderInvestmentStyleStrip({style_context:{
  ...base,causal_knowledge_status:'no_matching_distilled_knowledge',
  causal_drivers:[],
}});
assert.ok(noKnowledge.includes('背后的资金流向还缺少可靠证据'));
assert.ok(!noKnowledge.includes('成长股估值过高'));
const injected=ctx.renderInvestmentStyleStrip({style_context:{
  ...base,causal_knowledge_status:'distilled_hypotheses',
  causal_drivers:[{
    mechanism:'<script>bad()</script>',
    conditions:'正常市场环境',verification:'核查数据',
    evidence_status:'private_research_hypothesis'
  }],
}});
assert.ok(!injected.includes('<script>bad()'));

console.log('Market returns UI checks passed: plain percentages, alignment classes, sign colors, source disagreement guard, no SVG.');
