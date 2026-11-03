'use strict';
// Exercise the shipped browser script in a small DOM/transport harness. No browser or packages required.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const script = readFileSync(join(__dirname, '..', 'contradiction_map', 'web', 'app.js'), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this._text = ''; this.className = '';
    this.dataset = {}; this.attributes = {}; this.listeners = {}; this.hidden = false; this.disabled = false; this.value = '';
    const classes = () => new Set(this.className.split(/\s+/).filter(Boolean));
    this.classList = {
      contains: name => classes().has(name),
      add: name => { const list = classes(); list.add(name); this.className = [...list].join(' '); },
      remove: name => { const list = classes(); list.delete(name); this.className = [...list].join(' '); },
      toggle: (name, on) => { const list = classes(); const add = on === undefined ? !list.has(name) : on; if (add) list.add(name); else list.delete(name); this.className = [...list].join(' '); },
    };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = [...children]; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  click() { for (const callback of this.listeners.click || []) callback({ target: this, preventDefault() {} }); }
  focus() {}
  scrollIntoView() {}
  querySelectorAll(selector) { return this.children.flatMap(child => [...(selector === 'button' && child.tagName === 'button' ? [child] : []), ...child.querySelectorAll(selector)]); }
  cloneNode(deep) { const clone = new Element(this.tagName); clone.className = this.className; clone._text = this._text; if (deep) clone.children = this.children.map(child => child.cloneNode(true)); return clone; }
}
const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => data });
const emptyResult = () => ({ target: { name: 'Synthetic' }, summary: { files_scanned: 2, deterministic: 0, heuristic: 0, files_skipped: 0 }, findings: [], files: [{ path: 'a.py' }, { path: 'b.py' }], coverage: { rules: [], warnings: [], limitations: [] } });
const settle = async () => { for (let index = 0; index < 25; index++) await Promise.resolve(); };
function harness(jobHandler) {
  const ids = new Map(), timers = new Map(), calls = [];
  let nextTimer = 1, handler = jobHandler;
  const get = id => { if (!ids.has(id)) ids.set(id, new Element()); return ids.get(id); };
  const graphKey = new Element(), filters = ['all', 'deterministic', 'heuristic'].map(filter => { const item = new Element('button'); item.dataset.filter = filter; return item; });
  const document = {
    activeElement: null, getElementById: get,
    querySelector: selector => selector.startsWith('meta') ? { content: 'synthetic-session' } : graphKey,
    querySelectorAll: () => filters,
    createElement: tag => new Element(tag), createElementNS: (_namespace, tag) => new Element(tag),
    createTextNode: text => { const node = new Element('#text'); node.textContent = text; return node; },
  };
  const window = { setTimeout: (fn, delay) => { const id = nextTimer++; timers.set(id, { fn, delay }); return id; }, clearTimeout: id => timers.delete(id) };
  const fetch = async (url, options) => {
    calls.push({ url, options });
    if (url === '/api/info') return response({ version: 'test' });
    if (url === '/api/scan') return response({ job_id: 'job-one' });
    return handler(url, options);
  };
  vm.runInNewContext(script, { document, window, fetch, AbortController, performance: { now: () => 1000 }, matchMedia: () => ({ matches: true }), URL, Blob, console });
  const descendants = root => [root, ...root.children.flatMap(descendants)];
  return { get, calls, timers, setHandler: value => { handler = value; }, find: (root, className) => descendants(get(root)).filter(element => element.classList.contains(className)),
    start: async () => { await settle(); get('demo-button').click(); await settle(); },
    run: async delay => { const timer = [...timers].find(([_id, item]) => item.delay === delay); assert.ok(timer, `expected ${delay}ms timer`); timers.delete(timer[0]); timer[1].fn(); await settle(); },
  };
}
async function disconnect(app) { await app.start(); await app.run(700); await app.run(1400); await app.run(2800); }

test('exact evidence displays source columns and bounded excerpt context without parsing repository HTML', async () => {
  const result = emptyResult();
  result.summary.deterministic = 1;
  result.findings = [{ id: 'synthetic', title: 'Literal mismatch', rule_id: 'NPM001', kind: 'deterministic', severity: 'high', confidence: 1, explanation: 'Synthetic evidence only.', limitations: 'Narrow literal comparison.', evidence: [{ path: 'package.json', line: 1, end_line: 1, column: 903, excerpt_start_column: 840, truncated: true, text: '<img src=x onerror=alert(1)>', role: 'manifest' }], related_paths: ['package.json'] }];
  const app = harness(() => response({ status: 'complete', result }));
  await app.start();
  assert.equal(app.find('evidence-content', 'source-line')[0].textContent, 'L1:903');
  assert.match(app.find('evidence-content', 'source-excerpt-note')[0].textContent, /starts at column 840/);
  assert.match(app.find('evidence-content', 'source-excerpt-note')[0].textContent, /source line is omitted/);
  const source = app.find('evidence-content', 'source-block')[0];
  const excerpt = source.children.find(child => child.tagName === 'pre');
  assert.equal(excerpt.textContent, '<img src=x onerror=alert(1)>');
  assert.equal(excerpt.children.length, 0);
  assert.match(app.get('flat-results').textContent, /package\.json:1:903/);
});

test('one failed poll retries the same job and can recover a completed snapshot', async () => {
  let polls = 0;
  const app = harness(() => { if (polls++ === 0) throw new TypeError('offline'); return response({ status: 'complete', result: emptyResult() }); });
  await app.start();
  assert.match(app.get('status-text').textContent, /Retrying job status/);
  await app.run(700);
  assert.match(app.get('status-text').textContent, /Mapped 2 files/);
  assert.equal(app.get('cancel-button').hidden, true);
  assert.equal(app.calls.filter(call => call.url === '/api/scan').length, 1);
});

test('bounded connection failure stops the spinner, keeps the job cancellable and confirms cancellation', async () => {
  const app = harness(() => { throw new TypeError('offline'); });
  await disconnect(app);
  assert.match(app.get('status-text').textContent, /scan outcome is unknown/);
  assert.equal(app.get('connecting-overlay').hidden, true);
  assert.equal(app.get('cancel-button').hidden, false);
  assert.equal(app.get('cancel-button').disabled, false);
  assert.equal(app.get('refresh-button').disabled, false);
  assert.equal(app.get('scan-button').disabled, true);
  assert.equal(app.timers.size, 0, 'automatic retries must stop');
  app.setHandler(url => response(url.endsWith('/cancel') ? { status: 'running' } : { status: 'cancelled' }));
  app.get('cancel-button').click(); await settle();
  assert.ok(app.calls.some(call => call.url === '/api/jobs/job-one/cancel'));
  assert.match(app.get('status-text').textContent, /^Scan cancelled/);
  assert.equal(app.get('cancel-button').hidden, true);
});

test('Refresh reconnects to retained job instead of starting a duplicate scan', async () => {
  const app = harness(() => { throw new TypeError('offline'); });
  await disconnect(app);
  app.setHandler(() => response({ status: 'complete', result: emptyResult() }));
  app.get('refresh-button').click(); await settle();
  assert.match(app.get('status-text').textContent, /Mapped 2 files/);
  assert.equal(app.calls.filter(call => call.url === '/api/scan').length, 1);
});

for (const [status, expected] of [[404, /job is no longer available.*fresh scan/i], [403, /session is no longer valid.*Reload/]]) {
  test(`HTTP ${status} releases unavailable job and gives an explicit recovery instruction`, async () => {
    const app = harness(() => response({ error: 'Unavailable' }, status));
    await app.start();
    assert.match(app.get('status-text').textContent, expected);
    assert.equal(app.get('cancel-button').hidden, true);
    assert.equal(app.get('scan-button').disabled, false);
    assert.equal(app.timers.size, 0);
  });
}

test('terminal worker error finishes normally without connection retries', async () => {
  const app = harness(() => response({ status: 'error', error: 'Synthetic file cannot be parsed.' }));
  await app.start();
  assert.match(app.get('status-text').textContent, /Synthetic file cannot be parsed/);
  assert.equal(app.get('cancel-button').hidden, true);
  assert.equal(app.timers.size, 0);
});

test('unresponsive status request times out and enters bounded recovery', async () => {
  const app = harness((_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => { const error = new Error('aborted'); error.name = 'AbortError'; reject(error); })));
  await app.start();
  await app.run(10000);
  assert.match(app.get('status-text').textContent, /Retrying job status/);
  assert.ok([...app.timers.values()].some(timer => timer.delay === 700));
  assert.equal(app.get('cancel-button').hidden, false);
});
