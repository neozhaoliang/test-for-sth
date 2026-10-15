/* Independent industry model WebUI smoke tests with native Node vm. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const file=process.argv[2];
if(!file)throw new Error('Supply extracted web.app inline JS file');
const js=fs.readFileSync(file,'utf8');
const pre=js.split("document.getElementById('submitBtn').addEventListener")[0];
const nodes={};
const doc={
  createElement(){
    let val='';
    return {set textContent(x){val=String(x)},
      get innerHTML(){return val.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}};
  },
  getElementById(id){
    if(!nodes[id])nodes[id]={textContent:'',innerHTML:'',value:'',listeners:{},
      addEventListener(type,fn){this.listeners[type]=fn;}};
    return nodes[id];
  },
};
const ctx=vm.createContext({document:doc,console,Date});
vm.runInContext(pre,ctx,{timeout:7000});
const route={primary:'owner_fcfe',industry:'软件'};
const inputs={
  annual_cash_flow_per_share:[1,1,1,1,1],
  terminal_cash_flow_per_share:1.02,required_return_pct:10,terminal_growth_pct:2,
};
const computed=ctx.industryModelCompute(route,inputs,10,2,0,0);
const expected=Array.from({length:5},(_,i)=>1/1.1**(i+1)).reduce((a,b)=>a+b,0)
  +1.02/(.10-.02)/(1.1**5);
assert.ok(Math.abs(computed.price-expected)<1e-8);
assert.equal(ctx.industryModelCompute(route,inputs,5,2,0,0),null);
const withHigherDiscount=ctx.industryModelCompute(route,inputs,12,2,0,0);
assert.ok(withHigherDiscount.price<computed.price);
assert.ok(ctx.industryModelCompute(route,inputs,10,2,-30,0).price<computed.price);
const rim=ctx.industryModelCompute({primary:'bank_residual_income'},{
  opening_book_per_share:10, annual_roe_pct:[12,12,12],
  annual_payout_pct:[100,100,100],required_return_pct:10,
},10,0,0,0);
const expectedRim=10+Array.from({length:3},(_,i)=>.2/1.1**(i+1)).reduce((a,b)=>a+b,0);
assert.ok(Math.abs(rim.price-expectedRim)<1e-8);
const denied=ctx.renderIndustryValuationLab({
  valuation_model:{route,valuation:{status:'insufficient_evidence',missing:[
    'annual_cash_flow_per_share','terminal_cash_flow_per_share']},interactive_inputs:null,
    evidence_gate:{missing:['annual_cash_flow_per_share']}},
});
assert.ok(denied.includes('当前可用估值数据较少'));
assert.ok(!denied.includes('sector-price'));
assert.ok(denied.includes('完整的股权现金流'));

const model={status:'calculated',route,
  valuation:{status:'calculated',intrinsic_per_share:expected},
  interactive_inputs:inputs,evidence_gate:{status:'verified_input_manifest'},
  macro_context:{
    observations:[{metric:'us_10y_yield_pct',usable:true,value:3.5,as_of:'2026-10-08'}],
    rmb_direction:'depreciating',overseas_revenue_pct:20,
    transmission_paths:[],
  },
};
const html=ctx.renderIndustryValuationLab({valuation_model:model});
assert.ok(html.includes('股权自由现金流 FCFE'));
assert.ok(html.includes('美国10年国债收益率'));
assert.ok(html.includes('2026-10-08'));
assert.ok(html.includes('人民币汇率趋势'));
assert.ok(html.includes('class="macro-inline"'));
assert.ok(!html.includes('近期利率与汇率数据</h4>'));
assert.ok(!html.includes('国际市场与中国利率</h4>'));
const dated=(metric,value,as_of)=>({metric,value,as_of,usable:true});
const minerModel={...model,macro_context:{
  ...model.macro_context,rmb_direction:'appreciating',rmb_is_dated:true,
  observations:[
    dated('fed_target_lower_pct',3.75,'2026-10-15'),
    dated('fed_target_upper_pct',4,'2026-10-15'),
    dated('fed_target_change_180d_pp',.25,'2026-10-15'),
    dated('us_10y_yield_pct',4.6,'2026-10-15'),
    dated('china_lpr_1y_pct',3,'2026-09-20'),
    dated('china_lpr_5y_pct',3.5,'2026-09-20'),
    dated('usdcny_midpoint_cny_per_usd',6.7253,'2026-10-15'),
    dated('usdcny_change_30obs_pct',-.865,'2026-10-15'),
  ],
  miner_transmission:{
    status:'evidence_bounded',
    currency_reading:'人民币升值会压低美元定价的金铜人民币折算价格，但不代表净利润必然下降。',
    rate_reading:'美国实际利率上行可能压制黄金，但金铜还受工业供需影响。',
    financing_reading:'美元债务可能受到有利折算，浮动利率成本需核实。',
    evidence_limit:'公司净汇兑敞口尚需原始披露。',
  },
}};
const compact=ctx.renderIndustryValuationLab({valuation_model:minerModel});
assert.ok(compact.includes('利率汇率一行摘要'));
assert.ok(compact.includes('3.75–4.00%'));
assert.ok(compact.includes('6.7253元'));
assert.ok(compact.includes('-0.86%') || compact.includes('-0.87%'));
assert.ok(compact.includes('中国LPR'));
assert.ok(compact.includes('金铜与汇率'));
assert.ok(compact.includes('美元利率与资源周期'));
assert.ok(compact.includes('公司净汇兑敞口尚需原始披露'));
assert.equal((compact.match(/class="macro-inline"/g)||[]).length,1);
const defaults={
  discount:10,growth:2,stress:0,premium:0,safety:20,
};
Object.entries(defaults).forEach(([k,v])=>{
  nodes['sector-'+k]={value:v,innerHTML:'',textContent:'',listeners:{},
    addEventListener(type,fn){this.listeners[type]=fn;}};
});
ctx.activateIndustryValuationLab({valuation_model:model});
const base=Number.parseFloat(nodes['sector-price'].textContent);
assert.ok(Math.abs(base-expected)<.01);
nodes['sector-discount'].value=12;
nodes['sector-discount'].listeners.input();
const lower=Number.parseFloat(nodes['sector-price'].textContent);
assert.ok(lower<base);
nodes['sector-discount'].value=4;
nodes['sector-discount'].listeners.input();
assert.equal(nodes['sector-price'].textContent,'情景不适用');
nodes['sector-reset'].listeners.click();
assert.equal(nodes['sector-discount'].value,10);
assert.equal(Number.parseFloat(nodes['sector-price'].textContent).toFixed(2),base.toFixed(2));
console.log('Industry valuation UI tests passed: FCFE, RIM, missing sources, macro labels, sliders, guard, reset.');
