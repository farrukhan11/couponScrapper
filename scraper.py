"""
Coupon Code Scraper - UK batch
Usage: python scraper.py
"""
import os
import sys
import re
import csv
import json
import time
import random
from datetime import datetime
from urllib.parse import parse_qs, quote_plus, unquote, urlparse, urljoin

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from playwright.sync_api import sync_playwright
import requests
from concurrent.futures import ThreadPoolExecutor
import config

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


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

REVEAL_WORDS = [
    "show code", "get code", "reveal code", "view code", "copy code",
    "see code", "reveal", "show coupon", "get coupon", "show discount",
    "get discount", "show voucher", "get voucher", "copy", "click to reveal",
    "tap to reveal", "unmask", "unlock",
]

MODAL_SELECTORS = [
    "[role='dialog']", "[class*='modal']", "[class*='Modal']",
    "[class*='popup']", "[class*='Popup']", "[class*='overlay']",
    "[class*='coupon']", "[class*='Coupon']", "[class*='reveal']",
    "[class*='clipboard']", "[id*='coupon']",
]


def random_delay(a, b):
    time.sleep(random.uniform(a, b))


def unique_list(values):
    seen = set()
    out = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


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
    if not os.path.exists(config.SEEN_CODES_FILE):
        return {}
    try:
        with open(config.SEEN_CODES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_seen_codes(seen_codes):
    with open(config.SEEN_CODES_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_codes, f, indent=2)


def save_urls(brand, region, page_num, urls):
    if not urls:
        return
    exists = os.path.exists(config.URLS_CSV)
    with open(config.URLS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["brand", "region", "search_page", "url"])
        for url in urls:
            writer.writerow([brand, region, page_num, url])


def save_result(brand, code, source_url, region, method):
    fields = ["brand", "code", "source_url", "region", "method", "found_at"]
    exists = os.path.exists(config.OUTPUT_CSV)
    with open(config.OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({
            "brand": brand,
            "code": code,
            "source_url": source_url,
            "region": region,
            "method": method,
            "found_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })


def build_search_url(query, region, page_num):
    q = quote_plus(query)
    if config.SEARCH_ENGINE == "bing":
        country = "gb" if region == "uk" else region
        first = (page_num - 1) * 10 + 1
        return f"https://www.bing.com/search?q={q}&cc={country}&first={first}"

    country = "gb" if region == "uk" else region
    start = (page_num - 1) * 10
    if start > 0:
        return f"https://www.google.com/search?q={q}&gl={country}&hl=en&start={start}"
    return f"https://www.google.com/search?q={q}&gl={country}&hl=en"


def resolve_redirect_url(url):
    """Fast parallel resolver for Google /goto redirects."""
    if not url.startswith("http"):
        url = urljoin("https://www.google.com", url)
    try:
        r = requests.get(url, headers=REQUEST_HEADERS, timeout=4, stream=True, allow_redirects=True)
        final_url = r.url
        if final_url and final_url.startswith(("http://", "https://")):
            parsed = urlparse(final_url)
            host = parsed.netloc.lower()
            if "google." not in host and "bing.com" not in host:
                return final_url
    except Exception:
        pass
    return None


def extract_search_links(page):
    links = []
    try:
        if config.SEARCH_ENGINE == "bing":
            for el in page.query_selector_all("li.b_algo h2 a"):
                href = el.get_attribute("href")
                if href and href.startswith(("http://", "https://")):
                    links.append(href)
        else:
            raw_hrefs = page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href'))")
            goto_urls = []

            for href in raw_hrefs:
                if not href:
                    continue
                href = href.strip()
                if href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
                    continue

                # 1. Direct google /url? wrapper
                if href.startswith("/url?"):
                    parsed = urlparse(href)
                    params = parse_qs(parsed.query)
                    target = (params.get("q") or params.get("url") or [None])[0]
                    if target:
                        target = unquote(target)
                        if target.startswith(("http://", "https://")):
                            p = urlparse(target)
                            if "google." not in p.netloc.lower() and "bing.com" not in p.netloc.lower():
                                links.append(target)
                    continue

                # 2. Google /goto? wrapper
                if href.startswith("/goto?") or "google.com/goto?" in href or (href.startswith("/") and "goto?" in href):
                    goto_urls.append(href)
                    continue

                if href.startswith("/"):
                    continue

                # 3. Direct external links
                try:
                    p = urlparse(href)
                    host = p.netloc.lower()
                    if "google." in host:
                        if p.path == "/goto":
                            goto_urls.append(href)
                        elif p.path == "/url":
                            params = parse_qs(p.query)
                            target = (params.get("q") or params.get("url") or [None])[0]
                            if target:
                                target = unquote(target)
                                if target.startswith(("http://", "https://")):
                                    p2 = urlparse(target)
                                    if "google." not in p2.netloc.lower():
                                        links.append(target)
                    elif "bing.com" in host:
                        continue
                    elif href.startswith(("http://", "https://")):
                        links.append(href)
                except Exception:
                    continue

            # Resolve all /goto? redirects concurrently in background threads
            if goto_urls:
                unique_gotos = list(set(goto_urls))
                with ThreadPoolExecutor(max_workers=15) as executor:
                    resolved = list(executor.map(resolve_redirect_url, unique_gotos))
                    for r in resolved:
                        if r:
                            links.append(r)

    except Exception:
        pass
    return unique_list(links)


def filter_coupon_urls(urls):
    skip = [
        "youtube.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
        "wikipedia.org", "reddit.com", "pinterest.com", "tiktok.com",
        "google.com", "bing.com",
    ]
    indicators = [
        "coupon", "deal", "discount", "promo", "voucher", "offer",
        "retailmenot", "honey", "groupon", "couponfollow",
        "vouchercodes", "hotukdeals", "dealspotr", "savings",
        "couponcabin", "offers.com",
    ]
    priority, normal = [], []
    for url in unique_list(urls):
        low = url.lower()
        if any(domain in low for domain in skip):
            continue
        (priority if any(word in low for word in indicators) else normal).append(url)
    return priority + normal


def is_captcha_page(page):
    try:
        url = page.url.lower()
        if "/sorry/" in url:
            # Check if captcha form or frame is still visible
            has_captcha_el = page.query_selector("form#captcha-form, iframe[src*='recaptcha'], div#recaptcha, .g-recaptcha, #captcha")
            if has_captcha_el:
                return True
            body = page.inner_text("body").lower()
            indicators = ["unusual traffic", "verify you are human", "are you a robot", "complete the security check"]
            return any(item in body for item in indicators)
        return False
    except Exception:
        return False


def wait_for_captcha(page, target_url=None):
    if not is_captcha_page(page):
        return
    print("  🛑 CAPTCHA detected! Browser mein manually solve karo...")
    waited = 0
    while waited < config.CAPTCHA_TIMEOUT:
        time.sleep(config.CAPTCHA_CHECK)
        waited += config.CAPTCHA_CHECK
        if not is_captcha_page(page):
            print(f"  ✅ CAPTCHA solved ({waited}s)")
            random_delay(1, 2)
            if target_url and ("/sorry/" in page.url.lower() or "google.com" in page.url.lower() and "/search" not in page.url.lower()):
                page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
            return
    print("  ⚠️ CAPTCHA timeout")


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
                search_url = build_search_url(query, region, page_num)
                page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                random_delay(1, 2)
                wait_for_captcha(page, search_url)
                try:
                    page.wait_for_selector(
                        "#search a[href], h3, [role='heading']",
                        timeout=5000,
                    )
                except Exception:
                    pass
                links = extract_search_links(page)
                print(f"     Page {page_num}: {len(links)} links")
                save_urls(brand, region, page_num, links)
                all_urls.extend(links)
                random_delay(config.MIN_DELAY, config.MAX_DELAY)
            except Exception as e:
                print(f"     ⚠️ Search error: {e}")
    return filter_coupon_urls(all_urls)[: config.MAX_SITES_PER_BRAND]


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
                except Exception:
                    pass
        except Exception:
            pass


def looks_like_code(value, allow_alpha=False):
    if not value:
        return False
    value = value.strip()
    if not 4 <= len(value) <= 20:
        return False
    if " " in value or "*" in value:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return False
    if value.lower() in BAD_WORDS:
        return False
    if value.isalpha():
        if not allow_alpha:
            return False
        if not value.isupper():
            return False
    return True


def mine_html(html):
    found = []
    attrs = [
        "data-code", "data-clipboard-text", "data-coupon-code",
        "data-promo-code", "data-voucher-code",
    ]
    for attr in attrs:
        pattern = rf"{re.escape(attr)}=[\"']([^\"']+)[\"']"
        for value in re.findall(pattern, html, re.I):
            if looks_like_code(value, allow_alpha=True):
                found.append((value, "html_attr"))

    json_pattern = (
        r"[\"'](?:code|couponCode|promoCode|voucherCode)[\"']"
        r"\s*[:=]\s*[\"']([^\"']+)[\"']"
    )
    for value in re.findall(json_pattern, html, re.I):
        if looks_like_code(value, allow_alpha=True):
            found.append((value, "html_json"))
    return found


def mine_text(text, allow_alpha=False, method="text"):
    found = []
    if not text:
        return found
    for value in re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{3,19}\b", text):
        if looks_like_code(value, allow_alpha=allow_alpha):
            found.append((value, method))
    return found


def read_clipboard(page):
    try:
        return (page.evaluate("() => navigator.clipboard.readText()") or "").strip()
    except Exception:
        return ""


def extract_after_click(page, before_text):
    found = []

    clipboard = read_clipboard(page)
    if clipboard:
        for part in re.split(r"\s+", clipboard):
            if looks_like_code(part, allow_alpha=True):
                found.append((part, "clipboard"))

    for selector in MODAL_SELECTORS:
        try:
            for el in page.query_selector_all(selector):
                if el.is_visible():
                    found.extend(
                        mine_text(el.inner_text(), allow_alpha=True, method="modal")
                    )
        except Exception:
            pass

    try:
        found.extend(mine_html(page.content()))
    except Exception:
        pass

    try:
        after_text = page.inner_text("body")
        new_words = set(after_text.split()) - set(before_text.split())
        found.extend(
            mine_text(
                " ".join(new_words),
                allow_alpha=True,
                method="revealed_text",
            )
        )
    except Exception:
        pass

    return found


def click_reveal_buttons(page):
    found = []
    try:
        buttons = page.query_selector_all("button, a, [role='button']")
    except Exception:
        return found

    clicks = 0
    for btn in buttons:
        if clicks >= 30:
            break
        try:
            label = (btn.inner_text() or "").strip()
        except Exception:
            continue
        if not label or len(label) > 50:
            continue
        if not any(word in label.lower() for word in REVEAL_WORDS):
            continue

        try:
            before_text = page.inner_text("body")
        except Exception:
            before_text = ""

        pages_before = len(page.context.pages)
        try:
            btn.scroll_into_view_if_needed()
            btn.click(timeout=5000)
            clicks += 1
            random_delay(1.5, 2.5)
        except Exception:
            dismiss_overlays(page)
            try:
                btn.click(timeout=3000)
                clicks += 1
                random_delay(1.5, 2.5)
            except Exception:
                continue

        if len(page.context.pages) > pages_before:
            for new_page in page.context.pages[pages_before:]:
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=8000)
                    found.extend(mine_html(new_page.content()))
                except Exception:
                    pass
                try:
                    new_page.close()
                except Exception:
                    pass

        found.extend(extract_after_click(page, before_text))

    return found


def extract_codes_from_page(page, url):
    found = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        random_delay(2, 4)
        if is_captcha_page(page):
            print("    ⚠️ CAPTCHA on coupon site — skipped")
            return []

        try:
            origin = "/".join(page.url.split("/")[:3])
            page.context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin=origin,
            )
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
            found.extend(mine_html(page.content()))
        except Exception:
            pass
        try:
            found.extend(mine_text(page.inner_text("body"), allow_alpha=False))
        except Exception:
            pass

        found.extend(click_reveal_buttons(page))
    except Exception as e:
        print(f"    ⚠️ Page error: {e}")

    clean = []
    seen = set()
    for code, method in found:
        code = code.strip().upper()
        if code in seen:
            continue
        if not looks_like_code(code, allow_alpha=True):
            continue
        seen.add(code)
        clean.append((code, method))
    return clean


def read_urls_from_csv(target_brand, target_region):
    urls = []
    if not os.path.exists(config.URLS_CSV):
        return urls
    with open(config.URLS_CSV, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 4:
                b, r, _, u = row[0].strip(), row[1].strip(), row[2].strip(), row[3].strip()
                if b.lower() == target_brand.lower() and r.lower() == target_region.lower():
                    urls.append(u)
            elif len(row) == 2:
                b, u = row[0].strip(), row[1].strip()
                if b.lower() == target_brand.lower():
                    urls.append(u)
    return filter_coupon_urls(urls)[:config.MAX_SITES_PER_BRAND]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Coupon Code Scraper")
    parser.add_argument("--step1", action="store_true", help="Step 1: Only search for links and save to CSV")
    parser.add_argument("--step2", action="store_true", help="Step 2: Only read links from CSV and visit them")
    args = parser.parse_args()

    print("=" * 55)
    print("🎫 COUPON CODE SCRAPER - UK")
    if args.step1:
        print("🛠️ MODE: STEP 1 (Search Only)")
    elif args.step2:
        print("🛠️ MODE: STEP 2 (Visit Links from CSV)")
    print("=" * 55)

    create_folders()
    brands = load_brands()
    if not brands:
        return

    seen_codes = load_seen_codes()
    locale = "en-GB" if "uk" in config.REGIONS else "en-US"
    total_new = 0

    print(
        f"🔍 {config.SEARCH_ENGINE.upper()} | 🌍 {config.REGIONS} | "
        f"📋 {len(brands)} brands | 🤖 AI OFF | {locale}"
    )

    with sync_playwright() as p:
        if config.USE_REAL_CHROME:
            user_data_dir = getattr(config, "CHROME_USER_DATA_DIR", "chrome_profile")
            profile_args = ["--disable-blink-features=AutomationControlled"]
            if hasattr(config, "CHROME_PROFILE") and config.CHROME_PROFILE:
                profile_args.append(f"--profile-directory={config.CHROME_PROFILE}")

            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                executable_path=config.CHROME_PATH,
                headless=config.HEADLESS,
                slow_mo=config.SLOW_MO,
                viewport={"width": 1920, "height": 1080},
                locale=locale,
                args=profile_args,
            )
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = p.chromium.launch(
                headless=config.HEADLESS,
                slow_mo=config.SLOW_MO,
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale=locale,
            )
            page = context.new_page()

        for index, brand in enumerate(brands, 1):
            print(f"\n🏷️ [{index}/{len(brands)}] {brand}")
            brand_new = 0
            brand_key = brand.lower()
            seen_codes.setdefault(brand_key, [])

            # Agar Step 1 chal raha hai aur is brand ke URLs pehle se CSV mein hain, toh skip karein
            if args.step1:
                existing = read_urls_from_csv(brand, config.REGIONS[0] if config.REGIONS else "uk")
                if len(existing) >= 3:
                    print(f"  ⏩ Pehle se {len(existing)} URLs CSV mein mojood hain. Skipping {brand}...")
                    continue

            for region in config.REGIONS:
                if args.step2:
                    urls = read_urls_from_csv(brand, region)
                    print(f"  📂 STEP 2: {len(urls)} URLs loaded from CSV for {region.upper()}")
                else:
                    urls = search_for_coupons(page, brand, region)

                if args.step1 or getattr(config, "SEARCH_ONLY", False):
                    print(f"  📁 STEP 1: {len(urls)} URLs captured in {config.URLS_CSV}. Stopping here for {brand}.")
                    continue

                print(f"  🌍 {region.upper()}: {len(urls)} sites visit hongi")

                for site_index, url in enumerate(urls, 1):
                    print(f"    🌐 [{site_index}/{len(urls)}] {url[:75]}")
                    for code, method in extract_codes_from_page(page, url):
                        if code in seen_codes[brand_key]:
                            continue
                        seen_codes[brand_key].append(code)
                        save_result(brand, code, url, region, method)
                        save_seen_codes(seen_codes)
                        brand_new += 1
                        print(f"       🎫 {code} ({method})")
                    random_delay(1, 3)

            total_new += brand_new
            print(f"  → {brand_new} new codes")

        context.close()

    save_seen_codes(seen_codes)
    print("\n" + "=" * 55)
    print(f"✅ DONE! Total new codes: {total_new}")
    print(f"📁 Codes: {config.OUTPUT_CSV}")
    print(f"📁 URLs: {config.URLS_CSV}")
    print("=" * 55)


if __name__ == "__main__":
    main()
