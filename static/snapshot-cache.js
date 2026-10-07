// Only public research endpoints may be saved in the browser.
const publicSnapshotPaths = new Set(['/api/push/preview', '/api/fundamental', '/api/research/news']);
const publicSnapshotMemory = new Map();

function validPublicSnapshot(path, data) {
  if (!data || typeof data !== 'object') return false;
  if (path === '/api/push/preview') return Boolean(data.generated_at && data.rules && Array.isArray(data.observations));
  if (path === '/api/fundamental') return Boolean(data.summary?.generated_at && Array.isArray(data.rows));
  return path === '/api/research/news' && data.schema_version === 1 && Boolean(data.news && data.generated_at);
}

function snapshotLabel(data, state = {}) {
  const stamp = data.generated_at || data.summary?.generated_at || '未提供';
  const today = new Date().toLocaleDateString('sv-SE', {timeZone: 'Asia/Shanghai'});
  const historical = String(stamp).slice(0, 10) !== today;
  return `${state.cached ? '本机保存的快照' : '已保存的采集快照'}：${stamp}（北京时间）${historical ? ' · 历史数据，非今日交易建议' : ' · 交易前需复核实时行情'}${state.cached ? ' · 正在检查更新' : ''}${data.snapshot_notice ? '\n' + data.snapshot_notice : ''}`;
}

async function loadPublicSnapshot(path, render, onError) {
  if (!publicSnapshotPaths.has(path)) throw Error('只允许缓存公开研究数据');
  const key = 'a-share-public-snapshot:v1:' + path;
  let cached = publicSnapshotMemory.get(path);
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (!cached && validPublicSnapshot(path, saved)) cached = saved;
  } catch { /* Storage may be unavailable or contain a damaged entry. */ }
  if (cached) render(cached, {cached: true});
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, {signal: controller.signal, cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || response.status);
    if (!validPublicSnapshot(path, data)) throw Error('尚无完整的采集快照');
    const stamp = p => p.generated_at || p.summary?.generated_at || '';
    if (cached && stamp(data) < stamp(cached)) throw Error('服务器快照较旧，等待后台同步');
    render(data, {cached: false});
    publicSnapshotMemory.set(path, data);
    try { localStorage.setItem(key, JSON.stringify(data)); } catch { /* Quota or privacy mode: keep showing the server result. */ }
  } catch (error) {
    onError(error.name === 'AbortError' ? Error('服务暂未响应，请稍后刷新') : error, Boolean(cached));
  } finally { clearTimeout(timeout); }
}
