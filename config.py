# ============================================
# CONFIGURATION
# ============================================

# Search engine: "google" ya "bing"
SEARCH_ENGINE = "google"

# Regions
REGIONS = ["uk"]

# Google ke kitne pages search karne hain (page 1 mein hi num=20 results aate hain)
SEARCH_PAGES = 1

# Kitni coupon sites visit karni hain per brand
MAX_SITES_PER_BRAND = 8

# Sirf search URLs collect karne hain (sites visit na karein)?
SEARCH_ONLY = False

# Delays (seconds) - human-like but fast
MIN_DELAY = 1
MAX_DELAY = 2

# CAPTCHA settings
CAPTCHA_TIMEOUT = 300   # Max wait (agar solve na kar sako)
CAPTCHA_CHECK = 2       # Har 2 sec mein check (jaldi detect hoga)

# Apna Chrome use karo
USE_CDP = False
USE_REAL_CHROME = True
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_USER_DATA_DIR = "chrome_profile"
CHROME_PROFILE = ""

# Output files - UK batch separate from previous runs
URLS_CSV = "urls_uk.csv"
OUTPUT_CSV = "results_uk.csv"
DEALS_CSV = "deals_uk.csv"
SEEN_CODES_FILE = "data/seen_codes_uk.json"

# Browser
HEADLESS = False
SLOW_MO = 50

# ============================================
# FIRECRAWL / EXTERNAL API
# ============================================
FIRECRAWL_API_KEY = "fc-5a9a8e92c80b427d99ec7072c4aa529f"   # Firecrawl API Key for Google Search without CAPTCHA
USE_AI = False
AI_PROVIDER = "gemini"     # gemini
AI_API_KEY = ""   # ollama ke liye ""
AI_MODEL = "gemini-3.1-flash-lite"       # Sabse sasta aur fast model for web scraping
AI_MAX_STEPS = 3         # har page par max AI decisions