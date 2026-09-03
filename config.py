# ============================================
# CONFIGURATION
# ============================================

# Regions (sirf labeling/CSV ke liye ab, kyunke sites client ne di hain)
REGIONS = ["uk"]

# Client ki di hui coupon sites ki list (ek per line)
SITES_FILE = "sites.txt"

# Delays (seconds) - human-like
MIN_DELAY = 8
MAX_DELAY = 15

# CAPTCHA settings
CAPTCHA_TIMEOUT = 300   # Max wait (agar solve na kar sako)
CAPTCHA_CHECK = 2       # Har 2 sec mein check (jaldi detect hoga)

# Apna Chrome use karo
USE_REAL_CHROME = True
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# Output files
URLS_CSV = "urls.csv"               # Saari URLs yahan
OUTPUT_CSV = "results.csv"          # Codes yahan
DEALS_CSV = "deals.csv"
SEEN_CODES_FILE = "data/seen_codes.json"

# Browser
HEADLESS = False
SLOW_MO = 300

# ============================================
# AI AGENT
# ============================================
USE_AI = True
AI_PROVIDER = "gemini"     # gemini
AI_API_KEY = ""   # ollama ke liye ""
AI_MODEL = "gemini-3.1-flash-lite"       # Sabse sasta aur fast model for web scraping
AI_MAX_STEPS = 3         # har page par max AI decisions