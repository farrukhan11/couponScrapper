"""
PAGE SCRAPER — matched brand pages se coupon codes nikalta hai.

Fetch ladder: httpx (fast) → Cloudflare/403 par Real-Chrome in-page fetch.
Mining: HTML attrs + JSON keys + visible text tokens (bad-words filter).

Usage:
  python page_scraper.py "commomy.com" "lgxnds.com" ...     # brands
  python page_scraper.py --file brands.txt
"""
import csv
import html as html_mod
import json
import os
import re
import sys
from datetime import datetime

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brand_matcher import find_matches, load_index

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

RESULTS_CSV = "results_uk.csv"
MAX_URLS_PER_BRAND = 10     # exact pehle, phir partials
MAX_BODY = 3_000_000        # 3MB cap per page

# ============================================================
# MINING (compact port of proven logic)
# ============================================================
BAD_WORDS = {
    "code", "codes", "coupon", "coupons", "click", "here", "shop", "sale",
    "sales", "free", "cart", "shipping", "delivery", "today", "http",
    "https", "www", "get", "use", "the", "and", "for", "with", "off",
    "all", "new", "best", "top", "must", "cookies", "cookie", "save",
    "savings", "deal", "deals", "offer", "offers", "verified", "copy",
    "copied", "apply", "applied", "details", "login", "sign", "submit",
    "search", "home", "menu", "blog", "news", "read", "more", "less",
    "view", "show", "reveal", "hidden", "expires", "expired", "ends",
    "left", "only", "select", "items", "site", "sitewide", "orders",
    "order", "checkout", "total", "price", "buy", "now", "last", "ago",
    "updated", "users", "user", "january", "february", "march", "april",
    "may", "june", "july", "august", "september", "october", "november",
    "december", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "terms", "conditions", "privacy", "policy",
    "about", "contact", "support", "help", "faq", "verified", "active",
    "store", "stores", "brand", "brands", "vouchers", "voucher", "promo",
    "promos", "discount", "discounts", "cashback", "reward", "rewards",
    "exclusive", "limited", "time", "days", "day", "activate", "claim",
    "email", "enter", "popular", "trending", "browse", "categories",
    "category", "summary", "highlights", "average", "rating", "ratings",
    "stars", "live", "expired", "tested", "working", "welcome",
}

ANALYTICS_RE = re.compile(r"^(GTM|UA|AW|DC|G|FB|IG)-?[A-Z0-9]{4,}$", re.I)


def looks_like_code(text):
    if not text:
        return False
    t = text.strip()
    if not (4 <= len(t) <= 20):
        return False
    if " " in t or "*" in t:
        return False
    if not re.match(r"^[A-Za-z0-9\-_]+$", t):
        return False
    if t.lower() in BAD_WORDS:
        return False
    if ANALYTICS_RE.match(t):
        return False
    # digits bhi aur letters bhi = strong
    if re.search(r"\d", t) and re.search(r"[A-Za-z]", t):
        return True
    # sirf uppercase letters (len > 8 toheen)
    if t.isupper() and len(t) >= 9:
        return True
    # mixed-case alpha (Welcome10 jaisa) — digit hona chahiye
    return False


def mine_codes(html_text):
    """Ek page ke HTML se codes: attrs + JSON keys + text tokens."""
    found = {}   # code -> method

    def add(code, method):
        c = code.strip().upper()
        if c and c not in found and looks_like_code(c):
            found[c] = method

    # 1) data-* attributes
    for m in re.findall(
            r'(?:data-code|data-clipboard-text|data-coupon-code|data-promo-code'
            r'|data-voucher-code|data-clipboard)=["\']([^"\']{3,25})["\']', html_text):
        add(m, "html_attr")

    # 2) JSON/JS keys
    for m in re.findall(
            r'["\'](?:code|coupon_code|promo_code|voucher_code|couponCode'
            r'|promoCode|voucherCode|couponcode)["\']\s*[:=]\s*["\']'
            r'([^"\']{3,25})["\']', html_text):
        add(m, "html_json")

    # 3) visible text tokens — HTML tags strip karke
    text = re.sub(r"<script\b.*?</script>", " ", html_text, flags=re.S | re.I)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    for m in re.findall(r"\b[A-Za-z0-9][A-Za-z0-9\-_]{3,19}\b", text):
        if re.search(r"\d", m):
            add(m, "text")

    return [{"code": c, "method": m} for c, m in found.items()]


# ============================================================
# FETCH LADDER
# ============================================================
def fetch_httpx(url):
    try:
        with httpx.Client(http2=True, timeout=25, follow_redirects=True,
                          headers={"User-Agent": UA}) as client:
            r = client.get(url)
            if r.status_code == 200:
                return r.text
            return None
    except Exception:
        return None


def browser_fetch_pages(urls_by_origin):
    """Blocked origins: Real Chrome headful + in-page fetch → HTML text.
    urls_by_origin: {origin: [urls]} → {url: html_text}"""
    out = {}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"])
        except Exception:
            import config
            browser = p.chromium.launch(
                headless=False,
                executable_path=getattr(config, "CHROME_PATH", ""),
                args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        for origin, urls in urls_by_origin.items():
            page = ctx.new_page()
            try:
                page.goto(origin, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
            except Exception:
                pass
            js = """async (url) => {
                const r = await fetch(url, {credentials: 'include'});
                if (!r.ok) return 'STATUS:' + r.status;
                const t = await r.text();
                return t.length > %d ? t.slice(0, %d) : t;
            }""" % (MAX_BODY, MAX_BODY)
            for u in urls:
                try:
                    txt = page.evaluate(js, u)
                    if txt and not txt.startswith("STATUS:"):
                        out[u] = txt
                except Exception:
                    continue
            page.close()
        browser.close()
    return out


def fetch_pages_for_brands(url_list):
    """Saari URLs: httpx pass pehle; blocked origins browser batch mein."""
    html_map = {}
    blocked = {}
    for url in url_list:
        origin = "/".join(url.split("/")[:3])
        txt = fetch_httpx(url)
        if txt:
            html_map[url] = txt
        else:
            blocked.setdefault(origin, []).append(url)
    if blocked:
        print(f"   🌐 Browser fallback: {sum(len(v) for v in blocked.values())} URLs "
              f"({len(blocked)} origins)")
        html_map.update(browser_fetch_pages(blocked))
    return html_map


# ============================================================
# MAIN PIPELINE
# ============================================================
def run(brands):
    index = load_index()
    print(f"🗂️  Index: {len(index)} sites, {sum(len(m) for m in index.values())} stores\n")

    # ---- match + URL list ----
    plan = {}   # brand -> [(tier, url)]
    for b in brands:
        hits = find_matches(b, index)
        tier_rank = {"exact": 0, "prefix": 1, "contains": 2, "fuzzy": 3}
        urls = sorted(hits.values(), key=lambda h: (tier_rank.get(h["tier"], 9), -len(h["slug"])))
        plan[b] = [(h["tier"], h["url"]) for h in urls[:MAX_URLS_PER_BRAND]]
        n_exact = sum(1 for t, _ in plan[b] if t == "exact")
        print(f"🏷️  {b}: {len(plan[b])} URLs ({n_exact} exact)")

    all_urls = []
    seen = set()
    for b, lst in plan.items():
        for _, u in lst:
            if u not in seen:
                seen.add(u)
                all_urls.append(u)

    print(f"\n🌐 Fetching {len(all_urls)} pages (httpx → browser fallback)...")
    html_map = fetch_pages_for_brands(all_urls)
    print(f"   ✅ {len(html_map)}/{len(all_urls)} pages mile")

    # ---- mine per brand ----
    rows = []
    print()
    for b, lst in plan.items():
        codes = {}
        got = 0
        for tier, u in lst:
            html_text = html_map.get(u)
            if not html_text:
                continue
            got += 1
            for c in mine_codes(html_text):
                if c["code"] not in codes:
                    codes[c["code"]] = {"method": c["method"], "url": u}
        print(f"🎫 {b}: {len(codes)} codes ({got}/{len(lst)} pages)")
        for c, info in codes.items():
            rows.append({"brand": b, "code": c, "method": info["method"],
                         "source_url": info["url"]})

    # ---- save ----
    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["brand", "code", "method", "source_url"])
        w.writeheader()
        w.writerows(rows)
    total = len(rows)
    print(f"\n{'=' * 50}")
    print(f"✅ DONE: {total} codes → {RESULTS_CSV}")
    return rows


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.strip() and not a.startswith("--")]
    if "--file" in sys.argv and len(sys.argv) > sys.argv.index("--file") + 1:
        fn = sys.argv[sys.argv.index("--file") + 1]
        with open(fn, "r", encoding="utf-8-sig") as f:
            args = [l.strip() for l in f if l.strip()]
    if not args:
        print('Usage: python page_scraper.py "commomy.com" ... | --file brands.txt')
        sys.exit(1)
    run(args)
