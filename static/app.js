let job = null, poll = null;
const $ = id => document.getElementById(id);
const esc = s => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const api = (url, opts) => fetch(url, opts).then(async r => {
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
  return d;
});

function toast(m) {
  const t = $('toast'); t.textContent = m; t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 4000);
}
function cls(line) {
  if (line.includes('✅') || line.includes('DONE')) return 'ok';
  if (line.includes('⛔') || line.includes('❌') || line.includes('ERROR')) return 'bad';
  if (line.includes('⚠️') || line.includes('🚫') || line.includes('∅')) return 'warn';
  return 'l';
}
function renderLog(lines) {
  $('term').innerHTML = lines.map(l => `<div class="${cls(l)}">${esc(l)}</div>`).join('');
  $('term').scrollTop = $('term').scrollHeight;
}
function setStatus(state, text) {
  $('statusBar').innerHTML = `<span class="dot ${state}"></span><span class="muted">${esc(text)}</span>`;
}
function setBusy(busy) {
  $('runBtn').disabled = busy;
  $('idxBtn').disabled = busy;
}

async function run() {
  const brands = $('brands').value.trim();
  if (!brands) { toast('Pehle brands likho!'); return; }
  setBusy(true);
  try {
    const d = await api('/api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ brands, headless: $('headless').checked })
    });
    job = d.job; beginPoll();
  } catch (e) { toast(e.message); setBusy(false); }
}

async function refreshIndex() {
  if (!confirm('Store index refresh karun? Ye poore sitemaps dobara crawl karega (thoda waqt lagega).')) return;
  setBusy(true);
  try {
    const d = await api('/api/refresh-index', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ headless: $('headless').checked })
    });
    job = d.job; beginPoll();
  } catch (e) { toast(e.message); setBusy(false); }
}

function beginPoll() {
  $('stopWrap').innerHTML = `<button class="btn-stop" onclick="stopJob()">■ Stop</button>`;
  setStatus('run', 'Running…');
  renderLog(['> job shuru ho gaya…']);
  if (poll) clearInterval(poll);
  poll = setInterval(tick, 1300);
  tick();
}

async function tick() {
  if (!job) return;
  try {
    const d = await api('/api/status?job=' + job);
    renderLog(d.lines.length ? d.lines : ['> chal raha hai…']);
    const kindLabel = d.kind === 'index' ? 'Index refresh' : 'Scrape';
    $('jobMeta').innerHTML =
      `<span class="pill">Job ${d.job}</span>` +
      `<span class="pill">${kindLabel}</span>` +
      `<span class="pill">Brands: ${(d.brands || []).length}</span>` +
      `<span class="pill">Start: ${esc(d.started || '')}</span>`;
    if (d.done) {
      clearInterval(poll); poll = null;
      setStatus('done', d.exit_code === 0 ? 'Completed ✅' : `Finished (exit ${d.exit_code})`);
      setBusy(false); $('stopWrap').innerHTML = '';
      if (d.kind === 'scrape') loadResults();
      loadHistory();
    }
  } catch (e) { /* transient */ }
}

async function stopJob() {
  try { await api('/api/stop?job=' + job, { method: 'POST' }); toast('Stop bhej diya'); }
  catch (e) { toast(e.message); }
}

async function loadResults() {
  if (!job) return;
  let d;
  try { d = await api('/api/results?job=' + job); } catch (e) { return; }
  let dl = '';
  if (d.files.results) dl += `<a class="btn-dl" style="text-decoration:none" href="/api/download/${job}/results">⬇ Results CSV</a>`;
  if (d.files.summary) dl += `<a class="btn-dl" style="text-decoration:none" href="/api/download/${job}/summary">⬇ Summary CSV</a>`;
  if (d.files.logs) dl += `<a class="btn-dl" style="text-decoration:none" href="/api/download/${job}/logs">⬇ Logs</a>`;
  $('dls').innerHTML = dl;
  if (!d.summary.length) { $('results').innerHTML = '<div class="empty">Koi result nahi mila.</div>'; return; }
  let h = `<div class="hint" style="margin-bottom:10px">${d.totals.brands} brands · ${d.totals.codes} codes</div>`;
  h += '<table><tr><th>Brand</th><th>Codes</th><th>Coupon Codes</th><th>Sites</th></tr>';
  for (const s of d.summary) {
    h += `<tr><td><b>${esc(s.brand)}</b></td>` +
      `<td><span class="badge">${esc(s.total_codes)}</span></td>` +
      `<td class="codes">${esc((s.codes || '').slice(0, 300))}</td>` +
      `<td class="codes">${esc((s.sites || '').slice(0, 120))}</td></tr>`;
  }
  h += '</table>';
  $('results').innerHTML = h;
}

async function loadHistory() {
  let d;
  try { d = await api('/api/jobs'); } catch (e) { return; }
  if (!d.jobs.length) { $('history').innerHTML = '<div class="empty">Koi purana run nahi.</div>'; return; }
  let h = '<table><tr><th>Job</th><th>Type</th><th>Brands</th><th>Start</th><th>Result</th><th>Download</th></tr>';
  for (const j of d.jobs.slice(0, 30)) {
    const st = j.done ? (j.exit_code === 0 ? '<span class="badge">done</span>' : `exit ${j.exit_code}`) : 'running…';
    let dl = '';
    if (j.has_results) {
      dl += `<a class="btn-dl" style="text-decoration:none" href="/api/download/${j.id}/results">CSV</a> `;
      dl += `<a class="btn-dl" style="text-decoration:none" href="/api/download/${j.id}/summary">Summary</a>`;
    } else if (j.kind === 'index') {
      dl += `<a class="btn-dl" style="text-decoration:none" href="/api/download/${j.id}/index">JSON</a>`;
    }
    h += `<tr><td>${esc(j.id)}</td><td>${j.kind}</td><td>${j.brands_count}</td>` +
      `<td class="codes">${esc(j.started)}</td><td>${st}</td><td>${dl}</td></tr>`;
  }
  h += '</table>';
  $('history').innerHTML = h;
}

document.addEventListener('DOMContentLoaded', loadHistory);
