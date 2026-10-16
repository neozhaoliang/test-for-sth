/* ROE / discount-rate valuation laboratory. Prices use fundamentals only.
   Equivalent equations are implemented in analysis/roe_discount_valuation.py. */
function roeDiscountCompute(book, roePct, growthPct, discountPct, horizon=10) {
  const b=Number(book), r=Number(roePct)/100,
        g=Number(growthPct)/100, k=Number(discountPct)/100;
  if (![b,r,g,k].every(Number.isFinite) || b<=0 ||
      r<=0 || r>1 || k<=0 || k>.4 || g<0 || g>=r || g>=k-.005)
    return null;
  const payout=1-g/r;
  let bv=b, pvResidual=0, pvDividend=0;
  for (let y=1;y<=horizon;y++) {
    const yearRoe=r+(k-r)*(y-1)/(horizon-1);
    const earnings=bv*yearRoe, dividend=earnings*payout;
    pvResidual+=(earnings-k*bv)/Math.pow(1+k,y);
    pvDividend+=dividend/Math.pow(1+k,y);
    bv+=earnings-dividend;
    if(!Number.isFinite(bv)||bv<=0) return null;
  }
  const finite=b+pvResidual, steady=b*(r-g)/(k-g);
  const terminal=bv/Math.pow(1+k,horizon);
  if(![finite,steady,terminal].every(Number.isFinite) ||
     Math.abs(finite-(pvDividend+terminal))>1e-7*Math.max(1,Math.abs(finite)))
    return null;
  return {finite,steady,payoutPct:payout*100,
    terminalPct:finite>0?100*terminal/finite:null};
}

function renderRoeDiscountLab(model) {
  const m=((model||{}).investor_reference||{}).roe_discount_lab;
  if(!m) return '';
  const book=Number(m.book_per_share);
  let html='<div class="evidence-block" id="roe-discount-lab" style="margin-top:16px;">'+
    '<h4>ROE × 增长 × 贴现率 · 独立经营估值</h4>'+
    '<p class="section-caption">估值不使用历史股价或PE/PB拟合。'+
    '主模型假设超额ROE在10年内逐步回归权益资本成本，期末PB=1；'+
    '另以无限期稳定经营作为对照。以下价格是条件情景，并非精准买点。</p>';
  if(m.status==='missing_book'||!Number.isFinite(book)||book<=0)
    return html+'<p>缺少可靠每股净资产，不能生成价格。请先核实财报。</p></div>';
  const d=m.defaults||{}, a=m.roe_anchor||{};
  const initialRoe=d.roe_pct==null?10:Math.max(.5,Math.min(60,Number(d.roe_pct)));
  const initialG=d.growth_pct==null?0:Math.max(0,Math.min(15,Number(d.growth_pct)));
  const slide=(key,label,min,max,step,val)=>'<div class="slider-row roe-discount-slider-row">'+
    '<label for="roe-'+key+'">'+escapeHtml(label)+'</label>'+
    '<input type="range" id="roe-'+key+'" min="'+min+'" max="'+max+
    '" step="'+step+'" value="'+val+'">'+
    '<output for="roe-'+key+'" id="roe-'+key+'-value">'+
    Number(val).toFixed(2)+'%</output></div>';
  html+='<p class="section-caption"><b>期初每股净资产</b> '+book.toFixed(2)+'元；'+
    (a.status==='historical_observed'?
      '最近完整年度ROE：'+escapeHtml((a.years||[]).join('、'))+
      '年，中位数'+escapeHtml(String(a.median_pct))+
      '%；四分位'+escapeHtml(String(a.low_pct))+'%—'+
      escapeHtml(String(a.high_pct))+'%。':
      '不足三个完整年度ROE，初始10%仅为可修改的示例。')+'</p>';
  html+='<div class="lab-layout roe-discount-layout"><div class="roe-discount-controls">'+
    slide('roe','正常化ROE',.5,60,.25,initialRoe)+
    slide('growth','长期权益增长率',0,15,.25,initialG)+
    slide('discount','投资者要求收益率／贴现率',4,25,.25,10)+
    '<div class="lab-actions"><button type="button" id="roe-reset">恢复初始假设</button></div>'+
    '</div><div class="lab-result roe-discount-result" aria-live="polite" aria-atomic="false">'+
    '<h4>主模型条件价格</h4>'+
    '<div class="lab-price" id="roe-price">—</div>'+
    '<div class="lab-stat-grid">'+
    '<div><span>经营参数压力价格区间</span><b id="roe-band">—</b></div>'+
    '<div><span>无限期稳态对照价格</span><b id="roe-steady">—</b></div>'+
    '<div><span>隐含派息率</span><b id="roe-payout">—</b></div>'+
    '<div><span>10年末净资产终值占比</span><b id="roe-terminal">—</b></div></div>'+
    '<div class="lab-foot" id="roe-note">—</div></div></div>';
  const paid=m.payout_evidence||{};
  if(paid.status==='verified') html+='<p class="section-caption">'+
    '同财年已实施股息/EPS的历史派息率约'+
    escapeHtml(String(paid.payout_pct))+'%，与上述模型隐含派息率独立对照；'+
    '差异越大，增长假设越需审查。</p>';
  else html+='<p class="section-caption">真实派息率尚未通过至少三个完整财年的配对验证；'+
    '模型隐含派息率并不是实际分红预测。</p>';
  return html+'<p class="section-caption">区间来自ROE±20%、增长率±1个百分点、'+
    '贴现率±1.5个百分点的27组组合中合法结果；不代表统计置信区间或市场估值上下限。</p></div>';
}

function activateRoeDiscountLab(model) {
  const m=((model||{}).investor_reference||{}).roe_discount_lab;
  const doc=typeof document==='undefined'?null:document;
  if(!doc||!m||!m.book_per_share||!doc.getElementById('roe-discount-lab'))return;
  const keys=['roe','growth','discount'];
  const controls=Object.fromEntries(keys.map(x=>[x,doc.getElementById('roe-'+x)]));
  if(keys.some(x=>!controls[x]))return;
  const initial=Object.fromEntries(keys.map(x=>[x,Number(controls[x].value)]));
  const display=(id,text)=>{const el=doc.getElementById('roe-'+id);if(el)el.textContent=text;};
  const validNumber=n=>Number.isFinite(n)&&n>0;
  function refresh() {
    const x=Object.fromEntries(keys.map(k=>[k,Number(controls[k].value)]));
    keys.forEach(k=>display(k+'-value',x[k].toFixed(2)+'%'));
    const main=roeDiscountCompute(m.book_per_share,x.roe,x.growth,x.discount);
    if(!main) {
      ['price','band','steady','payout','terminal'].forEach(k=>display(k,'—'));
      display('note','参数不满足约束：增长率须≥0、低于ROE且至少比贴现率低0.5个百分点。');
      return;
    }
    const estimates=[];
    for(const r of [Math.max(.5,x.roe*.8),x.roe,Math.min(60,x.roe*1.2)])
      for(const g of [Math.max(0,x.growth-1),x.growth,Math.min(15,x.growth+1)])
        for(const k of [Math.max(4,x.discount-1.5),x.discount,Math.min(25,x.discount+1.5)]) {
          const ans=roeDiscountCompute(m.book_per_share,r,g,k);
          if(ans&&validNumber(ans.finite))estimates.push(ans.finite);
        }
    const lo=Math.min(...estimates),hi=Math.max(...estimates);
    display('price',main.finite.toFixed(2)+'元/股');
    display('band',lo.toFixed(2)+'—'+hi.toFixed(2)+'元');
    display('steady',main.steady.toFixed(2)+'元/股');
    display('payout',main.payoutPct.toFixed(1)+'%');
    display('terminal',main.terminalPct==null?'—':main.terminalPct.toFixed(1)+'%');
    const notes=['主模型假设10年后超额ROE为0、PB=1。'];
    if(m.defaults.roe_pct==null)notes.push('ROE初值为示例，不是实测正常化ROE。');
    if(m.defaults.growth_pct==null)notes.push('增长率初值为零增长压力情景。');
    notes.push('贴现率为用户可修改的要求收益率，不由股价回测得到。');
    if(estimates.length<27)notes.push('部分极端组合违反增长约束而被排除。');
    if(main.terminalPct>70)notes.push('期末净资产占比较高，结果对终值敏感。');
    display('note',notes.join(' '));
  }
  keys.forEach(k=>controls[k].addEventListener('input',refresh));
  const reset=doc.getElementById('roe-reset');
  if(reset)reset.addEventListener('click',()=>{
    keys.forEach(k=>controls[k].value=initial[k]);refresh();
  });
  refresh();
}
