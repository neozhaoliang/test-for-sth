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

// Dynamic cross-industry style: the LLM may feature pharma/metals instead of
// a fixed dividend-vs-tech pair, but numeric values come only from board data.
const sectorRows=[
  ['有色金属',12.3],['化学制药',8.9],['医疗器械',5.2],
  ['电力行业',1.2],['银行',.4],['软件开发',-7.4],['半导体',-9.5],
].map(([name,d20_pct])=>({name,d20_pct,latest_date:'2026-10-14'}));
const broadStyle={
  ...base,
  sector_rotation:{
    status:'observed', as_of:'2026-10-14', coverage:7,universe_size:37,
    sectors:sectorRows, leaders:['有色金属','化学制药'],
    laggards:['半导体','软件开发'],up_count:5,down_count:2,
  },
  rotation_reading:{
    status:'llm_selected', featured_sectors:['化学制药','医疗器械','有色金属','半导体'],
    summary:'医药板块相对更强，有色和半导体明显分化，本轮不能只讨论红利与科技。',
    hypotheses:['相关行业盈利预期可能正在改变，仍需核实上市公司业绩变化。'],
  },
};
const broadMarkup=ctx.renderInvestmentStyleStrip({style_context:broadStyle});
assert.ok(broadMarkup.includes('化学制药') && broadMarkup.includes('医疗器械'));
assert.ok(broadMarkup.includes('>+8.90%</strong>'));
assert.ok(broadMarkup.includes('>+12.30%</strong>'));
assert.ok(broadMarkup.includes('>-9.50%</strong>'));
assert.ok(broadMarkup.includes('医药板块相对更强'));
assert.ok(broadMarkup.includes('7个行业中'));
assert.ok(!broadMarkup.includes('科创50／创业板平均'));
assert.ok(!broadMarkup.includes('红利相对成长'));
assert.equal((broadMarkup.match(/class="style-return-row"/g)||[]).length,4);
const rotated={...broadStyle,rotation_reading:{
  ...broadStyle.rotation_reading, featured_sectors:['银行','电力行业','软件开发','半导体'],
}};
const rotatedMarkup=ctx.renderInvestmentStyleStrip({style_context:rotated});
assert.ok(rotatedMarkup.includes('>+0.40%</strong>'));
assert.ok(!rotatedMarkup.includes('>+8.90%</strong>'));
// A failed sector provider cannot erase previously verified index returns.
const unavailable=ctx.renderInvestmentStyleStrip({style_context:{
  ...base, sector_rotation:{status:'insufficient_data',coverage:0,sectors:[],benchmarks:[]},
}});
assert.ok(unavailable.includes('上证红利'));
assert.ok(unavailable.includes('科创50／创业板平均'));
assert.ok(unavailable.includes('已核验的红利与成长指数对照') ||
          unavailable.includes('当前市场在交易什么？'));
const injectedSector=ctx.renderInvestmentStyleStrip({style_context:{
  ...broadStyle, rotation_reading:{...broadStyle.rotation_reading,
    summary:'<img src=x onerror=alert(1)>'},
}});
assert.ok(!injectedSector.includes('<img src=x onerror=alert(1)>'));



// Index-only case: never confuse missing sector data with missing all market
// data, and do not force the fixed red/growth pairing when 5 indices survive.
const indexOnly=ctx.renderInvestmentStyleStrip({style_context:{
  status:'observed',style_regime:'unknown',
  sector_rotation:{
    status:'insufficient_data', coverage:0, sectors:[], benchmarks:[
      {name:'上证指数',d20_pct:-2.15,latest_date:'2026-10-14'},
      {name:'沪深300',d20_pct:-1.72,latest_date:'2026-10-14'},
      {name:'上证红利',d20_pct:1.33,latest_date:'2026-10-14'},
      {name:'科创50',d20_pct:-14.05,latest_date:'2026-10-14'},
      {name:'创业板指',d20_pct:-13.87,latest_date:'2026-10-14'},
    ],
  },
}});
for(const field of ['上证红利','沪深300','上证指数','科创50','创业板指'])
  assert.ok(indexOnly.includes(field),'missing available index '+field);
assert.ok(indexOnly.includes('>+1.33%</strong>'));
assert.ok(indexOnly.includes('>-14.05%</strong>'));
assert.ok(indexOnly.includes('0个行业板块和5个指数'));
assert.ok(!indexOnly.includes('多行业同期行情覆盖不足'));
assert.ok(!indexOnly.includes('不能可靠判断市场的主导风格'));
assert.ok(!indexOnly.includes('已经发生全市场风格迁移'));

// Partial sector case: all observed industries remain visible; 6 is the
// bar for an industry-market claim, not a hard threshold for reporting facts.
const partial=ctx.renderInvestmentStyleStrip({style_context:{
  status:'observed',sector_rotation:{
    status:'partial',coverage:2,as_of:'2026-10-14',
    sectors:[
      {name:'有色金属',d20_pct:11.0,latest_date:'2026-10-14'},
      {name:'化学制药',d20_pct:-2.4,latest_date:'2026-10-14'},
    ],
    benchmarks:[
      {name:'沪深300',d20_pct:-1.7,latest_date:'2026-10-14'},
      {name:'科创50',d20_pct:-10.5,latest_date:'2026-10-14'},
    ],
  },
}});
for(const name of ['有色金属','化学制药','沪深300','科创50'])
  assert.ok(partial.includes(name));
assert.ok(partial.includes('>+11.00%</strong>'));
assert.ok(partial.includes('>-2.40%</strong>'));
assert.ok(partial.includes('2个行业板块和2个指数'));
assert.ok(partial.includes('不能据此断言整个A股市场的行业主线已切换'));

// If every source is missing, report that *actual* narrow failure.
const zero=ctx.renderInvestmentStyleStrip({style_context:{
  status:'insufficient_data',sector_rotation:{status:'insufficient_data',sectors:[],benchmarks:[]},
}});
assert.ok(zero.includes('行业和指数数据源本次均未返回'));

console.log('Market returns UI checks passed: plain percentages, alignment classes, sign colors, source disagreement guard, no SVG.');
