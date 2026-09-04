# ============================================
# CONFIGURATION
# ============================================

# Regions (sirf labeling/CSV ke liye ab, kyunke sites client ne di hain)
REGIONS = ["uk"]

# Client ki di hui coupon sites ki list (ek per line)
SITES_FILE = "sites.txt"

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
# EXTERNAL API (DEPRECATED — ab use nahi hota)
# ============================================
# Search ab own_search.py se hota hai (apna Google/Bing browser stack) —
# koi Firecrawl/API dependency nahi. Key sirf legacy/reference ke liye:
# FIRECRAWL_API_KEY = "fc-..."
USE_AI = False
AI_PROVIDER = "gemini"     # gemini
AI_API_KEY = ""   # ollama ke liye ""
AI_MODEL = "gemini-3.1-flash-lite"       # Sabse sasta aur fast model for web scraping
AI_MAX_STEPS = 3         # har page par max AI decisions