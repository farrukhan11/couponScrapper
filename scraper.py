"""
Coupon Code Scraper
Usage: python scraper.py
"""
import os
import re
import csv
import json
import time
import random
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import config


# ============================================
# SETUP
# ============================================
def create_folders():
    os.makedirs("data", exist_ok=True)


def load_brands():
    if not os.path.exists("brands.txt"):
        print("❌ brands.txt nahi mila!")
        return []
    with open("brands.txt", "r") as f:
        brands = [line.strip() for line in f if line.strip()]
    print(f"✅ {len(brands)} brands loaded")
    return brands


def load_seen_codes():
    if os.path.exists(config.SEEN_CODES_FILE):
        with open(config.SEEN_CODES_FILE, "r") as f:
            return json.load(f)
    return {}


def save_seen_codes(seen_codes):
    os.makedirs("data", exist_ok=True)
    with open(config.SEEN_CODES_FILE, "w") as f:
        json.dump(seen_codes, f, indent=2)


def save_urls_to_csv(brand, region, page_num, urls):
    if not urls:
        return
    exists = os.path.exists(config.URLS_CSV)
    with open(config.URLS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["brand", "region", "search_page", "url"])
        for url in urls:
            writer.writerow([brand, region, page_num, url])
    print(f"     💾 {len(urls)} URLs saved (page {page_num})")


def save_result_row(result):
    fields = ["brand", "code", "source_url", "region", "method", "found_at"]
    exists = os.path.exists(config.OUTPUT_CSV)
    with open(config.OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow(result)


def save_deal_row(brand, region, page_url, deal_url, label):
    fields = ["brand", "deal_url", "label", "source_url", "region", "found_at"]
    exists = os.path.exists(config.DEALS_CSV)
    with open(config.DEALS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({
            "brand": brand,
            "deal_url": deal_url,
            "label": label,
            "source_url": page_url,
            "region": region,
            "found_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })


# ============================================
# SEARCH
# ============================================
def build_search_url(query, region, page_num):
    if config.SEARCH_ENGINE == "bing":
        cc = "gb" if region == "uk" else "us"
        start = (page_num - 1) * 10 + 1
        return f"https://www.bing.com/search?q={query}&cc={cc}&first={start}"
    else:
        start = (page_num - 1) * 10
        return f"https://www.google.com/search?q={query}&gl={region}&hl=en&start={start}"


def search_for_coupons(page, brand, region):
    queries = [
        f"coupon code {brand}",
        f"{brand} discount code",
    ]
    all_urls = []
    for query in queries:
        for page_num in range(1, config.SEARCH_PAGES + 1):
            try:
                url = build_search_url(query, region, page_num)
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                random_delay(2, 4)
                wait_for_captcha(page)
                try:
                    page.wait_for_selector("h3", timeout=10000)
                except Exception:
                    pass
                links = extract_search_links(page)
                print(f"     🔎 Page {page_num}: {len(links)} links mile")
                save_urls_to_csv(brand, region, page_num, links)
                all_urls.extend(links)
                random_delay(config.MIN_DELAY, config.MAX_DELAY)
            except Exception as e:
                print(f"  ⚠️  Search error: {e}")
    return filter_coupon_urls(all_urls)[:config.MAX_SITES_PER_BRAND]


def extract_search_links(page):
    links = []
    try:
        if config.SEARCH_ENGINE == "bing":
            for el in page.query_selector_all("li.b_algo h2 a"):
                href = el.get_attribute("href")
                if href and href.startswith("http"):
                    links.append(href)
        else:
            for h3 in page.query_selector_all("h3"):
                try:
                    href = h3.evaluate("el => el.closest('a') ? el.closest('a').href : null")
                except Exception:
                    href = None
                if href and href.startswith("http") and "google." not in href:
                    links.append(href)
    except Exception:
        pass
    seen = set()
    unique = []
    for u in links:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def filter_coupon_urls(urls):
    skip_domains = [
        "youtube.com", "facebook.com", "twitter.com", "instagram.com",
        "wikipedia.org", "reddit.com", "pinterest.com", "tiktok.com",
        "google.com", "bing.com",
    ]
    coupon_indicators = [
        "coupon", "deal", "discount", "promo", "voucher", "offer",
        "retailmenot", "honey", "groupon", "couponfollow",
        "vouchercodes", "hotukdeals", "dealspotr", "savings",
        "couponcabin", "offers.com",
    ]
    priority, normal = [], []
    for url in urls:
        u = url.lower()
        if any(d in u for d in skip_domains):
            continue
        if any(i in u for i in coupon_indicators):
            priority.append(url)
        else:
            normal.append(url)
    combined = priority + normal
    seen = set()
    unique = []
    for url in combined:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


# ============================================
# CAPTCHA
# ============================================
def is_captcha_page(page):
    url = page.url.lower()
    if "/sorry/" in url or "captcha" in url:
        return True
    try:
        text = page.inner_text("body").lower()
        indicators = [
            "unusual traffic", "verify you are human", "are you a robot",
            "before you continue", "complete the security check",
        ]
        if any(ind in text for ind in indicators):
            return True
    except Exception:
        pass
    return False


def wait_for_captcha(page):
    if not is_captcha_page(page):
        return
    print("  🛑 CAPTCHA detected! Browser mein manually solve karo...")
    waited = 0
    while waited < config.CAPTCHA_TIMEOUT:
        time.sleep(config.CAPTCHA_CHECK)
        waited += config.CAPTCHA_CHECK
        if not is_captcha_page(page):
            print(f"  ✅ CAPTCHA solved! Foran aage ({waited}s)")
            random_delay(2, 3)
            return
    print("  ⚠️  CAPTCHA timeout — skip kar rahe hain")


# ============================================
# EXTRACTION — 3 LAYERS
# ============================================
def extract_codes_from_page(page, url, brand, region):
    """
    Layer 1: Poora HTML → data attrs / JSON / scripts
    Layer 2: Visible text
    Layer 3: Har button click → naye codes + deal URLs
    """
    found_codes = []
    found_deals = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        random_delay(2, 4)
        if is_captcha_page(page):
            print("    ⚠️  CAPTCHA on site — skipping")
            return [], []

        # ---------- NAYA: Clipboard permission + overlays hatana ----------
        try:
            origin = "/".join(page.url.split("/")[:3])
            page.context.grant_permissions(
                ["clipboard-read", "clipboard-write"], origin=origin)
        except Exception:
            pass
        dismiss_overlays(page)

        # Scroll — lazy content
        try:
            for _ in range(4):
                page.mouse.wheel(0, 1000)
                random_delay(0.8, 1.5)
        except Exception:
            pass

        # ---------- LAYER 1: HTML se ----------
        try:
            html = page.content()
            found_codes.extend(mine_from_html(html))
        except Exception:
            pass

        # ---------- LAYER 2: Visible text ----------
        try:
            text = page.inner_text("body")
            found_codes.extend(mine_from_text(text))
        except Exception:
            pass

        # ---------- LAYER 3: Buttons ----------
        btn_codes, btn_deals = click_all_buttons(page, url, brand, region)
        found_codes.extend(btn_codes)
        found_deals.extend(btn_deals)
    except Exception as e:
        print(f"    ⚠️  Page error: {e}")
    return clean_coupons(found_codes), found_deals


# ---------- Layer 1: HTML mining ----------
def mine_from_html(html):
    codes = []
    # data attributes
    for attr in ["data-code", "data-clipboard-text", "data-coupon-code",
                 "data-promo-code", "data-voucher-code"]:
        for m in re.findall(attr + r'=["\']([^"\']+)["\']', html):
            if looks_like_code(m):
                codes.append({"code": m, "method": "html_attr"})
    # JSON values: "code":"EXTRA100"
    for m in re.findall(r'["\'](?:code|couponCode|promoCode|voucherCode)["\']\s*[:=]\s*["\']([^"\']+)["\']', html):
        if looks_like_code(m):
            codes.append({"code": m, "method": "html_json"})
    return codes


# ---------- Layer 2: Text mining ----------
def mine_from_text(text, require_digit=True):
    codes = []
    if not text:
        return codes
    candidates = re.findall(r'\b[A-Za-z][A-Za-z0-9-_]{3,19}\b', text)
    for cand in candidates:
        # NAYA: poore page ke text mein sirf wo codes jin mein digit ho
        # (garbage jaise T-SHIRT, NEWSLETTERS khatam)
        if require_digit and not re.search(r'\d', cand):
            continue
        if code_score(cand) >= 2:
            codes.append({"code": cand, "method": "text"})
    return codes


def code_score(text):
    score = 0
    if re.search(r'[A-Z]', text):
        score += 1
    if re.search(r'[0-9]', text):
        score += 2
    if "-" in text or "_" in text:
        score += 1
    letters = [c for c in text if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7:
        score += 1
    return score


# ---------- NAYA: Overlays / cookie banners hatana ----------
def dismiss_overlays(page):
    sels = [
        "button:has-text('Accept All')", "button:has-text('Accept')",
        "button:has-text('Allow All')", "button:has-text('Got It')",
        "button:has-text('No Thanks')", "button:has-text('Close')",
        "button:has-text('Dismiss')", "[aria-label*='lose']",
        "[class*='cookie'] button", "[id*='consent'] button",
        "[class*='consent'] button",
    ]
    for sel in sels:
        try:
            for el in page.query_selector_all(sel):
                try:
                    if el.is_visible():
                        el.click(timeout=1000)
                        random_delay(0.3, 0.6)
                except Exception:
                    continue
        except Exception:
            pass


# ---------- NAYA: Clipboard se code parhna ----------
def read_clipboard(page):
    try:
        txt = page.evaluate("() => navigator.clipboard.readText()")
        if txt:
            return txt.strip()
    except Exception:
        pass
    return None


# ---------- NAYA: Click ke BAAD code dhoondna ----------
MODAL_SELECTORS = [
    "[role='dialog']", "[class*='modal']", "[class*='Modal']",
    "[class*='popup']", "[class*='Popup']", "[class*='overlay']",
    "[class*='coupon']", "[class*='Coupon']", "[class*='reveal']",
    "[class*='clipboard']", "[id*='coupon']",
]


def extract_after_click(page, before_text):
    codes = []
    # 1) Clipboard (Copy Code buttons)
    clip = read_clipboard(page)
    if clip:
        for part in re.split(r'\s+', clip):
            if looks_like_code(part):
                codes.append({"code": part, "method": "clipboard"})
    # 2) Modal / popup ka visible text (yahan digit rule nahi)
    for sel in MODAL_SELECTORS:
        try:
            for el in page.query_selector_all(sel):
                try:
                    if el.is_visible():
                        codes.extend(mine_from_text(el.inner_text(), require_digit=False))
                except Exception:
                    continue
        except Exception:
            pass
    # 3) HTML dobara mine karo (input value + data-code click ke baad aata ha)
    try:
        html = page.content()
        codes.extend(mine_from_html(html))
        for m in re.findall(r'<input[^>]+value=["\']([^"\']+)["\']', html):
            if looks_like_code(m):
                codes.append({"code": m, "method": "input_value"})
    except Exception:
        pass
    # 4) Sirf NAYA text (diff) — garbage kam, code zyada
    try:
        after_text = page.inner_text("body")
        new_words = set(after_text.split()) - set(before_text.split())
        if new_words:
            codes.extend(mine_from_text(" ".join(new_words), require_digit=False))
    except Exception:
        pass
    return codes


# ---------- Layer 3: Buttons (FIXED) ----------
REVEAL_WORDS = [
    "show code", "get code", "reveal code", "view code", "copy code",
    "see code", "reveal", "show coupon", "get coupon", "show discount",
    "get discount", "show voucher", "get voucher", "copy",
    "click to reveal", "tap to reveal", "unmask", "unlock",
]
DEAL_WORDS = [
    "get deal", "get offer", "shop now", "go to store", "get reward",
    "activate", "use deal", "claim", "get discount", "shop",
]


def click_all_buttons(page, page_url, brand, region):
    codes = []
    deals = []
    try:
        buttons = page.query_selector_all("button, a, [role='button']")
    except Exception:
        buttons = []

    clicked = 0
    seen_deal_urls = set()

    for btn in buttons:
        if clicked >= 30:
            break
        try:
            txt = (btn.inner_text() or "").strip()
        except Exception:
            continue
        if not txt or len(txt) > 40:
            continue
        low = txt.lower()

        is_reveal = any(w in low for w in REVEAL_WORDS)
        is_deal = any(w in low for w in DEAL_WORDS)
        if not is_reveal and not is_deal:
            continue

        # ---------- REVEAL BUTTON ----------
        if is_reveal:
            before_text = ""
            try:
                before_text = page.inner_text("body")
            except Exception:
                pass

            pages_before = len(page.context.pages)
            try:
                btn.scroll_into_view_if_needed()
                btn.click(timeout=5000)
                clicked += 1
                random_delay(1.5, 2.5)
            except Exception:
                # Click block hua (overlay?) — overlays hatao, retry
                dismiss_overlays(page)
                try:
                    btn.click(timeout=3000)
                    clicked += 1
                    random_delay(1.5, 2.5)
                except Exception:
                    continue

            # Naya tab khula? (kai sites code naye tab mein dikhati hain)
            if len(page.context.pages) > pages_before:
                new_page = page.context.pages[-1]
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=8000)
                    codes.extend(mine_from_html(new_page.content()))
                    codes.extend(mine_from_text(new_page.inner_text("body")))
                except Exception:
                    pass
                try:
                    new_page.close()
                except Exception:
                    pass

            codes.extend(extract_after_click(page, before_text))
            close_popups(page)

        # ---------- DEAL BUTTON ----------
        elif is_deal:
            try:
                href = btn.get_attribute("href")
                if href and href.startswith("http") and href not in seen_deal_urls:
                    seen_deal_urls.add(href)
                    deals.append({"url": href, "label": txt})
                    save_deal_row(brand, region, page_url, href, txt)
                    print(f"       🔗 DEAL: {txt} → {href[:50]}")
            except Exception:
                continue

    return clean_coupons(codes), deals


def close_popups(page):
    try:
        for sel in ["[class*='close']", "[aria-label*='lose']"]:
            for x in page.query_selector_all(sel):
                try:
                    if x.is_visible():
                        x.click()
                        random_delay(0.5, 1)
                        return
                except Exception:
                    continue
    except Exception:
        pass


# ============================================
# VALIDATION
# ============================================
BAD_WORDS = {
    "code", "codes", "coupon", "coupons", "click", "here", "shop",
    "sale", "sales", "free", "cart", "shipping", "delivery", "today",
    "http", "https", "www", "com", "org", "net", "get", "use", "the",
    "and", "for", "with", "off", "all", "new", "best", "top", "must",
    "tion", "tions", "cookies", "cookie", "kitchen", "save", "savings",
    "deal", "deals", "offer", "offers", "verified", "copy", "copied",
    "apply", "applied", "details", "screenshot", "login", "sign",
    "submit", "search", "home", "menu", "blog", "news", "read", "more",
    "less", "view", "show", "reveal", "hidden", "expires", "expired",
    "ends", "left", "only", "select", "items", "site", "sitewide",
    "orders", "order", "checkout", "total", "price", "buy", "now",
    "last", "ago", "updated", "users", "user", "interested",
    "august", "september", "october", "november", "december",
    "january", "february", "march", "april", "june", "july",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "terms", "conditions", "privacy",
    "policy", "about", "contact", "support", "help", "faq", "faqs",
    "dyson", "cafe", "appliances", "appliance", "cashback", "cash",
    "back", "reward", "rewards", "exclusive", "limited", "time",
    "days", "day", "activate", "claim", "alert", "alerts", "email",
    "enter", "instantly", "popular", "trending", "stores", "browse",
    "categories", "category", "brand", "brands", "summary", "highlights",
    "average", "rating", "ratings", "stars", "live",
}


def looks_like_code(text):
    if not text:
        return False
    text = text.strip()
    if len(text) < 4 or len(text) > 20:
        return False
    if " " in text:
        return False
    if "*" in text:
        return False
    if not re.match(r'^[A-Za-z0-9\-_]+$', text):
        return False
    if text.lower() in BAD_WORDS:
        return False
    if text.isalpha():
        if text.isupper() and len(text) <= 8:
            return False
        if text.islower():
            return False
    return True


def clean_coupons(coupons):
    seen = set()
    clean = []
    for c in coupons:
        code = c["code"].strip().upper()
        if code not in seen and looks_like_code(code):
            seen.add(code)
            c["code"] = code
            clean.append(c)
    return clean


# ============================================
# STORAGE
# ============================================
def is_duplicate(brand, code, seen_codes):
    return code.upper() in seen_codes.get(brand.lower(), [])


def mark_as_seen(brand, code, seen_codes):
    b = brand.lower()
    c = code.upper()
    seen_codes.setdefault(b, [])
    if c not in seen_codes[b]:
        seen_codes[b].append(c)


# ============================================
# UTILS
# ============================================
def random_delay(a, b):
    time.sleep(random.uniform(a, b))


# ============================================
# MAIN
# ============================================
def main():
    print("=" * 50)
    print("🎫 COUPON CODE SCRAPER")
    print("=" * 50)
    create_folders()
    brands = load_brands()
    if not brands:
        return
    seen_codes = load_seen_codes()
    total_new = 0
    print(f"🔍 Engine: {config.SEARCH_ENGINE.upper()} | 🌍 {config.REGIONS} | 📋 {len(brands)} brands\n")

    with sync_playwright() as p:
        if config.USE_REAL_CHROME:
            context = p.chromium.launch_persistent_context(
                user_data_dir="chrome_profile",
                executable_path=config.CHROME_PATH,
                headless=config.HEADLESS,
                slow_mo=config.SLOW_MO,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = p.chromium.launch(headless=config.HEADLESS, slow_mo=config.SLOW_MO)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            page = context.new_page()

        for i, brand in enumerate(brands, 1):
            print(f"\n🏷️  [{i}/{len(brands)}] {brand}")
            count = 0
            for region in config.REGIONS:
                urls = search_for_coupons(page, brand, region)
                print(f"  🌍 {region.upper()}: {len(urls)} sites visit hongi")
                for j, url in enumerate(urls, 1):
                    print(f"    🌐 [{j}/{len(urls)}] {url[:60]}")
                    codes, deals = extract_codes_from_page(page, url, brand, region)
                    for coupon in codes:
                        if not is_duplicate(brand, coupon["code"], seen_codes):
                            mark_as_seen(brand, coupon["code"], seen_codes)
                            save_result_row({
                                "brand": brand,
                                "code": coupon["code"],
                                "source_url": url,
                                "region": region,
                                "method": coupon["method"],
                                "found_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            })
                            count += 1
                            print(f"       🎫 {brand.upper()}: {coupon['code']} ({coupon['method']})")
                    random_delay(1, 3)
            print(f"  → {count} new codes ✅" if count else "  → 0 codes ❌")
            total_new += count

        context.close()

    save_seen_codes(seen_codes)
    print(f"\n{'=' * 50}")
    print(f"✅ DONE! Total new codes: {total_new}")
    print(f"📁 Codes: {config.OUTPUT_CSV}")
    print(f"📁 Deals: {config.DEALS_CSV}")
    print(f"📁 URLs: {config.URLS_CSV}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()