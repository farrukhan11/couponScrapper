# ============================================
# CONFIGURATION (env-based)
# ============================================
# Ye values environment variables se aati hain, warna default.
# Cloud VM (Contabo Ubuntu) par .env file se set karo.
import os

# .env auto-load (real env vars override karti hain)
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV_FILE):
    try:
        with open(_ENV_FILE, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
    except Exception:
        pass

# ============================================
# BROWSER
# ============================================
# Apna Chrome (browser-fallback fetches ke liye).
# Khali chhod do to Playwright ka bundled Chromium use hoga.
CHROME_PATH = os.environ.get(
    "CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")

# Headless browser? Cloud VM (bina display) par "1" zaroori.
# Local dev par "0" (visible Chrome) behtar stealth deta hai.
HEADLESS = os.environ.get("SCRAPER_HEADLESS", "0").strip().lower() in (
    "1", "true", "yes", "on")

# ============================================
# FIRECRAWL FALLBACK (sirf BLOCKED pages ke liye — optional)
# ============================================
# https://firecrawl.dev se key lo. Khali = sirf apna infra (free).
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")

# ============================================
# WEB UI
# ============================================
# Team ke liye shared password (login). Khali = koi auth nahi (unsafe).
UI_PASSWORD = os.environ.get("UI_PASSWORD", "")
# Session signing key. Khali = random (har restart par sessions reset).
UI_SECRET_KEY = os.environ.get("UI_SECRET_KEY", "")
PORT = int(os.environ.get("PORT", "8000") or "8000")
# Per-run jobs (results/history) yahan save hote hain.
JOBS_DIR = os.environ.get("JOBS_DIR", "jobs")
