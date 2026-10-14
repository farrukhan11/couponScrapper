"""
COUPON SCRAPER — WEB UI
=======================
Non-tech team ke liye browser control panel:
  - Shared-password login
  - Brands paste karo (naam ya domain, ek per line) -> Run
  - Live logs + results table + CSV download
  - Run history (per-run saved results)
  - Store-index refresh button
  - Headless toggle

Run (dev):    python web_ui.py                 ->  http://localhost:8000
Run (prod):   gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 web_ui:app
"""
import csv
import hmac
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from functools import wraps

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

ROOT = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(ROOT, getattr(config, "JOBS_DIR", "jobs") or "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

RESULTS_CSV = os.path.join(ROOT, "results.csv")
INDEX_FILE = os.path.join(ROOT, "data", "stores_index.json")
MAX_LINES = 2000
MAX_BRANDS = 200

app = Flask(__name__)
app.secret_key = getattr(config, "UI_SECRET_KEY", "") or uuid.uuid4().hex
app.config["PASSWORD"] = (getattr(config, "UI_PASSWORD", "") or "").strip()

LOCK = threading.RLock()
JOBS = {}                      # id -> in-memory job dict (running + finished)
ACTIVE = {"id": None}          # ek waqt mein ek job


# ============================================================
# AUTH
# ============================================================
def auth_ok():
    if not app.config["PASSWORD"]:
        return True            # password set nahi -> dev mode (no auth)
    return bool(session.get("auth"))


def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not auth_ok():
            if request.path.startswith(("/api/", "/download")):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrap


@app.route("/login", methods=["GET", "POST"])
def login():
    if not app.config["PASSWORD"]:
        return redirect(url_for("index"))
    err = None
    if request.method == "POST":
        pwd = (request.form.get("password") or "").strip()
        if hmac.compare_digest(pwd.encode("utf-8"),
                               app.config["PASSWORD"].encode("utf-8")):
            session["auth"] = True
            return redirect(request.args.get("next") or url_for("index"))
        err = "Ghalat password"
    return render_template("login.html", error=err)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ============================================================
# JOB MANAGEMENT
# ============================================================
def _meta_path(job_id):
    return os.path.join(JOBS_DIR, job_id, "meta.json")


def _write_meta(job):
    meta = {
        "id": job["id"], "kind": job["kind"],
        "brands": job.get("brands", []),
        "brands_count": len(job.get("brands", [])),
        "started": job["started"], "started_ts": job["started_ts"],
        "done": job.get("done", False), "exit_code": job.get("exit_code"),
        "has_results": os.path.exists(os.path.join(job["dir"], "results.csv")),
    }
    try:
        with open(_meta_path(job["id"]), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception:
        pass


def _load_meta(job_id):
    try:
        with open(_meta_path(job_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def list_jobs():
    out = []
    if not os.path.isdir(JOBS_DIR):
        return out
    for name in os.listdir(JOBS_DIR):
        m = _load_meta(name)
        if m:
            out.append(m)
    out.sort(key=lambda m: m.get("started_ts", 0), reverse=True)
    return out


def read_csv(path, limit=1000):
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


def build_cmd(kind, job_dir, headless):
    if kind == "index":
        return [sys.executable, os.path.join(ROOT, "sitemap_crawler.py")]
    brands_file = os.path.join(job_dir, "brands.txt")
    return [sys.executable, os.path.join(ROOT, "page_scraper.py"),
            "--file", brands_file]


def run_job_thread(job_id):
    job = JOBS.get(job_id)
    if not job:
        return
    env = dict(os.environ,
               PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
               SCRAPER_HEADLESS="1" if job["headless"] else "0")
    logs_path = os.path.join(job["dir"], "logs.txt")
    try:
        proc = subprocess.Popen(
            job["cmd"], cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        job["proc"] = proc
        with open(logs_path, "w", encoding="utf-8") as lf:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                if line == "":
                    continue
                job["lines"].append(line)
                lf.write(line + "\n")
                lf.flush()
        proc.wait()
        job["exit_code"] = proc.returncode
    except Exception as e:
        job["lines"].append(f"[UI ERROR] {e}")
        job["exit_code"] = -1

    # ---- outputs collect ----
    try:
        if job["kind"] == "scrape" and os.path.exists(RESULTS_CSV):
            shutil.copy2(RESULTS_CSV, os.path.join(job["dir"], "results.csv"))
            try:
                from summary import build_summary
                build_summary(os.path.join(job["dir"], "results.csv"),
                              os.path.join(job["dir"], "summary.csv"))
            except Exception as e:
                job["lines"].append(f"[UI WARN] summary fail: {e}")
        elif job["kind"] == "index" and os.path.exists(INDEX_FILE):
            shutil.copy2(INDEX_FILE, os.path.join(job["dir"], "stores_index.json"))
    except Exception as e:
        job["lines"].append(f"[UI ERROR] output copy: {e}")

    job["done"] = True
    _write_meta(job)
    with LOCK:
        if ACTIVE["id"] == job_id:
            ACTIVE["id"] = None


def start_job(kind, brands, headless):
    with LOCK:
        if ACTIVE["id"] and not JOBS.get(ACTIVE["id"], {}).get("done", True):
            return None, "Ek job pehle se chal raha hai — pehle usay stop/khatam karo"

        job_id = uuid.uuid4().hex[:10]
        job_dir = os.path.join(JOBS_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)

        if kind == "scrape":
            if not brands:
                return None, "Kam se kam 1 brand do (naam ya domain)"
            brands = brands[:MAX_BRANDS]
            with open(os.path.join(job_dir, "brands.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(brands) + "\n")

        cmd = build_cmd(kind, job_dir, headless)
        job = {
            "id": job_id, "kind": kind, "dir": job_dir, "cmd": cmd,
            "brands": brands, "headless": headless,
            "lines": deque(maxlen=MAX_LINES), "done": False, "exit_code": None,
            "proc": None,
            "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "started_ts": time.time(),
        }
        JOBS[job_id] = job
        _write_meta(job)
        ACTIVE["id"] = job_id
        threading.Thread(target=run_job_thread, args=(job_id,), daemon=True).start()
        return job_id, None


def get_job_view(job_id):
    """Running job in-memory se, finished job disk se."""
    job = JOBS.get(job_id)
    if job:
        return {
            "job": job_id, "running": not job["done"], "done": job["done"],
            "exit_code": job["exit_code"], "started": job["started"],
            "kind": job["kind"], "brands": job.get("brands", []),
            "lines": list(job["lines"])[-400:],
        }
    meta = _load_meta(job_id)
    if not meta:
        return None
    lines = []
    logs = os.path.join(JOBS_DIR, job_id, "logs.txt")
    if os.path.exists(logs):
        with open(logs, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()[-400:]
    return {
        "job": job_id, "running": False, "done": True,
        "exit_code": meta.get("exit_code"), "started": meta.get("started"),
        "kind": meta.get("kind"), "brands": meta.get("brands", []),
        "lines": lines,
    }


# ============================================================
# ROUTES
# ============================================================
@app.route("/")
@login_required
def index():
    return render_template("dashboard.html", max_brands=MAX_BRANDS)


@app.post("/api/run")
@login_required
def api_run():
    data = request.get_json(force=True, silent=True) or {}
    raw = (data.get("brands") or "").strip()
    brands = [b.strip() for b in raw.replace(",", "\n").splitlines() if b.strip()]
    headless = bool(data.get("headless", True))
    job_id, err = start_job("scrape", brands, headless)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"job": job_id})


@app.post("/api/refresh-index")
@login_required
def api_refresh_index():
    data = request.get_json(force=True, silent=True) or {}
    headless = bool(data.get("headless", True))
    job_id, err = start_job("index", [], headless)
    if err:
        return jsonify({"error": err}), 409
    return jsonify({"job": job_id})


@app.get("/api/status")
@login_required
def api_status():
    view = get_job_view(request.args.get("job", ""))
    if not view:
        return jsonify({"error": "job nahi mila"}), 404
    return jsonify(view)


@app.post("/api/stop")
@login_required
def api_stop():
    job_id = request.args.get("job", "") or (request.get_json(silent=True) or {}).get("job", "")
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job nahi mila"}), 404
    proc = job.get("proc")
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            job["lines"].append("[UI] ⛔ Stop — process terminate kiya gaya")
            return jsonify({"stopped": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"stopped": False, "note": "process already finished"})


@app.get("/api/jobs")
@login_required
def api_jobs():
    return jsonify({"jobs": list_jobs()})


@app.get("/api/results")
@login_required
def api_results():
    job_id = request.args.get("job", "")
    job_dir = os.path.join(JOBS_DIR, job_id)
    if not os.path.isdir(job_dir):
        return jsonify({"error": "job nahi mila"}), 404
    summary = read_csv(os.path.join(job_dir, "summary.csv"))
    results = read_csv(os.path.join(job_dir, "results.csv"), limit=2000)
    total_codes = sum(len([c for c in (r.get("codes") or "").split(", ") if c])
                      for r in summary)
    return jsonify({
        "summary": summary, "results": results,
        "totals": {"brands": len(summary), "codes": total_codes},
        "files": {
            "results": os.path.exists(os.path.join(job_dir, "results.csv")),
            "summary": os.path.exists(os.path.join(job_dir, "summary.csv")),
            "logs": os.path.exists(os.path.join(job_dir, "logs.txt")),
        },
    })


@app.get("/api/download/<job_id>/<what>")
@login_required
def api_download(job_id, what):
    job_dir = os.path.join(JOBS_DIR, job_id)
    mapping = {
        "results": ("results.csv", "results.csv", "text/csv"),
        "summary": ("summary.csv", "summary.csv", "text/csv"),
        "logs": ("logs.txt", "logs.txt", "text/plain"),
        "index": ("stores_index.json", "stores_index.json", "application/json"),
    }
    if what not in mapping:
        abort(404)
    fname, dl, mime = mapping[what]
    path = os.path.join(job_dir, fname)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=dl, mimetype=mime)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", str(getattr(config, "PORT", 8000))))
    print("=" * 60)
    print(f"🌐 COUPON SCRAPER UI  →  http://0.0.0.0:{port}")
    if not app.config["PASSWORD"]:
        print("⚠️  UI_PASSWORD set nahi — koi auth nahi (dev mode)")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
