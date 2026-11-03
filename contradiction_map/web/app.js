'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const token = document.querySelector('meta[name="session-token"]').content;
  const state = { result: null, selected: null, filter: 'all', job: null, busy: false, lastRequest: null, lastClientNote: null, lastInputValue: '', lastFilePicker: 'files-input', reading: false, readCancelled: false, disconnected: false, pollTimer: null, epoch: 0, start: 0, limits: { max_files: 500, max_total_bytes: 8 * 1024 * 1024, max_file_bytes: 512 * 1024 } };
  const ns = 'http://www.w3.org/2000/svg';
  const el = (tag, className, content) => { const element = document.createElement(tag); if (className) element.className = className; if (content !== undefined) element.textContent = content; return element; };
  const svg = (tag, attrs, parent) => { const element = document.createElementNS(ns, tag); Object.entries(attrs || {}).forEach(([key, value]) => element.setAttribute(key, String(value))); if (parent) parent.append(element); return element; };
  const asText = value => Array.isArray(value) ? value.join(' ') : String(value || '');
  const shortPath = path => { const parts = String(path).split('/'); return parts.length > 2 ? '\u2026/' + parts.slice(-2).join('/') : String(path); };
  const confidence = finding => Math.round(Number(finding.confidence || 0) * 100) + '% rule weight';
  const kindName = finding => finding.kind === 'deterministic' ? 'Deterministic' : 'Heuristic candidate';
  const pathsFor = finding => [...new Set([...(finding.related_paths || []), ...(finding.evidence || []).map(item => item.path)])];
  class ApiError extends Error {
    constructor(message, status = 0) { super(message); this.name = 'ApiError'; this.status = status; }
  }
  async function api(path, body) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers: { 'X-Contradiction-Token': token, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) }, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', signal: controller.signal });
      let data;
      try { data = await response.json(); } catch (error) { if (error.name === 'AbortError') throw error; throw new ApiError('The local companion returned an unreadable response.', response.status); }
      if (!response.ok) throw new ApiError(asText(data.error || data.message) || `Local service error (${response.status}).`, response.status);
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new ApiError('The local companion did not respond within 10 seconds.');
      throw error;
    } finally { window.clearTimeout(timeout); }
  }
  function setStatus(message, kind = '', timing = '') {
    $('status-text').textContent = message;
    $('status-dot').className = 'status-dot' + (kind ? ' ' + kind : '');
    $('scan-timing').textContent = timing || (state.result ? 'LOCAL ANALYSIS \u00b7 NO MODEL CALLS' : 'NO API KEY REQUIRED');
  }
  function setBusy(busy) {
    state.busy = busy;
    state.disconnected = false;
    ['scan-button', 'folder-button', 'files-button', 'demo-button', 'empty-demo-button', 'target-input'].forEach(id => { const item = $(id); if (item) item.disabled = busy; });
    $('scan-button').hidden = busy;
    $('cancel-button').hidden = !busy;
    $('cancel-button').disabled = false;
    $('connecting-overlay').hidden = !busy;
    $('refresh-button').disabled = busy || !state.lastRequest;
    $('graph').classList.toggle('muted-graph', busy);
  }
  async function startScan(request, clientNote = null) {
    if (state.busy) return;
    const epoch = ++state.epoch;
    state.start = performance.now();
    setBusy(true);
    $('cancel-button').disabled = true;
    setStatus('Reading a bounded snapshot and comparing explicit claims\u2026', 'busy', 'MAPPING IN PROGRESS');
    try {
      const response = await api('/api/scan', request);
      if (epoch !== state.epoch) return;
      if (!response.job_id) throw new Error('The local companion did not return a scan job.');
      state.job = response.job_id;
      $('cancel-button').disabled = false;
      state.lastRequest = request;
      state.lastInputValue = $('target-input').value.trim();
      state.lastClientNote = clientNote;
      pollJob(response.job_id, epoch);
    } catch (error) {
      if (epoch !== state.epoch) return;
      setBusy(false);
      setStatus(error.message, 'error', 'SCAN DID NOT COMPLETE');
      if (!state.result) showError(error.message);
    }
  }
  function finishJobError(message) {
    state.job = null;
    if (state.pollTimer !== null) window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
    setBusy(false);
    setStatus(message + (state.result ? ' The previous completed snapshot is still displayed.' : ''), 'error', 'SCAN DID NOT COMPLETE');
    if (!state.result) showError(message);
  }
  function handleMissingJob(error) {
    if (error.status === 404) { finishJobError('This job is no longer available. Start a fresh scan; the previous worker outcome is unknown.'); return true; }
    if (error.status === 403) { finishJobError('The local session is no longer valid. Reload this page to reconnect to the companion.'); return true; }
    return false;
  }
  function showDisconnected(message) {
    state.disconnected = true;
    $('connecting-overlay').hidden = true;
    $('graph').classList.remove('muted-graph');
    $('cancel-button').disabled = false;
    $('refresh-button').disabled = false;
    setStatus('Connection lost; the scan outcome is unknown. Refresh reconnects to this job. Cancel scan retries cancellation. ' + message, 'error', 'RECONNECT REQUIRED');
  }
  async function pollJob(id, epoch, failures = 0) {
    if (epoch !== state.epoch || id !== state.job) return;
    state.pollTimer = null;
    let job;
    try { job = await api('/api/jobs/' + encodeURIComponent(id)); }
    catch (error) {
      if (epoch !== state.epoch || id !== state.job) return;
      if (handleMissingJob(error)) return;
      if (failures < 3) {
        setStatus(`Connection interrupted. Retrying job status (${failures + 1}/3)... Cancellation remains available.`, 'busy', 'RECONNECTING');
        state.pollTimer = window.setTimeout(() => pollJob(id, epoch, failures + 1), 700 * (2 ** failures));
      } else showDisconnected(error.message);
      return;
    }
    if (epoch !== state.epoch || id !== state.job) return;
    if (job.status === 'complete' || job.status === 'completed') {
      if (!job.result || !job.result.summary || !Array.isArray(job.result.findings)) { finishJobError('This scan completed without a readable result. Start a fresh scan.'); return; }
      state.job = null;
      setBusy(false);
      state.result = job.result;
      if (state.lastClientNote) { state.result.coverage = state.result.coverage || {}; state.result.coverage.warnings = state.result.coverage.warnings || []; state.result.coverage.warnings.push(state.lastClientNote); }
      state.selected = job.result.findings?.[0]?.id || null;
      renderResult();
      const elapsed = ((performance.now() - state.start) / 1000).toFixed(1);
      const count = state.result.findings?.length || 0;
      setStatus(count ? `Mapped ${state.result.summary.files_scanned} files \u00b7 ${count} finding${count === 1 ? '' : 's'} to review` : `Mapped ${state.result.summary.files_scanned} files \u00b7 no conflicts detected by supported rules`, '', elapsed + 's \u00b7 SNAPSHOT COMPLETE');
    } else if (job.status === 'cancelled') {
      state.job = null;
      setBusy(false);
      setStatus('Scan cancelled. ' + (state.result ? 'The previous completed snapshot is still displayed.' : 'Choose a repository when you are ready.'), '', 'CANCELLED');
    } else if (job.status === 'error' || job.status === 'failed') {
      finishJobError(asText(job.error) || 'The scan could not complete.');
    } else {
      state.pollTimer = window.setTimeout(() => pollJob(id, epoch), 350);
    }
  }
  function showError(message) {
    const wrap = el('div', 'empty-result');
    wrap.append(el('strong', '', 'Could not map this repository.'), el('p', '', message), el('p', '', 'Check the folder or public GitHub URL, then try again.'));
    $('findings-list').replaceChildren(wrap);
  }
  function tag(finding) { return el('span', 'kind-tag' + (finding.kind === 'heuristic' ? ' heuristic' : ''), kindName(finding)); }
  function renderResult() {
    const result = state.result;
    const findings = result.findings || [];
    $('map-title').textContent = result.target?.name || 'Repository snapshot';
    $('stat-files').textContent = result.summary.files_scanned;
    $('stat-deterministic').textContent = result.summary.deterministic;
    $('stat-heuristic').textContent = result.summary.heuristic;
    $('finding-count').textContent = findings.length;
    $('graph-empty').hidden = true;
    $('download-button').disabled = false;
    renderFindings();
    renderGraph();
    renderEvidence();
    renderCoverage();
  }
  function filteredFindings() { return (state.result?.findings || []).filter(finding => state.filter === 'all' || finding.kind === state.filter); }
  function renderFindings() {
    const previouslyFocused = document.activeElement;
    const focusId = previouslyFocused && previouslyFocused.dataset ? previouslyFocused.dataset.findingId : null;
    const focusFlat = previouslyFocused && previouslyFocused.classList.contains('flat-finding');
    const list = $('findings-list');
    const flat = $('flat-results');
    list.replaceChildren(); flat.replaceChildren();
    const findings = filteredFindings();
    if (!findings.length) {
      const noFindings = !(state.result?.findings || []).length;
      const content = el('div', 'empty-result');
      content.append(el('span', 'clean-check', noFindings ? '\u2713' : '\u2301'), el('strong', '', noFindings ? 'No conflicts detected.' : 'No findings in this view.'), el('p', '', noFindings ? 'The supported rules found no contradictions in the included files. This is a focused scan, not a proof of consistency.' : 'Try another filter to review the remaining findings.'));
      list.append(content); flat.append(content.cloneNode(true));
    }
    findings.forEach(finding => {
      const selected = finding.id === state.selected;
      const button = el('button', 'finding' + (selected ? ' selected' : ''));
      button.type = 'button';
      button.dataset.findingId = finding.id;
      button.setAttribute('aria-pressed', String(selected));
      const top = el('div', 'finding-top');
      const position = (state.result.findings.indexOf(finding) + 1).toString().padStart(2, '0');
      top.append(el('span', 'finding-index', position), el('span', 'severity ' + finding.severity, finding.severity));
      const meta = el('div', 'finding-meta');
      meta.append(tag(finding), el('span', 'confidence', confidence(finding)));
      button.append(top, el('strong', 'finding-title', finding.title), el('span', 'finding-paths', pathsFor(finding).map(shortPath).join(' \u2194 ')), meta);
      button.addEventListener('click', () => selectFinding(finding.id));
      list.append(button);
      const flatButton = el('button', 'flat-finding' + (selected ? ' selected' : ''));
      flatButton.type = 'button';
      flatButton.dataset.findingId = finding.id;
      flatButton.setAttribute('aria-pressed', String(selected));
      flatButton.append(tag(finding), el('strong', '', finding.title), el('p', '', finding.explanation), el('small', '', (finding.evidence || []).map(item => item.path + ':' + item.line + (Number.isInteger(item.column) && item.column > 0 ? ':' + item.column : '')).join(' \u2194 ')));
      flatButton.addEventListener('click', () => { selectFinding(finding.id); $('evidence-panel').scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' }); });
      flat.append(flatButton);
    });
    if (focusId) { const container = focusFlat ? flat : list; const focusTarget = [...container.querySelectorAll('button')].find(item => item.dataset.findingId === focusId); if (focusTarget) focusTarget.focus({ preventScroll: true }); }
  }
  function selectFinding(id) {
    state.selected = id;
    renderFindings(); renderGraph(); renderEvidence();
  }
  function renderEvidence() {
    const finding = state.result?.findings?.find(item => item.id === state.selected);
    const content = $('evidence-content');
    content.replaceChildren();
    if (!finding) {
      $('evidence-title').textContent = 'A clear view of the current scope.';
      content.append(el('p', 'evidence-description', 'No supported rule detected a conflict. Review Scope & limitations below for skipped files, supported comparisons, and blind spots.'));
      return;
    }
    $('evidence-title').textContent = finding.title;
    const heading = el('div', 'evidence-heading-row');
    heading.append(tag(finding), el('span', 'rule-id', finding.rule_id + ' \u00b7 ' + confidence(finding)));
    content.append(heading, el('p', 'evidence-description', finding.explanation));
    const grid = el('div', 'evidence-grid');
    (finding.evidence || []).forEach(item => {
      const source = el('article', 'source-block');
      const header = el('div', 'source-heading');
      const end = item.end_line && item.end_line !== item.line ? '\u2013' + item.end_line : '';
      const column = Number.isInteger(item.column) && item.column > 0 ? ':' + item.column : '';
      header.append(el('span', 'source-path', item.path), el('span', 'source-line', 'L' + item.line + column + end));
      source.append(header, el('pre', '', item.text));
      if (item.truncated) { const startColumn = Number.isInteger(item.excerpt_start_column) && item.excerpt_start_column > 0 ? item.excerpt_start_column : 1; source.append(el('p', 'source-excerpt-note', 'Bounded excerpt: starts at column ' + startColumn + '. Part of this source line is omitted; use the source location above for the full context.')); }
      if (item.role) source.append(el('div', 'source-role', item.role));
      grid.append(source);
    });
    content.append(grid);
    if (finding.limitations) content.append(el('p', 'evidence-limits', 'Interpretation limit \u00b7 ' + asText(finding.limitations)));
  }
  function renderCoverage() {
    const coverage = state.result?.coverage || {};
    const content = $('coverage-content');
    content.replaceChildren();
    const rules = coverage.rules || [];
    $('coverage-summary').textContent = rules.length + ' supported comparisons \u00b7 ' + (state.result.summary.files_skipped || 0) + ' files skipped' + (coverage.truncated ? ' \u00b7 scan bounded' : '');
    $('footer-rules').textContent = rules.length + ' focused rules \u00b7 No AI inference';
    content.append(el('p', '', 'A snapshot of included text files, using the rules below. No findings is not proof of consistency. Results are not a security assessment. Confidence is a fixed rule weight, not a measured probability. The graph displays at most 18 files, prioritizing selected evidence; flat view lists all findings.'));
    rules.forEach(rule => { const p = el('p', 'coverage-rule'); p.append(el('code', '', rule.id || rule.rule_id || ''), document.createTextNode(' \u00b7 ' + (rule.name || '') + ' \u00b7 ' + (rule.kind || '') + '. ' + asText(rule.limitations))); content.append(p); });
    const limits = Array.isArray(coverage.limitations) ? coverage.limitations : [coverage.limitations].filter(Boolean);
    limits.forEach(limit => content.append(el('p', '', asText(limit))));
    (coverage.warnings || []).forEach(warning => content.append(el('p', 'warning-note', asText(warning))));
    if (coverage.truncated) content.append(el('p', 'warning-note', 'Input bounds were reached. This report covers a partial snapshot; inspect warnings before drawing conclusions.'));
  }
  function renderGraph() {
    const background = $('graph-background');
    const edges = $('graph-edges');
    const nodes = $('graph-nodes');
    background.replaceChildren(); edges.replaceChildren(); nodes.replaceChildren();
    const cx = 410, cy = 229;
    // Plotter-like geometry gives spatial context without suggesting analyzed evidence.
    svg('path', { d: 'M70 110V65H115 M705 65H750V110 M750 355V400H705 M115 400H70V355', fill: 'none', stroke: '#211e35', 'stroke-width': '1', opacity: '.35' }, background);
    svg('path', { d: 'M120 229H700 M410 70V390', fill: 'none', stroke: '#211e35', 'stroke-width': '.8', 'stroke-dasharray': '3 7', opacity: '.2' }, background);
    svg('path', { d: 'M410 63 713 229 410 395 107 229Z M410 108 630 229 410 350 190 229Z', fill: 'none', stroke: '#9d96d8', 'stroke-width': '1', opacity: '.55' }, background);
    for (let index = 0; index < 9; index++) svg('path', { d: `M${80 + index * 6} 365l18 -18`, stroke: '#211e35', 'stroke-width': '1', opacity: '.3' }, background);
    const coordinate = svg('text', { x: 73, y: 54, fill: '#72677d', 'font-family': 'Courier New, monospace', 'font-size': '7', 'letter-spacing': '1' }, background); coordinate.textContent = 'CLAIM SPACE / LOCAL SNAPSHOT';
    const result = state.result;
    const selected = result?.findings?.find(finding => finding.id === state.selected);
    const selectedPaths = selected ? pathsFor(selected) : [];
    const findingPaths = [...new Set((result?.findings || []).flatMap(pathsFor))];
    const allPaths = [...new Set([...selectedPaths, ...findingPaths, ...(result?.files || []).map(file => file.path)])].slice(0, 18);
    if (!result) return;
    const totalFiles = (result.files || []).length;
    document.querySelector('.graph-key-right').textContent = totalFiles > allPaths.length ? `SHOWING ${allPaths.length} OF ${totalFiles} FILES` : 'SELECT A FINDING TO TRACE IT';
    const count = Math.max(allPaths.length, 1);
    const points = allPaths.map((path, index) => {
      const theta = -Math.PI / 2 + index * Math.PI * 2 / count;
      const radius = index % 3 === 0 ? 167 : index % 3 === 1 ? 131 : 192;
      return { path, x: Math.round(cx + Math.cos(theta) * radius * 1.59), y: Math.round(cy + Math.sin(theta) * radius * .82), selected: selectedPaths.includes(path) };
    });
    points.forEach(point => {
      const elbowX = Math.round(cx + (point.x - cx) * .45);
      svg('path', { d: `M${cx},${cy} H${elbowX} L${point.x},${point.y}`, fill: 'none', stroke: '#211e35', 'stroke-width': point.selected ? '1.7' : '1', 'stroke-opacity': point.selected ? '.75' : '.25', class: 'graph-edge' }, edges);
    });
    const marked = points.filter(point => point.selected);
    for (let index = 0; index < marked.length - 1; index++) {
      const left = marked[index], right = marked[index + 1];
      svg('path', { d: `M${left.x},${left.y} L${right.x},${left.y} L${right.x},${right.y}`, fill: 'none', stroke: '#ed5635', 'stroke-width': '2.4', 'stroke-dasharray': '5 4', class: 'graph-edge' }, edges);
    }
    const center = svg('g', { transform: `translate(${cx},${cy})` }, nodes);
    svg('rect', { x: -21, y: -21, width: 48, height: 48, fill: '#211e35' }, center);
    svg('rect', { x: -27, y: -27, width: 48, height: 48, fill: '#d6f34d', stroke: '#211e35', 'stroke-width': '2' }, center);
    svg('path', { d: 'M-14 -11H-3V0H8V11H-3 M-14 11V-11 M8 -11L-3 0', fill: 'none', stroke: '#211e35', 'stroke-width': '2.3', 'stroke-linejoin': 'miter' }, center);
    const centerText = svg('text', { x: 0, y: 48, fill: '#211e35', 'font-size': '10', 'font-weight': 'bold', 'text-anchor': 'middle', 'font-family': 'Courier New, monospace' }, center); centerText.textContent = (result.target?.name || 'repository').split(/[\\/]/).pop().slice(0, 32);
    const centerSub = svg('text', { x: 0, y: 63, fill: '#72677d', 'font-size': '7', 'text-anchor': 'middle', 'font-family': 'Courier New, monospace', 'letter-spacing': '1' }, center); centerSub.textContent = 'EVIDENCE NEXUS';
    points.forEach(point => {
      const related = (result.findings || []).find(finding => pathsFor(finding).includes(point.path));
      const group = svg('g', { class: 'graph-node', transform: `translate(${point.x},${point.y})`, ...(related ? { tabindex: '0', role: 'button', 'aria-label': point.path + ', inspect related finding', 'aria-pressed': String(point.selected) } : { 'aria-label': point.path }) }, nodes);
      const floating = svg('g', { class: 'node-float' }, group);
      const size = point.selected ? 27 : 22;
      svg('rect', { x: -size / 2 + 4, y: -size / 2 + 4, width: size, height: size, fill: '#211e35', opacity: point.selected ? '1' : '.3' }, floating);
      svg('rect', { x: -size / 2, y: -size / 2, width: size, height: size, fill: point.selected ? '#ff6c45' : '#9d96d8', stroke: '#211e35', 'stroke-width': point.selected ? '1.7' : '1.2', class: 'node-ring' }, floating);
      svg('path', { d: 'M-4 -5H1L5 -1V5H-4ZM1 -5V-1H5', fill: 'none', stroke: '#211e35', 'stroke-width': '1' }, floating);
      const text = svg('text', { y: 30, x: 0, fill: '#211e35', 'font-size': '9', 'font-weight': point.selected ? 'bold' : 'normal', 'text-anchor': 'middle' }, floating);
      const name = shortPath(point.path); text.textContent = name.length > 25 ? '\u2026' + name.slice(-23) : name;
      const title = svg('title', {}, group); title.textContent = point.path;
      if (related) { group.addEventListener('click', () => selectFinding(related.id)); group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectFinding(related.id); } }); }
    });
    const graphDescription = result.findings?.length ? `Evidence network showing ${points.length} files. ${selected ? selected.title + '. Highlighted source files: ' + selectedPaths.join(', ') : 'Select a finding to highlight its source files.'} Exact evidence is available below or in Flat view.` : `Repository network showing ${points.length} files. No conflicts were detected by supported rules.`;
    $('graph').setAttribute('aria-label', graphDescription);
  }
  const allowedExtensions = new Set(['py', 'toml', 'json', 'md', 'markdown', 'txt', 'yml', 'yaml', 'js', 'mjs', 'cjs', 'ts', 'tsx', 'jsx', 'html', 'css', 'ini', 'cfg', 'conf', 'properties', 'example', 'rst', 'ps1', 'sh', 'bat', 'cmd', 'xml', 'csv', 'sql', 'go', 'rs', 'java', 'c', 'h', 'cpp', 'hpp']);
  const excludedDirs = new Set(['.git', '.hg', '.svn', '.venv', 'venv', 'env', 'node_modules', 'vendor', 'vendors', 'dist', 'build', '__pycache__', '.next', '.nuxt', 'coverage', '.cache', '.tox', '.mypy_cache', '.pytest_cache', 'site-packages', 'target', '.idea', '.vscode', 'secrets', 'credentials', '.aws', '.ssh', '.gnupg']);
  function includable(path) {
    const parts = path.replaceAll('\\', '/').split('/');
    if (parts.some(part => excludedDirs.has(part.toLowerCase()) || part === '..')) return false;
    const name = parts.at(-1).toLowerCase();
    if ((name === '.env' || name.startsWith('.env.')) && !name.endsWith('.example') && !name.endsWith('.sample') && !name.endsWith('.template')) return false;
    if (/^(id_rsa|id_ed25519|credentials|secrets?)(\.|$)/.test(name) || /\.(pem|key|p12|pfx|jks|sqlite|db)$/.test(name)) return false;
    return allowedExtensions.has(name.split('.').at(-1)) || ['dockerfile', 'makefile', '.gitignore', '.env.example', '.env.sample', '.env.template', 'license'].includes(name);
  }
  async function readFiles(fileList, pickerId) {
    if (state.busy || !fileList.length) return;
    state.reading = true; state.readCancelled = false;
    state.lastFilePicker = pickerId;
    setBusy(true);
    setStatus('Reading the text files you selected in the browser...', 'busy', 'LOCAL FILE SELECTION');
    const files = [];
    let bytes = 0, skipped = 0;
    const entries = [...fileList].sort((a, b) => (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name));
    try {
      for (const file of entries) {
        if (state.readCancelled) break;
        const originalPath = file.webkitRelativePath || file.name;
        const path = file.webkitRelativePath ? file.webkitRelativePath.split('/').slice(1).join('/') : file.name;
        if (!includable(originalPath) || !includable(path) || file.size > state.limits.max_file_bytes) { skipped++; continue; }
        if (files.length >= state.limits.max_files || bytes + file.size > state.limits.max_total_bytes) throw new Error('Selection exceeds the safe input limit. Choose fewer files or use the local folder picker for a bounded scan.');
        const content = await file.text();
        if (content.includes('\u0000')) { skipped++; continue; }
        files.push({ path, content }); bytes += file.size;
      }
      state.reading = false; setBusy(false);
      if (state.readCancelled) { setStatus('File selection cancelled. Your previous snapshot is unchanged.', '', 'CANCELLED'); return; }
      if (!files.length) { setStatus('No supported text files were selected. Secret, binary, generated and vendor files are excluded.', 'error'); return; }
      $('target-input').value = `${entries[0]?.webkitRelativePath?.split('/')[0] || 'Selected files'} \u00b7 ${files.length} selected text files`;
      await startScan({ mode: 'files', files, target: entries[0]?.webkitRelativePath?.split('/')[0] || 'Selected files' }, 'Browser-selected file snapshot. Use Refresh to reselect files and include disk changes. ' + skipped + ' input(s) were excluded in the browser before submission; the server skipped count covers submitted inputs only.');
    } catch (error) { state.reading = false; setBusy(false); setStatus('Could not read the selected files: ' + error.message, 'error'); }
  }
  $('scan-form').addEventListener('submit', event => {
    event.preventDefault();
    const target = $('target-input').value.trim();
    if (!target) return;
    if (state.lastRequest && target === state.lastInputValue) { if (state.lastRequest.mode === 'files') $(state.lastFilePicker).click(); else startScan(state.lastRequest, state.lastClientNote); return; }
    startScan({ mode: /^https?:\/\//i.test(target) ? 'url' : 'path', target });
  });
  ['demo-button', 'empty-demo-button'].forEach(id => $(id).addEventListener('click', () => { $('target-input').value = 'Synthetic demo repository'; startScan({ mode: 'demo' }); }));
  $('folder-button').addEventListener('click', () => { setStatus('Choose a folder in the browser picker. Only selected, supported text files are read.'); $('folder-input').click(); });
  $('files-button').addEventListener('click', () => $('files-input').click());
  ['folder-input', 'files-input'].forEach(id => $(id).addEventListener('change', event => { readFiles(event.target.files, id).catch(error => setStatus('Could not read the selected files: ' + error.message, 'error')); event.target.value = ''; }));
  $('cancel-button').addEventListener('click', async () => {
    if (state.reading) { state.readCancelled = true; $('cancel-button').disabled = true; setStatus('Cancelling file selection...', 'busy'); return; }
    if (!state.job) return;
    const id = state.job, epoch = state.epoch;
    $('cancel-button').disabled = true;
    setStatus('Cancelling the current scan\u2026', 'busy', 'CANCELLATION REQUESTED');
    try {
      await api('/api/jobs/' + encodeURIComponent(id) + '/cancel', {});
      if (id !== state.job || epoch !== state.epoch) return;
      if (state.disconnected) { setBusy(true); setStatus('Cancellation requested. Confirming the job outcome...', 'busy'); pollJob(id, epoch); }
    } catch (error) {
      if (id !== state.job || epoch !== state.epoch) return;
      if (handleMissingJob(error)) return;
      $('cancel-button').disabled = false;
      if (state.disconnected) showDisconnected('Could not confirm cancellation: ' + error.message);
      else setStatus('Could not confirm cancellation: ' + error.message + ' Job status checks continue.', 'error');
    }
  });
  $('refresh-button').addEventListener('click', () => {
    if (state.job && state.disconnected) { const id = state.job; setBusy(true); setStatus('Reconnecting to the existing job...', 'busy', 'RECONNECTING'); pollJob(id, state.epoch); return; }
    if (!state.lastRequest) return;
    if (state.lastRequest.mode === 'files') { setStatus('Reselect files to read a fresh snapshot. Cancel keeps your current result.'); $(state.lastFilePicker).click(); }
    else startScan(state.lastRequest, state.lastClientNote);
  });
  $('download-button').addEventListener('click', () => {
    if (!state.result) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(state.result, null, 2) + '\n'], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = 'contradiction-map.json'; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach(item => { const active = item === button; item.classList.toggle('active', active); item.setAttribute('aria-pressed', String(active)); });
    if (state.result) renderFindings();
  }));
  function setView(flat) {
    $('graph-container').hidden = flat; $('flat-container').hidden = !flat;
    $('graph-view-button').classList.toggle('active', !flat); $('flat-view-button').classList.toggle('active', flat);
    $('graph-view-button').setAttribute('aria-pressed', String(!flat)); $('flat-view-button').setAttribute('aria-pressed', String(flat));
    if (flat && !state.result) $('flat-results').replaceChildren(el('p', 'flat-intro', 'Scan a repository or explore the demo to read findings in an accessible list.'));
  }
  $('graph-view-button').addEventListener('click', () => setView(false));
  $('flat-view-button').addEventListener('click', () => setView(true));
  $('help-toggle').addEventListener('click', () => { const open = $('help-panel').hidden; $('help-panel').hidden = !open; $('help-toggle').setAttribute('aria-expanded', String(open)); });
  renderGraph();
  api('/api/info').then(info => {
    $('version').textContent = 'v' + (info.version || '0.1.0');
    if (info.limits) Object.assign(state.limits, info.limits);
  }).catch(error => { setStatus('Local companion is unavailable. Start the app with Start-ContradictionMap.ps1 and reopen its local URL. ' + error.message, 'error', 'CONNECTION REQUIRED'); });
})();
