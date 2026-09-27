function paperChart(accounts) {
  const series=Object.values(accounts), points=series.flatMap(a=>a.curve);
  if(new Set(points.map(p=>p.date)).size<2)return '<p class="muted">等待至少两次模拟检查后绘制曲线。初始资金已设定，不填充虚构历史收益。</p>';
  const times=points.map(p=>Date.parse(p.date)), values=points.map(p=>p.return_pct);
  const start=Math.min(...times),end=Math.max(...times),low=Math.min(0,...values)-0.5,high=Math.max(0,...values)+0.5;
  const x=t=>60+(Date.parse(t)-start)/Math.max(1,end-start)*780,y=v=>220-(v-low)/(high-low)*190;
  const lines=series.map((a,i)=>`<polyline fill="none" stroke="${i?'#f5bd57':'#5b9cff'}" stroke-width="3" ${i?'stroke-dasharray="8 5"':''} points="${a.curve.map(p=>`${x(p.date)},${y(p.return_pct)}`).join(' ')}"/>`).join('');
  return `<svg viewBox="0 0 900 260" role="img" aria-label="两账户累计收益百分比曲线，完整数据见净值明细"><line x1="60" y1="${y(0)}" x2="840" y2="${y(0)}" stroke="#93a7c0" stroke-dasharray="3 4"/><text x="0" y="30" fill="#93a7c0">${num(high,1)}%</text><text x="0" y="220" fill="#93a7c0">${num(low,1)}%</text>${lines}<text x="60" y="250" fill="#93a7c0">${esc(new Date(start).toLocaleDateString())}</text><text x="740" y="250" fill="#93a7c0">${esc(new Date(end).toLocaleDateString())}</text></svg>`;
}

async function loadPaper(){
  const button=document.getElementById('paperReload');button.disabled=true;
  try{
    const state=await api.get('/api/paper');
    document.getElementById('paperStatus').textContent=`${state.status} · 更新：${state.updated_at||'尚无成交'}\n计划每个交易日13:05运行；任务排队可能延迟，14:00后不补造交易。快照最多约5分钟同步一次。`;
    document.getElementById('paperRules').textContent=state.assumptions;
    document.getElementById('paperAccounts').innerHTML=Object.entries(state.accounts).map(([key,a])=>`<article class="card"><h2>${esc(a.name)} · 本金${num(a.initial/10000,0)}万元</h2><p class="${key==='aggressive'?'warn':'muted'}">${esc(a.goal)}${key==='aggressive'?' · 集中持仓可能产生较大回撤':''}</p><div class="grid g2">${metric('总资产',num(a.equity,2),'元')}${metric('累计收益',num(a.return_pct,2)+'%')}${metric('最大回撤',num(a.max_drawdown_pct,2)+'%')}${metric('可用现金',num(a.cash,2),'累计费用 '+num(a.fees,2)+' 元')}</div><p class="muted">${a.valuation_complete===false?'估值不完整，部分股票沿用旧价':'估值以最近一次模拟检查为准'} · 成交${a.trades.length}笔</p><h3>当前持仓</h3><div class="scroll"><table><thead><tr><th>股票</th><th>股数</th><th>买入价 / 现价</th><th>止损 / 止盈阶段</th><th>行情时间</th></tr></thead><tbody>${a.holdings.map(h=>`<tr><td>${esc(h.code)}<br>${esc(h.name)}</td><td>${h.quantity}</td><td>${num(h.cost_price,2)} / ${num(h.current_price,2)}</td><td>${num(h.stop_price,2)} / ${h.profit_stage}档已兑现</td><td>${esc(h.quote_as_of)}</td></tr>`).join('')||'<tr><td colspan="5">空仓，等待满足开仓条件。</td></tr>'}</tbody></table></div><h3>本次决策</h3><div class="status">${esc(a.decisions.join('\n')||'尚未运行；不以初始资金推算收益。')}</div><h3>成交记录 · 最近50笔</h3><div class="scroll"><table><thead><tr><th>时间 / 股票</th><th>方向 / 股数</th><th>成交价 / 费用</th><th>原因</th></tr></thead><tbody>${a.trades.slice(-50).reverse().map(t=>`<tr><td>${esc(t.time)}<br>${esc(t.code)} ${esc(t.name)}</td><td>${t.side==='buy'?'买入':'卖出'} ${t.quantity}</td><td>${num(t.price,2)} / ${num(t.fee,2)}</td><td style="white-space:normal;min-width:180px">${esc(t.reason)}</td></tr>`).join('')||'<tr><td colspan="4">暂无模拟成交</td></tr>'}</tbody></table></div></article>`).join('');
    document.getElementById('paperChart').innerHTML=paperChart(state.accounts);
    document.getElementById('paperCurveRows').innerHTML=Object.values(state.accounts).flatMap(a=>a.curve.map(p=>`<tr><td>${esc(a.name)}</td><td>${esc(p.date)}</td><td>${num(p.equity,2)}</td><td>${num(p.return_pct,2)}%</td><td>${p.complete===false?'含旧价':'完整'}</td></tr>`)).join('')||'<tr><td colspan="5">尚无净值记录</td></tr>';
  }catch(e){document.getElementById('paperStatus').textContent='模拟仓读取失败：'+e.message}
  finally{button.disabled=false}
}
document.getElementById('paperReload').onclick=loadPaper;
document.getElementById('paperRun').onclick=async()=>{
  const button=document.getElementById('paperRun');button.disabled=true;
  try{
    const secret=document.getElementById('paperSecret').value;
    if(!secret)throw Error('请输入管理密钥');
    const result=await api.post('/api/paper/run',{}, {'Authorization':'Bearer '+secret});
    document.getElementById('paperStatus').textContent=result.message;
  }catch(e){document.getElementById('paperStatus').textContent='无法启动：'+e.message}
  finally{button.disabled=false}
};
loadPaper();
