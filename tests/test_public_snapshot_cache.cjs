const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

const path = '/api/push/preview';
const key = 'a-share-public-snapshot:v1:' + path;
const old = {generated_at: '2026-09-30 12:05:00', observations: [], rotation_boards: [], rules: {}};
const fresh = {...old, generated_at: '2026-10-07 12:05:00'};
function setup(fetch, saved = old, storageFails = false) {
  const storage = new Map(saved ? [[key, JSON.stringify(saved)]] : []);
  const context = vm.createContext({fetch, AbortController, setTimeout, clearTimeout, localStorage: {
    getItem: k => storage.get(k) || null,
    setItem: (k, v) => { if (storageFails) throw Error('quota'); storage.set(k, v); }
  }});
  vm.runInContext(fs.readFileSync('static/snapshot-cache.js', 'utf8'), context);
  return {context, storage};
}

test('saved data renders before a slow network response, then refreshes and persists', async () => {
  let release;
  const {context, storage} = setup(() => new Promise(resolve => {release = resolve;}));
  const renders = [];
  const promise = context.loadPublicSnapshot(path, (p, s) => renders.push([p.generated_at, s.cached]), e => {throw e;});
  assert.deepEqual(renders, [[old.generated_at, true]]);
  release({ok: true, json: async () => fresh});
  await promise;
  assert.deepEqual(renders, [[old.generated_at, true], [fresh.generated_at, false]]);
  assert.equal(JSON.parse(storage.get(key)).generated_at, fresh.generated_at);
});

test('503 preserves the stored data and reports whether a fallback is available', async () => {
  for (const saved of [old, null]) {
    const {context, storage} = setup(async () => ({ok: false, json: async () => ({error: '等待有效采集'})}), saved);
    let error;
    await context.loadPublicSnapshot(path, () => {}, (e, cached) => {error = [e.message, cached];});
    assert.deepEqual(error, ['等待有效采集', Boolean(saved)]);
    assert.equal(storage.has(key), Boolean(saved));
  }
});

test('damaged cache is skipped; storage quota failure still displays successful data', async () => {
  const {context, storage} = setup(async () => ({ok: true, json: async () => fresh}), {generated_at: 'broken'}, true);
  const renders = [];
  await context.loadPublicSnapshot(path, (p, s) => renders.push([p.generated_at, s.cached]), e => {throw e;});
  assert.deepEqual(renders, [[fresh.generated_at, false]]);
  assert.equal(JSON.parse(storage.get(key)).generated_at, 'broken');
});

test('private endpoints never request or persist through the public cache', async () => {
  const {context} = setup(() => {throw Error('must not fetch');});
  await assert.rejects(context.loadPublicSnapshot('/api/account', () => {}, () => {}), /只允许缓存公开研究数据/);
});

test('server restart with an older snapshot cannot overwrite newer browser data', async () => {
  const {context, storage} = setup(async () => ({ok: true, json: async () => old}), fresh);
  const renders = [];
  let error;
  await context.loadPublicSnapshot(path, p => renders.push(p.generated_at), e => {error = e.message;});
  assert.deepEqual(renders, [fresh.generated_at]);
  assert.match(error, /服务器快照较旧/);
  assert.equal(JSON.parse(storage.get(key)).generated_at, fresh.generated_at);
});

test('memory retains the successful snapshot when browser storage is unavailable', async () => {
  let offline = false;
  const {context} = setup(async () => {if (offline) throw Error('offline'); return {ok: true, json: async () => fresh};}, null, true);
  await context.loadPublicSnapshot(path, () => {}, e => {throw e;});
  offline = true;
  let rendered, hasFallback;
  await context.loadPublicSnapshot(path, p => {rendered = p.generated_at;}, (_, cached) => {hasFallback = cached;});
  assert.equal(rendered, fresh.generated_at);
  assert.equal(hasFallback, true);
});

test('historical cache labels include its date and non-current trading boundary', () => {
  const {context} = setup(() => {});
  const label = context.snapshotLabel(old, {cached: true});
  assert.match(label, /2026-09-30/);
  assert.match(label, /非今日交易建议/);
  assert.match(label, /正在检查更新/);
});

test('research errors clear loading rows; push task status cannot block research data', async () => {
  const {context} = setup(async () => ({ok: false, json: async () => ({error: '等待午间采集'})}), null);
  const elements = new Map();
  context.document = {getElementById: id => {
    if (!elements.has(id)) elements.set(id, {textContent: '读取中…', innerHTML: ''});
    return elements.get(id);
  }};
  context.api = {get: async () => {throw Error('status unavailable');}};
  context.esc = s => s;
  const source = fs.readFileSync('templates/index.html', 'utf8');
  vm.runInContext(source.slice(source.indexOf('function renderPush'), source.indexOf("document.getElementById('pushReload').onclick")), context);
  await context.loadPush();
  assert.match(elements.get('capitalStatus').textContent, /等待午间采集/);
  assert.match(elements.get('pushSectorRows').innerHTML, /等待午间采集/);
  assert.doesNotMatch(elements.get('researchSnapshotStatus').textContent, /读取中/);
});
