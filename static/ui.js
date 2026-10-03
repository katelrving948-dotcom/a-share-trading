let accountDirty = false;
let confirmedAccount = null;
let importStep = 'upload';
const pageParents = {fundamental:'research', paper:'more', controls:'more', import:'holdings'};
const pageAliases = {push:'plan'};
const pageScroll = {};
let currentPage = 'overview';

function showPage(requested, remember = true) {
  const key = pageAliases[requested] || requested || 'overview';
  const target = document.getElementById('page-' + key);
  if (!target) return showPage('overview', false);
  pageScroll[currentPage] = window.scrollY;
  currentPage = key;
  document.querySelectorAll('.page').forEach(page => page.classList.toggle('active', page === target));
  document.querySelectorAll('nav [data-page]').forEach(button => {
    const active = button.dataset.page === (pageParents[key] || key);
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  document.body.classList.toggle('import-mode', key === 'import');
  const access = document.getElementById('accountAccess');
  document.getElementById(key === 'import' ? 'importAccess' : 'holdingsAccess').append(access);
  if (remember && location.hash !== '#' + key) location.hash = key;
  window.scrollTo(0, pageScroll[key] || 0);
}

function showImportStep(step) {
  importStep = step;
  ['upload','review','saved'].forEach(key => {
    document.getElementById('import-' + key).hidden = step !== key;
    const item = document.querySelector('[data-step="' + key + '"]');
    if (key === step) item.setAttribute('aria-current', 'step');
    else item.removeAttribute('aria-current');
  });
  document.getElementById('importAccess').hidden = step === 'saved';
  window.scrollTo(0, 0);
}

function renderOverview(payload) {
  const account = payload.account || {}, weekly = payload.weekly_plan || {};
  const risk = document.getElementById('overviewRisk');
  risk.classList.toggle('ok', account.can_open_new === true);
  risk.textContent = account.can_open_new ? '账户级闸门通过 · 个股买点仍需复核' : '停止新增风险 · ' + ((account.block_reasons || []).join('；') || '等待账户与行情复核');
  document.getElementById('overviewDate').textContent = '计划 ' + (weekly.generated_at || weekly.plan_id || '待生成');
  document.getElementById('overviewMetrics').innerHTML = metric('本周候选', (weekly.active_count ?? 0) + '只', '最多2主选、1备选') + metric('账户仓位', confirmedAccount ? holdingPosition(confirmedAccount) : '待解锁') + metric('可用资金', confirmedAccount ? num(confirmedAccount.available_cash, 2) : '待解锁', '元') + metric('风险档位', account.risk_profile?.name || '待复核');
  document.getElementById('overviewPlan').textContent = weekly.frozen ? '周度名单已冻结，查看买点与失效条件' : '计划待确认，查看当前研究结果';
}

function holdingPosition(account) {
  if (!(Number(account.equity) > 0)) return '待确认';
  const holdings = account.holdings || [];
  if (holdings.some(item => item.current_price == null)) return '待核对现价';
  const value = holdings.reduce((sum, item) => sum + Number(item.quantity || 0) * Number(item.current_price), 0);
  return num(value / account.equity * 100, 1) + '%';
}

function renderAccountSummary(account) {
  confirmedAccount = account;
  document.getElementById('accountAccess').classList.add('unlocked');
  document.getElementById('holdingMetrics').innerHTML = metric('账户总资产', num(account.equity, 2), '元') + metric('可用资金', num(account.available_cash, 2), '元');
  document.getElementById('holdingSummary').classList.remove('status');
  document.getElementById('holdingSummary').innerHTML = (account.holdings || []).map(item => `<article class="card holding-summary"><h2>${esc(item.name || '名称待确认')}</h2><span class="code">${esc(item.code)} · 已保存持仓</span><div class="holding-values"><div><span>持股 / 可卖</span><strong>${esc(item.quantity)} / ${esc(item.available_quantity)} 股</strong></div><div><span>成本 / 截图现价</span><strong>${num(item.cost_price, 2)} / ${num(item.current_price, 2)}</strong></div></div><details><summary>查看详情</summary><p class="muted">截图时间：${esc(account.source_as_of || '未标注')}<br>自定义止损：${num(item.stop_price, 2)}<br>截图现价用于核对，午间建议使用标注时间的行情。</p></details></article>`).join('') || '<div class="status">已确认空仓。</div>';
  document.getElementById('overviewHoldings').textContent = `已保存 ${(account.holdings || []).length} 只持仓 · 仓位 ${holdingPosition(account)}`;
  const metrics = document.querySelectorAll('#overviewMetrics .metric strong');
  if (metrics.length) {
    metrics[1].textContent = holdingPosition(account);
    metrics[2].textContent = num(account.available_cash, 2);
  }
}

document.querySelectorAll('[data-page]').forEach(button => button.addEventListener('click', () => {
  if (button.dataset.page === 'import' && importStep === 'saved') showImportStep('upload');
  showPage(button.dataset.page);
}));
window.addEventListener('hashchange', () => {
  const key = pageAliases[location.hash.slice(1)] || location.hash.slice(1) || 'overview';
  if (key !== currentPage) showPage(key, false);
});
showPage(location.hash.slice(1), false);

document.querySelectorAll('[data-holding-tab]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-holding-tab]').forEach(tab => {
    tab.classList.toggle('active', tab === button);
    tab.setAttribute('aria-pressed', String(tab === button));
  });
  ['list','advice'].forEach(key => document.getElementById('holdings-' + key).hidden = key !== button.dataset.holdingTab);
}));
document.querySelectorAll('[data-research]').forEach(button => button.addEventListener('click', () => {
  if (button.dataset.research === 'fundamental') return showPage('fundamental');
  document.querySelectorAll('[data-research]').forEach(tab => {
    tab.classList.toggle('active', tab === button);
    tab.setAttribute('aria-pressed', String(tab === button));
  });
  document.querySelectorAll('.research-panel').forEach(panel => panel.hidden = panel.id !== 'research-' + button.dataset.research);
}));
document.getElementById('overviewReload').onclick = () => loadPush();
document.getElementById('accountReverify').onclick = () => {
  document.getElementById('accountAccess').classList.remove('unlocked');
  document.getElementById('accountSecret').focus();
};
['manualEntry','editHoldings'].forEach(id => document.getElementById(id).onclick = () => {
  showPage('import');
  showImportStep('review');
});
document.getElementById('backToUpload').onclick = () => showImportStep('upload');
window.addEventListener('beforeunload', event => {
  if (accountDirty) { event.preventDefault(); event.returnValue = ''; }
});

let previewUrls = [];
document.getElementById('accountScreenshot').addEventListener('change', event => {
  previewUrls.forEach(url => URL.revokeObjectURL(url));
  previewUrls = [];
  const files = [...event.target.files];
  const valid = files.length > 0 && files.length <= 5 && files.every(file => ['image/jpeg','image/png','image/webp'].includes(file.type) && file.size <= 7_000_000);
  document.getElementById('accountExtract').disabled = !valid;
  document.getElementById('uploadStatus').textContent = files.length ? (valid ? `已选择 ${files.length} 张截图，请确认访问密码后识别。` : '请选择最多5张 JPG / PNG / WebP 图片，每张不超过7MB。') : '选择图片后开始识别；结果为待确认草稿。';
  document.getElementById('screenshotPreview').replaceChildren();
  if (!valid) return;
  files.forEach(file => {
    const url = URL.createObjectURL(file);previewUrls.push(url);
    const figure = document.createElement('figure'), img = document.createElement('img'), caption = document.createElement('figcaption');
    img.src = url;img.alt = '待识别截图：' + file.name;caption.textContent = file.name;
    figure.append(img, caption);document.getElementById('screenshotPreview').append(figure);
  });
});

// Reuse the desktop table data; mobile cards expose every field on expansion.
const tableSummaries = {weeklyRows:[1,6,2], pushSectorRows:[1,3,4], pushHotRows:[1,5,2], pushRows:[1,6,5], fundRows:[1,3,12]};
Object.entries(tableSummaries).forEach(([id, indexes]) => {
  const rows = document.getElementById(id), table = rows.closest('table');
  rows.closest('.scroll').classList.add('responsive-table');
  const cards = document.createElement('div');cards.className = 'mobile-records';
  rows.closest('.scroll').after(cards);
  let offset = 0;
  function render() {
    const entries = [...rows.rows], labels = [...table.querySelectorAll('thead th')].map(cell => cell.innerText.replace(/\n/g,' '));
    if (offset >= entries.length) offset = 0;
    cards.replaceChildren();
    entries.slice(offset, offset + 10).forEach(row => {
      if (row.cells.length === 1) {
        const empty = document.createElement('p');empty.className = 'muted';empty.textContent = row.innerText;cards.append(empty);return;
      }
      const cells = [...row.cells];
      const details = document.createElement('details');details.className = 'mobile-record';
      details.innerHTML = `<summary><strong>${cells[indexes[0]].innerHTML}</strong></summary><dl class="record-fields">${cells.map((cell, i) => `<div><dt>${esc(labels[i])}</dt><dd>${cell.innerHTML}</dd></div>`).join('')}</dl>`;
      const summary = document.createElement('div');summary.className = 'record-summary';
      summary.innerHTML = indexes.slice(1).map(i => `<div><span>${esc(labels[i])}</span>${cells[i].innerHTML}</div>`).join('');
      // Summary stays visible when details are closed.
      const record = document.createElement('div');record.className = 'mobile-record-wrap';record.append(details, summary);cards.append(record);
    });
    if (entries.length > 10) {
      const pager = document.createElement('div');pager.className = 'record-pagination';
      const previous = document.createElement('button'), next = document.createElement('button'), label = document.createElement('span');
      previous.className = next.className = 'secondary';previous.textContent = '上一页';next.textContent = '下一页';
      previous.disabled = offset === 0;next.disabled = offset + 10 >= entries.length;
      label.textContent = `${offset + 1}–${Math.min(offset + 10, entries.length)} / ${entries.length}`;
      previous.onclick = () => {offset -= 10;render();cards.scrollIntoView({block:'start'});};
      next.onclick = () => {offset += 10;render();cards.scrollIntoView({block:'start'});};
      pager.append(previous,label,next);cards.append(pager);
    }
  }
  new MutationObserver(() => {offset = 0;render();}).observe(rows, {childList:true});
  render();
});
