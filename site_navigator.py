"""
SITE NAVIGATOR — brand ke liye kisi bhi coupon site par uska khud ka
search use karke sahi brand/coupon page dhoondta hai.

Kyun ye tareeqa: har coupon aggregator ka apna alag URL pattern hota
hai (simplycodes.com/store/nike.com vs retailescaper.com/adairs-coupon-code
vs groupon.co.uk/coupons/nike) — pattern guess karna fragile hai.
Iski jagah hum wahi karte hain jo ek insaan karega: site kholo, uske
apne search box mein brand type karo, phir sahi result par click karo.

Flow:
  1) Site homepage par jao
  2) Search box dhoondo (agar chupa ho to search-icon click karke reveal karo)
  3) Brand type karke submit karo
  4) Results mein se brand se best-match link dhoondo aur click karo
  5) Final URL wapas do — isi par purana extract_codes_from_page() chalega

Agar step 2/4 selectors se na milay, to ai_agent.py ki LLM infra reuse
karke fallback lagaya ja sakta hai (niche note dekho).
"""
import re
import time
import random
import config

try:
    import ai_agent
except ImportError:
    ai_agent = None


# ============================================
# SEARCH BOX DISCOVERY
# ============================================
SEARCH_ICON_SELECTORS = [
    "[class*='search-icon']", "[class*='searchIcon']", "[aria-label*='earch']",
    "button[class*='search']", "a[class*='search']", "[id*='search-toggle']",
    "svg[class*='search']", "[class*='search-btn']", "[class*='search-trigger']",
]

SEARCH_INPUT_SELECTORS = [
    "input[type='search']",
    "input[name='q']", "input[name='s']", "input[name='query']",
    "input[name='search']", "input[name='keyword']", "input[name='keywords']",
    "input[placeholder*='search' i]", "input[placeholder*='store' i]",
    "input[placeholder*='brand' i]", "input[placeholder*='shop' i]",
    "input[id*='search' i]", "input[class*='search' i]",
]


def find_search_input(page):
    """Pehle seedha dhoondo. Na milay to search-icon click karke reveal karo."""
    for sel in SEARCH_INPUT_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return el
        except Exception:
            continue

    # Chupa hua search box — icon click karke kholna padta hai
    for sel in SEARCH_ICON_SELECTORS:
        try:
            icon = page.query_selector(sel)
            if icon and icon.is_visible():
                icon.click(timeout=2000)
                time.sleep(0.8)
                for isel in SEARCH_INPUT_SELECTORS:
                    el = page.query_selector(isel)
                    if el and el.is_visible():
                        return el
        except Exception:
            continue
    return None


def submit_search(page, box, brand):
    try:
        box.click()
        box.fill(brand)
        random_delay(0.3, 0.7)
        box.press("Enter")
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(2)
        return True
    except Exception:
        return False


# ============================================
# RESULT MATCHING
# ============================================
def brand_slug(brand):
    return re.sub(r"[^a-z0-9]+", "", brand.lower())


def find_best_brand_link(page, brand):
    """Results page (ya homepage listing) mein se brand se sabse
    achi match wala link dhoondta hai."""
    slug = brand_slug(brand)
    brand_low = brand.lower()
    try:
        anchors = page.query_selector_all("a")
    except Exception:
        return None

    exact, partial = [], []
    for a in anchors:
        try:
            href = (a.get_attribute("href") or "").lower()
            text = (a.inner_text() or "").strip().lower()
        except Exception:
            continue
        if not href or href.startswith("javascript") or href.startswith("#"):
            continue
        href_slug = brand_slug(href)
        text_slug = brand_slug(text)
        if not text_slug and not href_slug:
            continue
        if text_low_matches_exact(text, brand_low) or slug == text_slug:
            exact.append(a)
        elif slug in href_slug or slug in text_slug:
            partial.append(a)

    if exact:
        return exact[0]
    if partial:
        return partial[0]
    return None


def text_low_matches_exact(text, brand_low):
    return text == brand_low or text == f"{brand_low} coupon" \
        or text == f"{brand_low} coupons" or text == f"{brand_low} discount codes"


def looks_like_no_results(page):
    try:
        text = page.inner_text("body").lower()
        return any(w in text for w in [
            "no results", "nothing found", "no stores found",
            "0 results", "not found", "no matches",
        ])
    except Exception:
        return False


# ============================================
# MAIN ENTRY POINT
# ============================================
def find_brand_page(page, site_url, brand, timeout=20000):
    """
    Site ke homepage se shuru hoke us site ke apne search se brand
    ka coupon/store page dhoondta hai.
    Return: final page URL (jahan extraction chalani hai) ya None.
    """
    try:
        page.goto(site_url, wait_until="domcontentloaded", timeout=timeout)
        random_delay(1.5, 3)
    except Exception as e:
        print(f"    ⚠️  Site load fail ({site_url}): {e}")
        return None

    dismiss_cookie_banner(page)

    box = find_search_input(page)
    if not box:
        # Fallback: AI se search box / brand link dhundwao (agar available ho)
        if getattr(config, "USE_AI", False) and ai_agent is not None:
            url = ai_find_brand_page(page, brand)
            if url:
                return url
        print(f"    ⚠️  {site_url}: search box nahi mila")
        return None

    if not submit_search(page, box, brand):
        print(f"    ⚠️  {site_url}: search submit fail hui")
        return None

    if looks_like_no_results(page):
        print(f"    ⚠️  {site_url}: '{brand}' ke koi results nahi")
        return None

    link = find_best_brand_link(page, brand)
    if link:
        try:
            link.scroll_into_view_if_needed()
            link.click(timeout=5000)
            page.wait_for_load_state("domcontentloaded", timeout=timeout)
            random_delay(1, 2)
        except Exception:
            pass  # search results page hi kaafi ho sakta hai, aage badho

    return page.url


def ai_find_brand_page(page, brand):
    """USE_AI=True ho aur search box na milay to ai_agent ki LLM call
    reuse karke bhi wahi kaam ho sakta hai — page state (buttons +
    text) AI ko do, AI batati ha kaunsa element brand ka page kholega."""
    try:
        state, els = ai_agent.get_page_state(page)
        decision = ai_agent.ai_decide(state, brand) or {}
        if str(decision.get("action")) == "click":
            n = decision.get("n")
            if isinstance(n, int) and 0 <= n < len(els):
                els[n].click(timeout=5000)
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                return page.url
    except Exception as e:
        print(f"    ⚠️  AI fallback error: {e}")
    return None


def dismiss_cookie_banner(page):
    sels = [
        "button:has-text('Accept All')", "button:has-text('Accept')",
        "button:has-text('Allow All')", "button:has-text('Got It')",
        "button:has-text('I Agree')", "[class*='cookie'] button",
        "[id*='consent'] button", "[class*='consent'] button",
    ]
    for sel in sels:
        try:
            for el in page.query_selector_all(sel):
                if el.is_visible():
                    el.click(timeout=1000)
                    random_delay(0.3, 0.6)
        except Exception:
            continue


def random_delay(a, b):
    time.sleep(random.uniform(a, b))
