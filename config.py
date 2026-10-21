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

# Real Google Chrome ka channel (jaise "chrome"). CHROME_PATH na ho to ye use hoga.
CHROME_CHANNEL = os.environ.get("CHROME_CHANNEL", "").strip()

# Stealth engine: "auto" (patchright -> playwright), "patchright", "playwright"
STEALTH_ENGINE = os.environ.get("STEALTH_ENGINE", "auto").strip().lower()

# Cookies/Cloudflare clearance bachane ke liye persistent profile
USE_PERSISTENT_PROFILE = os.environ.get("USE_PERSISTENT_PROFILE", "1").strip().lower() in (
    "1", "true", "yes", "on")
CHROME_PROFILE_DIR = os.environ.get("CHROME_PROFILE_DIR", "chrome_profile")
# Renderer ka alag profile (purane tabs/session restore se bachne ke liye)
RENDER_PROFILE_DIR = os.environ.get("RENDER_PROFILE_DIR", ".render_profile")

# Images/fonts/media block (bandwidth bachao)
BLOCK_MEDIA = os.environ.get("BLOCK_MEDIA", "1").strip().lower() in (
    "1", "true", "yes", "on")

# Residential proxy (jaise http://user:pass@host:port ya host:port). Khali = direct.
PROXY_URL = os.environ.get("PROXY_URL", "").strip()

# ============================================
# BRAND-SITE INTEL (sale / shipping / newsletter)
# ============================================
REGION = os.environ.get("SCRAPER_REGION", "us").strip().lower()
SCRAPE_BRAND_SITE = os.environ.get("SCRAPE_BRAND_SITE", "1").strip().lower() in (
    "1", "true", "yes", "on")
SALE_LIMIT = int(os.environ.get("SALE_LIMIT", "250") or "250")

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
