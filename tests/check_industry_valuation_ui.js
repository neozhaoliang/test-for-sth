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
assert.ok(denied.includes('当前不输出目标价'));
assert.ok(!denied.includes('sector-price'));
assert.ok(denied.includes('未来逐年股权自由现金流'));

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
assert.ok(html.includes('us_10y_yield_pct'));
assert.ok(html.includes('2026-10-08'));
assert.ok(html.includes('美元兑人民币'));
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
