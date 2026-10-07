let researchNews = {};
let newsFilter = 'all';
let newsLimit = 10;

function safeNewsLink(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : '';
  } catch { return ''; }
}

function newsArticle(item) {
  const link = safeNewsLink(item.url);
  return `<article class="news-item"><div class="news-meta"><time>${esc(item.time || '时间未提供')}</time><span>${esc(item.source || '来源未提供')}</span>${(item.categories || []).map(key => `<span class="news-tag">${key === 'geopolitics' ? '地缘局势' : '国内金融'}</span>`).join('')}</div><h3>${esc(item.title)}</h3>${item.summary ? `<details><summary>查看摘要</summary><p>${esc(item.summary)}</p></details>` : ''}${link ? `<a class="news-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer">查看原文<span class="sr-only">：${esc(item.title)}</span></a>` : '<span class="muted">原文链接暂不可用</span>'}</article>`;
}

function newsFreshness(section) {
  const stamp = section.generated_at;
  if (!stamp) return '尚无成功采集记录';
  const age = Date.now() - new Date(stamp.replace(' ', 'T') + '+08:00').getTime();
  return `采集于 ${stamp}（北京时间）${age > 2 * 3600000 ? ' · 已超过2小时未更新' : ''}${section.error ? ' · ' + section.error : ''}`;
}

function renderResearchNews() {
  const sections = researchNews.news || {};
  const keys = newsFilter === 'all' ? ['geopolitics', 'domestic'] : [newsFilter];
  const labels = {geopolitics: '地缘局势', domestic: '国内金融'};
  document.getElementById('newsStatus').textContent = keys.map(key => `${labels[key]}：${newsFreshness(sections[key] || {})}`).join('\n');
  const articles = new Map();
  keys.forEach(key => (sections[key]?.items || []).forEach(item => {
    const identity = item.url || item.title;
    if (!articles.has(identity)) articles.set(identity, {...item, categories: []});
    articles.get(identity).categories.push(key);
  }));
  const items = [...articles.values()].sort((a, b) => (b.time || '').localeCompare(a.time || ''));
  document.getElementById('newsList').innerHTML = items.length ? items.slice(0, newsLimit).map(newsArticle).join('') : '<p class="muted">暂无该分类快讯，请等待下一次采集。</p>';
  document.getElementById('newsMore').hidden = items.length <= newsLimit;
}

function renderUSMarket() {
  const market = researchNews.us_market || {};
  document.getElementById('usMarketStatus').textContent = market.session_date ? `行情日期 ${market.session_date}（美股交易日） · 采集于 ${market.generated_at}（北京时间） · 来源：${market.source}${researchNews.us_market_error ? '\n' + researchNews.us_market_error : ''}` : researchNews.us_market_error || '尚无成功的美股收盘快照，请等待定时采集。';
  document.getElementById('usMarketQuotes').innerHTML = (market.markets || []).map(item => {
    const change = item.change_pct == null ? NaN : Number(item.change_pct);
    const valid = Number.isFinite(change);
    const label = !valid ? '涨跌待更新' : change > 0 ? '上涨' : change < 0 ? '下跌' : '持平';
    return `<article class="us-index"><h3>${esc(item.name)}</h3><strong>${num(item.price, 2)}</strong><div class="${valid ? change > 0 ? 'bad' : change < 0 ? 'ok' : 'muted' : 'muted'}">${label} ${valid ? (change > 0 ? '+' : '') + num(change, 2) + '%' : '--'}</div><p class="muted">行情源时间 ${esc(item.as_of || '未提供')}</p></article>`;
  }).join('');
  const section = researchNews.news?.us || {};
  document.getElementById('usNewsStatus').textContent = newsFreshness(section);
  document.getElementById('usNewsList').innerHTML = (section.items || []).slice(0, 10).map(newsArticle).join('') || '<p class="muted">暂无美股市场快讯。</p>';
}

async function loadResearchNews() {
  const button = document.getElementById('newsReload');
  button.disabled = true;
  try {
    await loadPublicSnapshot('/api/research/news', (data, state) => {
      researchNews = data;
      renderResearchNews();
      renderUSMarket();
      if (state.cached) ['newsStatus', 'usMarketStatus', 'usNewsStatus'].forEach(id => {
        document.getElementById(id).textContent += '\n本机保存的快照 · 正在检查更新';
      });
    }, (error, cached) => {
      ['newsStatus', 'usMarketStatus', 'usNewsStatus'].forEach(id => {
        const el = document.getElementById(id);
        el.textContent = (cached ? el.textContent.replace(' · 正在检查更新', '') + '\n检查更新失败，保留本机快照：' : '快照读取失败：') + error.message;
      });
    });
  } finally { button.disabled = false; }
}

document.querySelectorAll('[data-news-filter]').forEach(button => button.addEventListener('click', () => {
  newsFilter = button.dataset.newsFilter;
  newsLimit = 10;
  document.querySelectorAll('[data-news-filter]').forEach(tab => {
    tab.classList.toggle('active', tab === button);
    tab.setAttribute('aria-pressed', String(tab === button));
  });
  renderResearchNews();
}));
document.getElementById('newsMore').onclick = () => { newsLimit += 10; renderResearchNews(); };
document.getElementById('newsReload').onclick = loadResearchNews;
document.querySelectorAll('[data-research="news"], [data-research="external"]').forEach(button => button.addEventListener('click', loadResearchNews));
loadResearchNews();
