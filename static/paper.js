function paperChart(accounts) {
  const series=Object.values(accounts), points=series.flatMap(a=>a.curve);
  if(new Set(points.map(p=>p.date)).size<2)return '<div class="p-chart-empty"><strong>等待形成收益曲线</strong>至少两次模拟检查后展示<br>初始资金已设定，不填充虚构历史收益</div>';
  const times=points.map(p=>Date.parse(p.date)), values=points.map(p=>p.return_pct);
  const start=Math.min(...times),end=Math.max(...times),low=Math.min(0,...values)-0.5,high=Math.max(0,...values)+0.5;
  const x=t=>80+(Date.parse(t)-start)/Math.max(1,end-start)*540,y=v=>220-(v-low)/(high-low)*190;
  const lines=series.map((a,i)=>`<polyline fill="none" stroke="${i?'#f5bd57':'#5b9cff'}" stroke-width="3" ${i?'stroke-dasharray="8 5"':''} points="${a.curve.map(p=>`${x(p.date)},${y(p.return_pct)}`).join(' ')}"/>`).join('');
  return `<svg viewBox="0 0 640 240" role="img" aria-label="两账户累计收益百分比曲线，完整数据见净值明细"><line x1="80" y1="${y(0)}" x2="620" y2="${y(0)}" stroke="#93a7c0" stroke-dasharray="3 4"/><text x="0" y="30" fill="#a8bbd1" font-size="24">${num(high,1)}%</text><text x="0" y="220" fill="#a8bbd1" font-size="24">${num(low,1)}%</text>${lines}</svg><div class="p-chart-dates"><span>${esc(new Date(start).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'}))}</span><span>${esc(new Date(end).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'}))}</span></div>`;
}

const paperMoney = value => value === null || value === undefined || value === '' ? '--' : Number(value).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2});
function paperTimestamp(value) {
  if(!value)return '尚无更新';
  const date=new Date(value);
  return Number.isNaN(date.getTime())?value:date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).replaceAll('/','-');
}
const paperDesktop = window.matchMedia('(min-width:1000px)');
let paperSelectedAccount = 'aggressive';

function selectPaperAccount(key = paperSelectedAccount) {
  paperSelectedAccount = key;
  document.querySelectorAll('#paperAccountSwitch button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.paperMode === key)));
  document.querySelectorAll('#paperAccounts [data-paper-account]').forEach(account => {
    account.hidden = !paperDesktop.matches && account.dataset.paperAccount !== key;
  });
}

function paperAccount(key, account) {
  const small = key === 'aggressive';
  const holdings = account.holdings.map(h => `<article class="p-record"><div class="p-record-head"><strong>${esc(h.name)}</strong><span>${esc(h.code)}</span></div><dl class="p-record-values"><div><dt>持股数量</dt><dd>${esc(h.quantity)} 股</dd></div><div><dt>买入价 / 现价</dt><dd>${num(h.cost_price,2)} / ${num(h.current_price,2)}</dd></div><div><dt>止损价</dt><dd>${num(h.stop_price,2)}</dd></div><div><dt>止盈阶段</dt><dd>${esc(h.profit_stage)}档已兑现</dd></div></dl><p class="p-note">行情时间：${esc(h.quote_as_of)}</p></article>`).join('') || '<div class="p-empty"><strong>当前空仓</strong>等待满足开仓条件</div>';
  const trades = account.trades.slice(-50).reverse().map(t => `<article class="p-record"><div class="p-record-head"><strong>${esc(t.name)} <span>${esc(t.code)}</span></strong><span class="p-badge">${t.side==='buy'?'买入':'卖出'} ${esc(t.quantity)} 股</span></div><p class="p-note">${esc(t.time)}</p><dl class="p-record-values"><div><dt>成交价 / 元</dt><dd>${num(t.price,2)}</dd></div><div><dt>费用 / 元</dt><dd>${paperMoney(t.fee)}</dd></div></dl><p class="p-reason">${esc(t.reason)}</p></article>`).join('') || '<div class="p-empty">暂无模拟成交</div>';
  return `<article class="p-account" data-paper-account="${esc(key)}" aria-label="${esc(account.name)}">
    <div class="p-account-head"><h2>${esc(account.name)}</h2><span class="p-badge">${small?'全仓策略':'分仓策略'}</span></div>
    <p class="p-goal">${small?'高波动实验 · 集中持仓可能产生较大回撤':'控制回撤实验 · 单股与总仓受限'}</p>
    <div class="p-assets"><span>总资产 / 元</span><strong>${paperMoney(account.equity)}</strong><small>初始资金 ${num(account.initial/10000,0)} 万元</small></div>
    <dl class="p-metrics"><div><dt>累计收益</dt><dd>${num(account.return_pct,2)}%</dd></div><div><dt>最大回撤</dt><dd>${num(account.max_drawdown_pct,2)}%</dd></div><div><dt>可用现金 / 元</dt><dd>${paperMoney(account.cash)}</dd></div><div><dt>累计费用 / 元</dt><dd>${paperMoney(account.fees)}</dd></div></dl>
    <section class="p-section"><h3>当前持仓 · ${account.holdings.length}只</h3>${holdings}</section>
    <section class="p-section"><h3>本次决策</h3><div class="p-decision">${esc(account.decisions.join('\n')||'尚未运行；不以初始资金推算收益。')}</div><p class="p-note ${account.valuation_complete===false?'warn':''}">${account.valuation_complete===false?'估值不完整，部分股票沿用旧价':'估值以最近一次模拟检查为准'}</p></section>
    <details class="p-trades"><summary>成交记录 · ${account.trades.length} 笔</summary><p class="p-note">展示最近50笔</p>${trades}</details>
    <details><summary>查看实验目标</summary><p class="p-note">${esc(account.goal)}。目标不代表实际收益。</p></details>
  </article>`;
}

async function loadPaper(){
  const button=document.getElementById('paperReload');button.disabled=true;button.textContent='刷新中…';
  try{
    const state=await api.get('/api/paper');
    document.getElementById('paperStatus').innerHTML=`<strong>${esc(state.status)}</strong> 快照：<time datetime="${esc(state.updated_at||'')}">${esc(paperTimestamp(state.updated_at))}</time><small>计划每个交易日13:05运行；任务排队可能延迟，14:00后不补造交易。快照最多约5分钟同步一次。</small>`;
    document.getElementById('paperRules').textContent=state.assumptions;
    const entries=Object.entries(state.accounts);
    if(!entries.some(([key])=>key===paperSelectedAccount))paperSelectedAccount=entries[0]?.[0]||'';
    document.getElementById('paperAccountSwitch').innerHTML=entries.map(([key,a])=>`<button type="button" data-paper-mode="${esc(key)}" aria-pressed="${key===paperSelectedAccount}" aria-controls="paperAccounts">${num(a.initial/10000,0)} 万元 · ${key==='aggressive'?'全仓':'分仓'}<small>${key==='aggressive'?'高波动实验':'控制回撤'}</small></button>`).join('');
    document.getElementById('paperAccountSwitch').hidden=entries.length<2;
    document.getElementById('paperAccounts').innerHTML=entries.map(([key,a])=>paperAccount(key,a)).join('')||'<div class="p-empty">暂无模拟账户</div>';
    selectPaperAccount();
    document.getElementById('paperChart').innerHTML=paperChart(state.accounts);
    document.getElementById('paperCurveRows').innerHTML=Object.values(state.accounts).flatMap(a=>a.curve.map(p=>`<tr><td>${esc(a.name)}</td><td>${esc(p.date)}</td><td>${num(p.equity,2)}</td><td>${num(p.return_pct,2)}%</td><td>${p.complete===false?'含旧价':'完整'}</td></tr>`)).join('')||'<tr><td colspan="5">尚无净值记录</td></tr>';
  }catch(e){document.getElementById('paperStatus').textContent='模拟仓读取失败：'+e.message+(document.querySelector('#paperAccounts .p-account')?'；保留上次读取的数据。':'')}
  finally{button.disabled=false;button.textContent='刷新'}
}
document.getElementById('paperAccountSwitch').addEventListener('click',event=>{
  const button=event.target.closest('button[data-paper-mode]');
  if(button)selectPaperAccount(button.dataset.paperMode);
});
paperDesktop.addEventListener('change',()=>selectPaperAccount());
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
