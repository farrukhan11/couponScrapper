"""
PAGE SCRAPER v2 — crawl4ai integrated.

Matched brand pages se coupon codes:
  Phase 1  httpx (fast, raw HTML) → static mining
           (403/challenge par Real-Chrome in-page fetch fallback)
  Phase 2  crawl4ai (async, stealth) + REVEAL_JS — khud reveal buttons
           click karta hai (single "reveal all" ya per-coupon), phir final
           rendered HTML → mining. Blocked pages real-chrome fallback.

Mining: HTML attrs + JSON/JS keys + text tokens (bad-words filter).

Usage:
  python page_scraper.py "commomy.com" "lgxnds.com" ...     # brands
  python page_scraper.py --file brands.txt
"""
import asyncio
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
REVEAL_SKIP_THRESHOLD = 12  # itne codes static se mil gaye to crawl4ai skip

# ============================================================
# MINING
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
    "stars", "live", "tested", "working", "welcome", "expired",
}

ANALYTICS_RE = re.compile(r"^(GTM|UA|AW|DC|G|FB|IG)-?[A-Z0-9]{4,}$", re.I)

# Text-mined junk patterns (testimonial usernames, CSS/unit tokens)
TEXT_NOISE_RES = [
    re.compile(r"^[A-Z]{7,}\d{3,}$"),                       # SHREWDSEEKER5445
    re.compile(r"\d+-(DAY|DAYS|STAR|STARS|SECOND|SECONDS|HR|HRS|HOUR|HOURS|MIN|MINUTE|MINUTES)$", re.I),
    re.compile(r"^\d{2,6}(PX|K|M|CM|MM)$", re.I),           # 390PX, 647K, 250K
    re.compile(r"^(ROW|COL|GRID|CELL)\d+$", re.I),          # ROW1, COL2
    re.compile(r"^[A-Z0-9]{2,}-[A-Z0-9]{2,}$"),             # 30-SECOND
    re.compile(r"^9[A-Z]99[A-Z]9[A-Z]$", re.I),             # 9P99P9P
]

# Text-mined coupon code ka typical shape: letter se shuru, 4-12 alnum, koi
# hyphen/underscore nahi. (UUID/CSS/timestamp/entity junk isse reject hote hain)
TEXT_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{3,11}$")
ENTITY_RE = re.compile(r"^U00[0-9A-F]", re.I)


def _text_noise(t):
    return any(r.match(t) for r in TEXT_NOISE_RES)


def _strict_text_code(t):
    """Sirf text-mining ke liye sakht validation (junk families block)."""
    if not TEXT_CODE_RE.match(t):
        return False
    if ENTITY_RE.match(t) or _text_noise(t):
        return False
    if not re.search(r"\d", t):                 # code mein digit zaroori
        return False
    letters = sum(1 for c in t if c.isalpha())
    if letters / len(t) < 0.4:                  # mostly digits = junk
        return False
    if len(t) >= 6 and all(c in "ABCDEF0123456789" for c in t):
        return False                            # hex hash fragment
    return True


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
    if re.search(r"\d", t) and re.search(r"[A-Za-z]", t):
        return True
    if t.isupper() and len(t) >= 9:
        return True
    return False


def mine_codes(html_text):
    """Ek page se codes. Priority: attrs/JSON (site ke declared codes).
    Text tokens tab hi jab wo kaafi hon — junk pattern filter ke saath."""
    found = {}   # code -> method

    def add(code, method):
        c = code.strip().upper()
        if not c or c in found or not looks_like_code(c):
            return
        if method == "text" and _text_noise(c):
            return
        found[c] = method

    # 1) data-* attributes (authoritative)
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

    strong = len(found)
    if strong < 1:
        # 3) text tokens — sirf jab attrs/JSON na milay (strict validation)
        text = re.sub(r"<script\b.*?</script>", " ", html_text, flags=re.S | re.I)
        text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_mod.unescape(text)
        for m in re.findall(r"\b[A-Za-z0-9][A-Za-z0-9\-_]{3,19}\b", text):
            if re.search(r"\d", m) and _strict_text_code(m.upper()):
                add(m, "text")

    return [{"code": c, "method": m} for c, m in found.items()]


def _is_challenge_page(html):
    head = (html or "")[:4000].lower()
    return ("just a moment" in head or "attention required" in head
            or "incapsula" in head or "you have been blocked" in head
            or "access denied" in head)


# ============================================================
# BRAND RELEVANCE + COMPETITOR-SECTION GUARDS
# ============================================================
COMPETITOR_PAT = re.compile(
    r"(competitor\s+(discounts?|deals?|codes?|offers?)"
    r"|competitors"
    r"|similar\s+(stores?|brands?|retailers?|deals?)"
    r"|deals?\s+from\s+other\s+(stores?|brands?|retailers?)"
    r"|other\s+(stores?|brands?)\s+you\s+(may|might)"
    r"|(brands?|stores?|retailers?)\s+related\s+to"
    r"|related\s+(retailers?|stores?|brands?)"
    r"|popular\s+stores?\s+you|you\s+may\s+also\s+(like|enjoy))", re.I)


def cut_competitor_sections(html):
    """'Competitor discounts / Similar stores' sections ke codes doosre
    brands ke hote hain — mining se pehle unhe hata do (attribution clean).
    Sirf aisi heading par cut karte hain jo document ke aakhri hisse mein ho
    (nav/footer/JS metadata ke early mentions ignore)."""
    if not html:
        return html
    cut_at = None
    for m in COMPETITOR_PAT.finditer(html):
        if m.start() >= len(html) * 0.5:
            cut_at = m.start()
            break
    if cut_at is None:
        return html
    return html[:cut_at]


def page_is_relevant(html, url, brand):
    """Kya ye page usi brand ka hai? (non-exact matched URLs ke junk codes roko)
    Title/URL mein brand-key hona chahiye, warna body mein 2+ mentions."""
    try:
        from brand_matcher import brand_keys
        info = brand_keys(brand)
    except Exception:
        return True
    full = info["keys"][0] if info["keys"] else ""
    if not full or len(full) < 4:
        return True

    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    title_joined = re.sub(r"[^a-z0-9]", "", html_mod.unescape(m.group(1)).lower()) if m else ""
    url_joined = re.sub(r"[^a-z0-9]", "", (url or "").lower())
    if full in title_joined or full in url_joined:
        return True

    # body fallback: brand-key 2+ dafa visible text mein ho
    text = re.sub(r"<script\b.*?</script>", " ", html or "", flags=re.S | re.I)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    joined = re.sub(r"[^a-z0-9]", "", text.lower())
    return joined.count(full) >= 2


# ============================================================
# STATIC FETCH LADDER (fast path)
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
# CRAWL4AI REVEAL PASS (async — buttons auto-click via js_code)
# ============================================================
REVEAL_JS = r"""
(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    // 1) Cookie/consent overlays dismiss
    const dismissWords = ["accept all", "accept", "allow all", "got it",
                          "no thanks", "dismiss", "i agree"];
    document.querySelectorAll("button").forEach(b => {
        const t = (b.innerText || "").trim().toLowerCase();
        if (t && dismissWords.includes(t) && b.offsetParent) {
            try { b.click(); } catch (e) {}
        }
    });
    await sleep(800);
    // 2) Lazy content: scroll down/up
    for (let i = 0; i < 4; i++) { window.scrollBy(0, 1400); await sleep(300); }
    window.scrollTo(0, 0);
    await sleep(300);

    const isReveal = t => {
        t = (t || "").trim().toLowerCase();
        return t && t.length <= 40 && [
            "get code", "reveal code", "view code", "copy code", "show code",
            "see code", "reveal", "show coupon", "get coupon", "show discount",
            "get discount", "show voucher", "get voucher", "unmask", "unlock",
        ].some(w => t.includes(w));
    };
    const btnSel = "button, a, [role='button']";

    // 3) "Show N more" / "Show more" buttons — chhupe coupons load karo
    for (let round = 0; round < 3; round++) {
        const more = [...document.querySelectorAll(btnSel)].find(b => {
            const t = (b.innerText || "").trim().toLowerCase();
            return t && t.length <= 30 &&
                (/show\s+\d+\s+more/.test(t) || t === "show more" ||
                 t.includes("more coupons") || t.includes("view more offers"));
        });
        if (!more) break;
        try { more.scrollIntoView({block: "center"}); more.click(); await sleep(1800); }
        catch (e) { break; }
    }

    // 4) Pattern A: ek button = sab codes reveal
    const allPats = ["show all", "reveal all", "view all codes",
                     "show all codes", "see all codes", "all codes"];
    const btns = [...document.querySelectorAll(btnSel)];
    const ra = btns.find(b => {
        const t = (b.innerText || "").trim().toLowerCase();
        return t && t.length <= 40 && allPats.some(p => t.includes(p));
    });
    if (ra) {
        try { ra.scrollIntoView({block: "center"}); ra.click(); await sleep(2500); }
        catch (e) {}
    }

    // 5) Pattern B: har coupon button individually click (re-query DOM)
    let clicked = 0;
    const btns2 = [...document.querySelectorAll(btnSel)];
    for (const b of btns2) {
        if (clicked >= 12) break;
        const t = (b.innerText || "").trim();
        if (!isReveal(t)) continue;
        try {
            b.scrollIntoView({block: "center"});
            b.click();
            clicked++;
            await sleep(1400);
            document.querySelectorAll("[aria-label*='lose'], [class*='close']")
                .forEach(x => { try { if (x.offsetParent) x.click(); } catch (e) {} });
        } catch (e) { continue; }
    }
    return {revealAll: !!ra, clicked};
})()
"""


async def crawl4ai_reveal_pass(urls):
    """crawl4ai: stealth browser + REVEAL_JS (buttons auto-click) → final HTML.
    Return: {url: {code: method}}"""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    out = {}
    sem = asyncio.Semaphore(4)
    bcfg = BrowserConfig(headless=True, verbose=False, enable_stealth=True, user_agent=UA)
    async with AsyncWebCrawler(config=bcfg) as crawler:
        async def one(u):
            async with sem:
                try:
                    cfg = CrawlerRunConfig(
                        cache_mode=CacheMode.BYPASS,
                        js_code=REVEAL_JS,
                        page_timeout=60000,
                        delay_before_return_html=2.0,
                        verbose=False,
                    )
                    r = await crawler.arun(url=u, config=cfg)
                    html = getattr(r, "html", "") or ""
                    if r.success and html and not _is_challenge_page(html):
                        out[u] = {c["code"]: c["method"] for c in mine_codes(html)}
                except Exception:
                    pass
        await asyncio.gather(*(one(u) for u in urls))
    return out


# ============================================================
# MAIN PIPELINE
# ============================================================
async def run(brands):
    index = load_index()
    print(f"🗂️  Index: {len(index)} sites, {sum(len(m) for m in index.values())} stores\n")

    # ---- match + URL plan ----
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
    html_map = await asyncio.to_thread(fetch_pages_for_brands, all_urls)
    print(f"   ✅ {len(html_map)}/{len(all_urls)} pages mile")

    # ---- URL → best tier map (gate ke liye) ----
    tier_rank = {"exact": 0, "prefix": 1, "contains": 2, "fuzzy": 3}
    url_tier = {}
    for b, lst in plan.items():
        for t, u in lst:
            cur = url_tier.get(u)
            if cur is None or tier_rank[t] < tier_rank[cur]:
                url_tier[u] = t

    # ---- static mining per URL (guards ke saath) ----
    static_map = {}
    n_irrelevant = 0
    for u in all_urls:
        html_text = html_map.get(u)
        if not html_text:
            continue
        if url_tier.get(u) != "exact":
            # non-exact match: page usi brand ka hona chahiye (junk gate)
            owner = next((b for b, lst in plan.items()
                          if any(u2 == u for _, u2 in lst)), u)
            if not page_is_relevant(html_text, u, owner):
                n_irrelevant += 1
                continue
        static_map[u] = mine_codes(cut_competitor_sections(html_text))
    if n_irrelevant:
        print(f"   🚫 {n_irrelevant} pages brand-relevant nahi the — mining skip")

    # ---- crawl4ai reveal pass ----
    # Saari EXACT URLs (JS-render asli coupon grid) + low-static pages.
    exact_urls = {u for u, t in url_tier.items() if t == "exact"}
    candidates = [u for u in all_urls
                  if u in static_map and
                  (u in exact_urls or len(static_map[u]) < REVEAL_SKIP_THRESHOLD)]
    print(f"\n🤖 crawl4ai reveal pass: {len(candidates)} pages "
          f"(JS render + buttons auto-click)...")
    reveal_map = {}
    if candidates:
        reveal_map = await crawl4ai_reveal_pass(candidates)
        ok = len(reveal_map)
        print(f"   🤖 crawl4ai: {ok}/{len(candidates)} pages")
        missed = [u for u in candidates if u not in reveal_map]
        if missed:
            print(f"   🌐 crawl4ai fail → real-chrome fallback: {len(missed)} URLs")
            by_origin = {}
            for u in missed:
                by_origin.setdefault("/".join(u.split("/")[:3]), []).append(u)
            fb = await asyncio.to_thread(browser_fetch_pages, by_origin)
            for u, h in fb.items():
                if h:
                    reveal_map[u] = {c["code"]: c["method"]
                                     for c in mine_codes(cut_competitor_sections(h))}

    # ---- merge per brand ----
    rows = []
    print()
    for b, lst in plan.items():
        codes = {}   # code -> (method, via)
        for tier, u in lst:
            for c in static_map.get(u, []):
                if c["code"] not in codes:
                    codes[c["code"]] = (c["method"], u)
            for c, method in reveal_map.get(u, {}).items():
                if c not in codes:
                    codes[c] = (method, "crawl4ai")
        print(f"🎫 {b}: {len(codes)} codes")
        for c, (method, via) in codes.items():
            src = next((u for t, u in lst if u in static_map or u in reveal_map), "")
            rows.append({"brand": b, "code": c, "method": method,
                         "via": via, "source_url": src})

    # ---- save ----
    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["brand", "code", "method", "via", "source_url"])
        w.writeheader()
        w.writerows(rows)
    total = len(rows)
    print(f"\n{'=' * 50}")
    print(f"✅ DONE: {total} codes → {RESULTS_CSV}")
    return rows


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.strip() and not a.startswith("--")]
    # --region us  → output: results_us.csv (default uk)
    region = "uk"
    if "--region" in sys.argv:
        i = sys.argv.index("--region")
        if len(sys.argv) > i + 1:
            region = sys.argv[i + 1].lower().strip()
    RESULTS_CSV = f"results_{region}.csv"
    if "--file" in sys.argv and len(sys.argv) > sys.argv.index("--file") + 1:
        fn = sys.argv[sys.argv.index("--file") + 1]
        with open(fn, "r", encoding="utf-8-sig") as f:
            args = [l.strip() for l in f if l.strip()]
    if not args:
        print('Usage: python page_scraper.py "commomy.com" ... | --file brands.txt')
        sys.exit(1)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run(args))
