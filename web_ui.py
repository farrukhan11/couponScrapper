"""
COUPON SCRAPER — WEB UI
=======================
Form me brands (name/URL), region aur options do → pipeline chalega
live logs ke saath → results table + CSV/txt downloads.

Run:  python web_ui.py     →  http://localhost:5000
"""
import csv
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from collections import deque
from datetime import datetime

from flask import Flask, jsonify, request, render_template_string, send_file, abort

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

ROOT = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(ROOT, "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

app = Flask(__name__)
LOCK = threading.Lock()
JOBS = {}  # id -> job dict
ACTIVE = {"id": None}


# ============================================================
# JOB RUNNER
# ============================================================
def build_cmd(job_dir, params):
    brands_file = os.path.join(job_dir, "brands.txt")
    cmd = [sys.executable, os.path.join(ROOT, "coupon_pipeline.py"),
           "--brands", brands_file, "--region", params.get("region", "uk")]
    sites = (params.get("sites") or "").strip()
    if sites:
        cmd += ["--sites", sites]
    if params.get("google"):
        cmd += ["--google"]
    if params.get("fresh"):
        cmd += ["--fresh"]
    return cmd


def run_job_thread(job_id):
    job = JOBS[job_id]
    job_dir = job["dir"]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    try:
        proc = subprocess.Popen(
            job["cmd"], cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        job["proc"] = proc
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                job["lines"].append(line)
        proc.wait()
        job["exit_code"] = proc.returncode
    except Exception as e:
        job["lines"].append(f"[UI ERROR] {e}")
        job["exit_code"] = -1

    # Outputs collect karo
    region = job["region"]
    try:
        for fname in (f"results_v2_{region}.csv", f"summary_v2_{region}.csv"):
            src = os.path.join(ROOT, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(job_dir, fname))
        ext_dir = os.path.join(ROOT, f"extension_codes_v2_{region}")
        if os.path.isdir(ext_dir):
            zpath = os.path.join(job_dir, "extension_codes.zip")
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in os.listdir(ext_dir):
                    if f.endswith(".txt"):
                        zf.write(os.path.join(ext_dir, f), f)
    except Exception as e:
        job["lines"].append(f"[UI ERROR] output copy: {e}")

    job["done"] = True
    with LOCK:
        if ACTIVE["id"] == job_id:
            ACTIVE["id"] = None


def start_job(params):
    brands_raw = (params.get("brands") or "").strip()
    brands = [b.strip() for b in re.split(r"[\n,]+", brands_raw) if b.strip()]
    if not brands:
        return None, "Kam se kam 1 brand do (naam ya URL)"
    brands = brands[:100]

    job_id = uuid.uuid4().hex[:10]
    job_dir = os.path.join(JOBS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    with open(os.path.join(job_dir, "brands.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(brands) + "\n")

    region = (params.get("region") or "uk").lower()
    cmd = build_cmd(job_dir, {**params, "region": region})
    job = {
        "id": job_id, "dir": job_dir, "cmd": cmd, "region": region,
        "brands": brands, "lines": deque(maxlen=1500),
        "done": False, "exit_code": None, "proc": None,
        "started": datetime.now().strftime("%H:%M:%S"),
    }
    JOBS[job_id] = job
    threading.Thread(target=run_job_thread, args=(job_id,), daemon=True).start()
    return job_id, None


# ============================================================
# API
# ============================================================
@app.post("/api/run")
def api_run():
    with LOCK:
        if ACTIVE["id"] and not JOBS.get(ACTIVE["id"], {}).get("done", True):
            return jsonify({"error": "Ek job pehle se chal raha hai — pehle usay khatam/stop karo"}), 409
        params = request.get_json(force=True) or {}
        job_id, err = start_job(params)
        if err:
            return jsonify({"error": err}), 400
        ACTIVE["id"] = job_id
    return jsonify({"job": job_id})


@app.get("/api/status")
def api_status():
    job_id = request.args.get("job", "")
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job nahi mila"}), 404
    return jsonify({
        "job": job_id,
        "running": not job["done"],
        "done": job["done"],
        "exit_code": job["exit_code"],
        "started": job["started"],
        "region": job["region"],
        "brands": job["brands"],
        "lines": list(job["lines"])[-400:],
    })


@app.post("/api/stop")
def api_stop():
    job_id = request.args.get("job", "") or (request.get_json(silent=True) or {}).get("job", "")
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job nahi mila"}), 404
    proc = job.get("proc")
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            job["lines"].append("[UI] ⛔ Stop button — process terminate kiya gaya")
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"stopped": True})
    return jsonify({"stopped": False, "note": "process already finished"})


def read_csv(path, limit=500):
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f)):
                rows.append(row)
                if i >= limit:
                    break
    except Exception:
        pass
    return rows


@app.get("/api/results")
def api_results():
    job_id = request.args.get("job", "")
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job nahi mila"}), 404
    if not job["done"]:
        return jsonify({"error": "job abhi chal raha hai"}), 400
    region = job["region"]
    summary = read_csv(os.path.join(job["dir"], f"summary_v2_{region}.csv"), limit=100)
    results = read_csv(os.path.join(job["dir"], f"results_v2_{region}.csv"), limit=500)
    total_codes = sum(int(float(r.get("total_codes") or 0)) for r in summary)
    return jsonify({
        "summary": summary, "results": results,
        "totals": {"brands": len(summary), "codes": total_codes},
        "files": {
            "results": os.path.exists(os.path.join(job["dir"], f"results_v2_{region}.csv")),
            "summary": os.path.exists(os.path.join(job["dir"], f"summary_v2_{region}.csv")),
            "codes_zip": os.path.exists(os.path.join(job["dir"], "extension_codes.zip")),
        },
    })


@app.get("/api/download/<job_id>/<what>")
def api_download(job_id, what):
    job = JOBS.get(job_id)
    if not job:
        abort(404)
    region = job["region"]
    mapping = {
        "results": (os.path.join(job["dir"], f"results_v2_{region}.csv"),
                    f"results_v2_{region}.csv", "text/csv"),
        "summary": (os.path.join(job["dir"], f"summary_v2_{region}.csv"),
                    f"summary_v2_{region}.csv", "text/csv"),
        "codes": (os.path.join(job["dir"], "extension_codes.zip"),
                  f"coupon_codes_{region}.zip", "application/zip"),
    }
    if what not in mapping:
        abort(404)
    path, name, mime = mapping[what]
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=name, mimetype=mime)


# ============================================================
# UI (inline single-page)
# ============================================================
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Coupon Scraper — Control Panel</title>
<style>
:root{
  --bg:#0b1020; --panel:#121831; --panel2:#0e1428; --line:#232b4d;
  --txt:#e8ecff; --mut:#8b93b8; --acc:#7c5cff; --acc2:#00d4a1; --warn:#ffb454; --bad:#ff5c7a;
}
*{box-sizing:border-box; margin:0; padding:0}
body{background:var(--bg); color:var(--txt); font-family:'Segoe UI',system-ui,sans-serif; min-height:100vh}
.wrap{max-width:1180px; margin:0 auto; padding:28px 20px 60px}
header{display:flex; align-items:center; gap:14px; margin-bottom:26px}
.logo{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,var(--acc),var(--acc2));
  display:flex;align-items:center;justify-content:center;font-size:22px}
h1{font-size:22px; font-weight:600}
h1 span{color:var(--mut); font-weight:400; font-size:14px; display:block}
.grid{display:grid; grid-template-columns:400px 1fr; gap:18px}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.card{background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px}
label{display:block; font-size:12px; color:var(--mut); text-transform:uppercase; letter-spacing:.08em; margin:14px 0 6px}
label:first-child{margin-top:0}
textarea,input,select{width:100%; background:var(--panel2); border:1px solid var(--line); color:var(--txt);
  border-radius:10px; padding:10px 12px; font-size:14px; font-family:inherit; outline:none}
textarea{min-height:96px; resize:vertical}
textarea:focus,input:focus,select:focus{border-color:var(--acc)}
.row2{display:grid; grid-template-columns:1fr 1fr; gap:12px}
.chk{display:flex; align-items:center; gap:8px; font-size:13px; color:var(--txt); margin-top:10px; cursor:pointer}
.chk input{width:auto}
.hint{font-size:11.5px; color:var(--mut); margin-top:5px; line-height:1.5}
button{border:0; border-radius:10px; padding:11px 18px; font-size:14px; font-weight:600; cursor:pointer;
  font-family:inherit; transition:.15s}
.btn-run{width:100%; margin-top:16px; background:linear-gradient(135deg,var(--acc),#5a3df0); color:#fff}
.btn-run:hover{filter:brightness(1.12)}
.btn-run:disabled{opacity:.5; cursor:not-allowed}
.btn-stop{background:#2a1530; color:var(--bad); border:1px solid #4d2038; padding:8px 14px; font-size:13px}
.btn-dl{background:var(--panel2); color:var(--acc2); border:1px solid var(--line); font-size:13px; padding:8px 13px}
.btn-dl:hover{border-color:var(--acc2)}
.terms{display:flex; gap:8px; flex-wrap:wrap; margin-top:12px}
.pill{font-size:11px; background:var(--panel2); border:1px solid var(--line); color:var(--mut);
  padding:4px 10px; border-radius:999px}
.term{background:#070b16; border:1px solid var(--line); border-radius:12px; height:340px; overflow-y:auto;
  padding:12px 14px; font-family:'Cascadia Code',Consolas,monospace; font-size:12.2px; line-height:1.65; white-space:pre-wrap; word-break:break-all}
.term .l{color:#9aa3c7}
.term .ok{color:var(--acc2)} .term .bad{color:var(--bad)} .term .warn{color:var(--warn)}
.status{display:flex; align-items:center; gap:10px; margin:14px 0 10px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--mut)}
.dot.run{background:var(--warn); animation:pulse 1.2s infinite}
.dot.done{background:var(--acc2)}
@keyframes pulse{50%{opacity:.35}}
h2{font-size:15px; font-weight:600; margin-bottom:12px; display:flex; justify-content:space-between; align-items:center}
table{width:100%; border-collapse:collapse; font-size:13px}
th{color:var(--mut); text-align:left; font-weight:500; font-size:11.5px; text-transform:uppercase;
  letter-spacing:.06em; padding:8px 10px; border-bottom:1px solid var(--line)}
td{padding:9px 10px; border-bottom:1px solid #1a2140; vertical-align:top}
tr:hover td{background:#151c38}
.codes{color:var(--acc2); font-family:Consolas,monospace; font-size:12px}
.badge{font-size:11px; padding:2px 9px; border-radius:999px; background:#123a2f; color:var(--acc2)}
.dls{display:flex; gap:8px}
.empty{color:var(--mut); font-size:13px; text-align:center; padding:26px}
.toast{position:fixed; bottom:20px; right:20px; background:var(--bad); color:#fff; padding:12px 18px;
  border-radius:10px; font-size:13.5px; display:none; z-index:9}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">🎫</div>
    <h1>Coupon Scraper <span>24 client sites · sitemap router · reveal-click extraction</span></h1>
  </header>

  <div class="grid">
    <!-- LEFT: form -->
    <div class="card">
      <label>Brands (naam ya URL — ek per line, max 100)</label>
      <textarea id="brands" placeholder="shemed.co.uk&#10;valerion.com&#10;boohoo"></textarea>

      <div class="row2">
        <div>
          <label>Region</label>
          <select id="region">
            <option value="uk" selected>UK</option>
            <option value="us">US</option>
          </select>
        </div>
        <div>
          <label>Sites filter (optional)</label>
          <input id="sites" placeholder="savoo.co.uk, groupon.co.uk">
        </div>
      </div>

      <label class="chk"><input type="checkbox" id="fresh" checked> Fresh run (purane codes ignore)</label>
      <label class="chk"><input type="checkbox" id="google"> Google fallback (Layer 3 — CAPTCHA khud solve karna ho sakta hai)</label>
      <div class="hint">💡 Sites filter blank = saari 23 sites. Search <b>100% own infra</b> hai:
      Layer 2 me site ke apne search box se brand page dhoonda jata hai (koi external API nahi).
      Google fallback optional hai — Chrome window khulegi aur CAPTCHA aaye to manually solve karo.</div>

      <button class="btn-run" id="runBtn" onclick="run()">▶ Run Scraper</button>
      <div class="terms" id="jobMeta"></div>
    </div>

    <!-- RIGHT: logs + results -->
    <div>
      <div class="card">
        <h2>Live Output <span id="stopWrap"></span></h2>
        <div class="status" id="statusBar"><span class="dot"></span><span style="font-size:13px;color:var(--mut)">Idle — job start karo</span></div>
        <div class="term" id="term"><div class="l">> pipeline ready. Brands daal kar Run dabao.</div></div>
      </div>

      <div class="card" style="margin-top:18px">
        <h2>Results
          <span class="dls" id="dls"></span>
        </h2>
        <div id="results"><div class="empty">Results yahan aayenge job complete hone par.</div></div>
      </div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
let job=null, poll=null;
const $=id=>document.getElementById(id);
const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
function toast(m){const t=$('toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',3500)}
function cls(line){
  if(line.includes('✅')||line.includes('DONE'))return 'ok';
  if(line.includes('⛔')||line.includes('❌')||line.includes('ERROR'))return 'bad';
  if(line.includes('⚠️')||line.includes('🚫')||line.includes('∅'))return 'warn';
  return 'l';
}
function renderLog(lines){
  $('term').innerHTML=lines.map(l=>`<div class="${cls(l)}">${esc(l)}</div>`).join('');
  $('term').scrollTop=$('term').scrollHeight;
}
function setStatus(state,text){
  $('statusBar').innerHTML=`<span class="dot ${state}"></span><span style="font-size:13px;color:var(--mut)">${text}</span>`;
}
async function run(){
  const brands=$('brands').value.trim();
  if(!brands){toast('Pehle brands likho!');return}
  $('runBtn').disabled=true;
  try{
    const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({brands,region:$('region').value,sites:$('sites').value,
        fresh:$('fresh').checked,google:$('google').checked})});
    const d=await r.json();
    if(!r.ok){toast(d.error||'Error');$('runBtn').disabled=false;return}
    job=d.job;
    $('stopWrap').innerHTML=`<button class="btn-stop" onclick="stopJob()">■ Stop</button>`;
    setStatus('run','Running…');
    poll=setInterval(tick,1300);
  }catch(e){toast('Server error');$('runBtn').disabled=false}
}
async function tick(){
  try{
    const r=await fetch('/api/status?job='+job);
    if(!r.ok)return;
    const d=await r.json();
    renderLog(d.lines);
    $('jobMeta').innerHTML=`<span class="pill">Job ${job}</span><span class="pill">Region: ${d.region.toUpperCase()}</span><span class="pill">Brands: ${d.brands.length}</span><span class="pill">Start: ${d.started}</span>`;
    if(d.done){
      clearInterval(poll);poll=null;
      setStatus('done',d.exit_code===0?'Completed ✅':`Finished (exit ${d.exit_code})`);
      $('runBtn').disabled=false;$('stopWrap').innerHTML='';
      loadResults();
    }
  }catch(e){}
}
async function stopJob(){
  await fetch('/api/stop?job='+job,{method:'POST'});
}
async function loadResults(){
  const r=await fetch('/api/results?job='+job);
  if(!r.ok)return;
  const d=await r.json();
  let dl='';
  if(d.files.results)dl+=`<a class="btn-dl" style="text-decoration:none" href="/api/download/${job}/results">⬇ Results CSV</a>`;
  if(d.files.summary)dl+=`<a class="btn-dl" style="text-decoration:none" href="/api/download/${job}/summary">⬇ Summary CSV</a>`;
  if(d.files.codes_zip)dl+=`<a class="btn-dl" style="text-decoration:none" href="/api/download/${job}/codes">⬇ Codes ZIP</a>`;
  $('dls').innerHTML=dl;
  if(!d.summary.length){$('results').innerHTML='<div class="empty">Koi result nahi mila.</div>';return}
  let h=`<div class="hint" style="margin-bottom:10px">${d.totals.brands} brands · ${d.totals.codes} codes</div>
  <table><tr><th>Brand</th><th>Codes</th><th>Coupon Codes</th><th>Deals</th><th>Sites</th></tr>`;
  for(const s of d.summary){
    h+=`<tr><td><b>${esc(s.brand)}</b></td><td><span class="badge">${s.total_codes}</span></td>
    <td class="codes">${esc((s.coupon_codes||'').slice(0,180))}</td>
    <td class="codes">${esc((s.deals||'None').slice(0,80))}</td><td>${s.resolved_sites||''}</td></tr>`;
  }
  h+='</table>';
  $('results').innerHTML=h;
}
</script>
</body>
</html>"""


@app.get("/")
def index():
    return render_template_string(PAGE)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print("=" * 60)
    print(f"🌐 WEB UI  →  http://localhost:{port}")
    print("=" * 60)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
