"""
Coupon Code Scraper - UK batch
Usage: python scraper.py
"""
import os
import re
import csv
import json
import time
import random
from datetime import datetime
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright
import config


# ============================================
# SETUP / STORAGE
# ============================================
def create_folders():
    os.makedirs("data", exist_ok=True)


def load_brands():
    if not os.path.exists("brands.txt"):
        print("❌ brands.txt nahi mila!")
        return []
    with open("brands.txt", "r", encoding="utf-8") as f:
        brands = [line.strip() for line in f if line.strip()]
    print(f"✅ {len(brands)} brands loaded")
    return brands


def load_seen_codes():
    if os.path.exists(config.SEEN_CODES_FILE):
        try:
            with open(config.SEEN_CODES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_seen_codes(seen_codes):
    os.makedirs("data", exist_ok=True)
    with open(config.SEEN_CODES_FILE, "w", encoding="utf-8") as f:
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
    q = quote_plus(query)
    if config.SEARCH_ENGINE == "bing":
        cc = "gb" if region == "uk" else region
        start = (page_num - 1) * 10 + 1
        return f"https://www.bing.com/search?q={q}&cc={cc}&first={start}"

    country = "gb" if region == "uk" else region
    start = (page_num - 1) * 10
    return f"https://www.google.com/search?q={q}&gl={country}&hl=en&start={start}"


def search_for_coupons(page, brand, region):
    queries = [
        f"{brand} coupon code",
        f"{brand} discount code",
        f"{brand} voucher code",
    ]
    all_urls = []

    for query in queries:
        print(f"  🔍 {query}")
        for page_num in range(1, config.SEARCH_PAGES + 1):
            try:
                page.goto(build_search_url(query, region, page_num), wait_until="domcontentloaded", timeout=20000)
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
                print(f"     ⚠️ Search error: {e}")

    return filter_coupon_urls(all_urls)[: config.MAX_SITES_PER_BRAND]


def extract_search_links(page):
    links = []
    try:
        if config.SEARCH_ENGINE == "bing":
            elements = page.query_selector_all("li.b_algo h2 a")
            for el in elements:
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

    return unique_list(links)


def filter_coupon_urls(urls):
    skip_domains = [
        "youtube.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
        "wikipedia.org", "reddit.com", "pinterest.com", "tiktok.com",
        "google.com", "bing.com",
    ]
    coupon_indicators = [
        "coupon", "deal", "discount", "promo", "voucher", "offer",
        "retailmenot", "honey", "groupon", "couponfollow",
        "vouchercodes", "hotukdeals", "dealspotr", "savings",
        "couponcabin", "offers.com",
    ]

    priority = []
    normal = []
    for url in unique_list(urls):
        low = url.lower()
        if any(domain in low for domain in skip_domains):
            continue
        if any(word in low for word in coupon_indicators):
            priority.append(url)
        else:
            normal.append(url)
    return priority + normal


# ============================================
# CAPTCHA / PAGE HELPERS
# ============================================
def is_captcha_page(page):
    try:
        current_url = page.url.lower()
        if "/sorry/" in current_url or "captcha" in current_url:
            return True
        text = page.inner_text("body").lower()
        indicators = [
            "unusual traffic", "verify you are human", "are you a robot",
            "before you continue", "complete the security check",
        ]
        return any(item in text for item in indicators)
    except Exception:
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
            print(f"  ✅ CAPTCHA solved ({waited}s)")
            random_delay(2, 3)
            return
    print("  ⚠️ CAPTCHA timeout — skip kar rahe hain")


def dismiss_overlays(page):
    selectors = [
        "button:has-text('Accept All')", "button:has-text('Accept')",
        "button:has-text('Allow All')", "button:has-text('Got It')",
        "button:has-text('No Thanks')", "button:has-text('Close')",
        "button:has-text('Dismiss')", "[aria-label*='lose']",
        "[class*='cookie'] button", "[id*='consent'] button",
        "[class*='consent'] button",
    ]
    for selector in selectors:
        try:
            for el in page.query_selector_all(selector):
                try:
                    if el.is_visible():
                        el.click(timeout=1000)
                        random_delay(0.2, 0.5)
                except Exception:
                    pass
        except Exception:
            pass


def close_popups(page):
    for selector in ["[class*='close']", "[aria-label*='lose']"]:
        try:
            for el in page.query_selector_all(selector):
                try:
                    if el.is_visible():
                        el.click(timeout=1000)
                        return
                except Exception:
                    pass
        except Exception:
            pass


def read_clipboard(page):
    try:
        value = page.evaluate("() => navigator.clipboard.readText()")
        return (value or "").strip()
    except Exception:
        return ""


# ============================================
# CODE EXTRACTION
# ============================================
BAD_WORDS = {
    "code", "codes", "coupon", "coupons", "click", "here", "shop", "sale",
    "sales", "free", "cart", "shipping", "delivery", "today", "http", "https",
    "www", "com", "org", "net", "get", "use", "the", "and", "for", "with",
    "off", "all", "new", "best", "top", "cookies", "cookie", "save", "savings",
    "deal", "deals", "offer", "offers", "verified", "copy", "copied", "apply",
    "applied", "details", "login", "submit", "search", "home", "menu", "blog",
    "news", "read", "more", "less", "view", "show", "reveal", "hidden",
    "expires", "expired", "ends", "only", "select", "items", "site", "sitewide",
    "orders", "order", "checkout", "total", "price", "buy", "now", "updated",
    "terms", "conditions", "privacy", "policy", "about", "contact", "support",
    "help", "cashback", "reward", "rewards", "exclusive", "limited", "activate",
    "claim", "email", "enter", "popular", "trending", "stores", "browse",
    "categories", "category", "brand", "brands", "rating", "ratings", "live",
}


def looks_like_code(text, allow_alpha=False):
    if not text:
        return False
    value = text.strip()
    if not 4 <= len(value) <= 20:
        return False
    if " " in value or "*" in value:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return False
    if value.lower() in BAD_WORDS:
        return False

    if value.isalpha():
        # Plain page text is noisy, but reveal/modal/clipboard may contain valid
        # alpha-only coupon codes such as WELCOME.
        if not allow_alpha:
            return False
        if not value.isupper():
            return False

    return True


def mine_from_html(html):
    found = []
    for attr in ["data-code", "data-clipboard-text", "data-coupon-code", "data-promo-code", "data-voucher-code"]:
        pattern = attr + r'=["\']([^"\']+)["\']'
        for match in re.findall(pattern, html, re.I):
            if looks_like_code(match, allow_alpha=True):
                found.append({"code": match, "method": "html_attr"})

    json_pattern = r'["\'](?:code|couponCode|promoCode|voucherCode)["\']\s*[:=]\s*["\']([^"\']+)["\']'
    for match in re.findall(json_pattern, html, re.I):
        if looks_like_code(match, allow_alpha=True):
            found.append({"code": match, "method": "html_json"})
    return found


def mine_from_text(text, allow_alpha=False):
    if not text:
        return []
    found = []
    for candidate in re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{3,19}\b", text):
        if looks_like_code(candidate, allow_alpha=allow_alpha):
            found.append({"code": candidate, "method": "text"})
    return found


MODAL_SELECTORS = [
    "[role='dialog']", "[class*='modal']", "[class*='Modal']",
    "[class*='popup']", "[class*='Popup']", "[class*='overlay']",
    "[class*='coupon']", "[class*='Coupon']", "[class*='reveal']",
    "[class*='clipboard']", "[id*='coupon']",
]

REVEAL_WORDS = [
    "show code", "get code", "reveal code", "view code", "copy code",
    "see code", "reveal", "show coupon", "get coupon", "show discount",
    "get discount", "show voucher", "get voucher", "copy", "click to reveal",
    "tap to reveal", "unmask", "unlock",
]

DEAL_WORDS = [
    "get deal", "get offer", "shop now", "go to store", "get reward",
    "activate", "use deal", "claim", "shop",
]


def extract_after_click(page, before_text):
    found = []

    clipboard = read_clipboard(page)
    if clipboard:
        for part in re.split(r"\s+", clipboard):
            if looks_like_code(part, allow_alpha=True):
                found.append({"code": part, "method": "clipboard"})

    for selector in MODAL_SELECTORS:
        try:
            for el in page.query_selector_all(selector):
                if el.is_visible():
                    for item in mine_from_text(el.inner_text(), allow_alpha=True):
                        item["method"] = "modal"
                        found.append(item)
        except Exception:
            pass

    try:
        html = page.content()
        found.extend(mine_from_html(html))
        for value in re.findall(r'<input[^>]+value=["\']([^"\']+)["\']', html, re.I):
            if looks_like_code(value, allow_alpha=True):
                found.append({"code": value, "method": "input_value"})
    except Exception:
        pass

    try:
        after_text = page.inner_text("body")
        new_words = set(after_text.split()) - set(before_text.split())
        for item in mine_from_text(" ".join(new_words), allow_alpha=True):
            item["method"] = "revealed_text"
            found.append(item)
    except Exception:
        pass

    return found


def click_reveal_buttons(page, page_url, brand, region):
    found = []
    deals = []
    seen_deal_urls = set()

    try:
        buttons = page.query_selector_all("button, a, [role='button']")
    except Exception:
        return found, deals

    clicked = 0
    for btn in buttons:
        if clicked >= 30:
            break
        try:
            label = (btn.inner_text() or "").strip()
        except Exception:
            continue
        if not label or len(label) > 50:
            continue

        low = label.lower()
        is_reveal = any(word in low for word in REVEAL_WORDS)
        is_deal = any(word in low for word in DEAL_WORDS)
        if not is_reveal and not is_deal:
            continue

        if is_deal and not is_reveal:
            try:
                href = btn.get_attribute("href")
                if href and href.startswith("http") and href not in seen_deal_urls:
                    seen_deal_urls.add(href)
                    deals.append({"url": href, "label": label})
                    save_deal_row(brand, region, page_url, href, label)
            except Exception:
                pass
            continue

        try:
            before_text = page.inner_text("body")
        except Exception:
            before_text = ""

        pages_before = len(page.context.pages)
        try:
            btn.scroll_into_view_if_needed()
            btn.click(timeout=5000)
            clicked += 1
            random_delay(1.5, 2.5)
        except Exception:
            dismiss_overlays(page)
            try:
                btn.click(timeout=3000)
                clicked += 1
                random_delay(1.5, 2.5)
            except Exception:
                continue

        # Coupon sites often open the merchant in a new tab while revealing the
        # code on the original tab. Read any useful content, then close new tabs.
        if len(page.context.pages) > pages_before:
            for new_page in page.context.pages[pages_before:]:
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=8000)
                    found.extend(mine_from_html(new_page.content()))
                except Exception:
                    pass
                try:
                    new_page.close()
                except Exception:
                    pass

        found.extend(extract_after_click(page, before_text))
        close_popups(page)

    return found, deals


def extract_codes_from_page(page, url, brand, region):
    found = []
    deals = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        random_delay(2, 4)
        if is_captcha_page(page):
            print("    ⚠️ CAPTCHA on coupon site — skipping")
            return [], []

        try:
            origin = "/".join(page.url.split("/")[:3])
            page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
        except Exception:
            pass

        dismiss_overlays(page)
        try:
            for _ in range(4):
                page.mouse.wheel(0, 1000)
                random_delay(0.8, 1.3)
        except Exception:
            pass

        try:
            found.extend(mine_from_html(page.content()))
        except Exception:
            pass
        try:
            # Plain visible text only accepts codes containing digits/symbols.
            found.extend(mine_from_text(page.inner_text("body"), allow_alpha=False))
        except Exception:
            pass

        button_codes, button_deals = click_reveal_buttons(page, url, brand, region)
        found.extend(button_codes)
        deals.extend(button_deals)
    except Exception as e:
        print(f"    ⚠️ Page error: {e}")

    return clean_coupons(found), deals


def clean_coupons(coupons):
    clean = []
    seen = set()
    for item in coupons:
        code = str(item.get("code", "")).strip().upper()
        if not code or code in seen:
            continue
        # At this stage alpha-only codes are allowed because HTML/reveal methods
        # may legitimately return them.
        if not looks_like_code(code, allow_alpha=True):
            continue
        seen.add(code)
        clean.append({"code": code, "method": item.get("method", "unknown")})
    return clean


def is_duplicate(brand, code, seen_codes):
    return code.upper() in seen_codes.get(brand.lower(), [])


def mark_as_seen(brand, code, seen_codes):
    brand_key = brand.lower()
    code_value = code.upper()
    seen_codes.setdefault(brand_key, [])
    if code_value not in seen_codes[brand_key]:
        seen_codes[brand_key].append(code_value)


def unique_list(values):
    seen = set()
    output = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def random_delay(a, b):
    time.sleep(random.uniform(a, b))


# ============================================
# MAIN
# ============================================
def main():
    print("=" * 55)
    print("🎫 COUPON CODE SCRAPER - UK")
    print("=" * 55)
    create_folders()
    brands = load_brands()
    if not brands:
        return

    seen_codes = load_seen_codes()
    total_new = 0
    browser_locale = "en-GB" if "uk" in config.REGIONS else "en-US"
    print(f"🔍 Engine: {config.SEARCH_ENGINE.upper()} | 🌍 {config.REGIONS} | 📋 {len(brands)} brands")
    print(f"🤖 AI: OFF | Locale: {browser_locale}\n")

    with sync_playwright() as p:
        if config.USE_REAL_CHROME:
            context = p.chromium.launch_persistent_context(
                user_data_dir="chrome_profile",
                executable_path=config.CHROME_PATH,
                headless=config.HEADLESS,
                slow_mo=config.SLOW_MO,
                viewport={"width": 1920, "height": 1080},
                locale=browser_locale,
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = p.chromium.launch(headless=config.HEADLESS, slow_mo=config.SLOW_MO)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale=browser_locale,
            )
            page = context.new_page()

        for index, brand in enumerate(brands, 1):
            print(f"\n🏷️ [{index}/{len(brands)}] {brand}")
            brand_count = 0

            for region in config.REGIONS:
                urls = search_for_coupons(page, brand, region)
                print(f"  🌍 {region.upper()}: {len(urls)} coupon sites visit hongi")

                for site_index, url in enumerate(urls, 1):
                    print(f"    🌐 [{site_index}/{len(urls)}] {url[:75]}")
                    codes, _ = extract_codes_from_page(page, url, brand, region)
                    for coupon in codes:
                        code = coupon["code"]
                        if is_duplicate(brand, code, seen_codes):
                            continue

                        mark_as_seen(brand, code, seen_codes)
                        save_result_row({
                            "brand": brand,
                            "code": code,
                            "source_url": url,
                            "region": region,
                            "method": coupon["method"],
                            "found_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        })
                        brand_count += 1
                        print(f"       🎫 {code} ({coupon['method']})")

                    # Persist progress after every visited source so an interrupted
                    # run can resume without losing already-found codes.
                    save_seen_codes(seen_codes)
                    random_delay(1, 3)

            print(f"  → {brand_count} new codes ✅" if brand_count else "  → 0 codes ❌")
            total_new += brand_count

        context.close()

    save_seen_codes(seen_codes)
    print("\n" + "=" * 55)
    print(f"✅ DONE! Total new codes: {total_new}")
    print(f"📁 Codes: {config.OUTPUT_CSV}")
    print(f"📁 Deals: {config.DEALS_CSV}")
    print(f"📁 URLs: {config.URLS_CSV}")
    print("=" * 55)


if __name__ == "__main__":
    main()
