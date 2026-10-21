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
  python page_scraper.py "Shop LC|shoplc.com" ...     # brands
  python page_scraper.py --file brands.txt
"""
import asyncio
import csv
import html as html_mod
import os
import re
import sys
from datetime import datetime

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import renderer
import search
from browser_engine import browser_context
from brand_matcher import find_matches, load_index
from brand_site import scrape_brand_site
from deal_miner import mine_deals

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# root user / container / VM par Chrome sandbox fail hota hai -> no-sandbox
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled",
               "--no-sandbox", "--disable-dev-shm-usage"]

RESULTS_CSV = "results.csv"
OFFERS_CSV = "offers.csv"

# Unified offers schema (coupon + deal + sale + shipping + newsletter)
OFFER_FIELDS = ["brand", "type", "title", "offer", "url", "percent_off",
                "amount", "price_original", "price_sale", "details",
                "source_url", "method", "via", "region", "found_at"]

MAX_URLS_PER_BRAND = 10     # exact pehle, phir partials
MAX_BODY = 3_000_000        # 3MB cap per page
REVEAL_SKIP_THRESHOLD = 12  # itne codes static se mil gaye to crawl4ai skip
RETRIES = 2                 # har fetch layer ke retry attempts

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


AUTH_CODE_RE = re.compile(r"^[A-Za-z0-9._\-]{3,30}$")


def looks_like_auth_code(text):
    """data-* / JSON declared codes: letters-only bhi valid (jaise REVOWELL)."""
    if not text:
        return False
    t = text.strip()
    if not (3 <= len(t) <= 30):
        return False
    if " " in t or "*" in t:
        return False
    if not AUTH_CODE_RE.match(t):
        return False
    if t.lower() in BAD_WORDS:
        return False
    if ANALYTICS_RE.match(t):
        return False
    return True


TNR_COPY_BTN = re.compile(r'title="Copy code and go to store"')
TNR_STATUS_RE = re.compile(r">(Worked|Failed|Restricted|Expired)</span>")


def _tnr_code_ok(code):
    c = (code or "").strip()
    if not (3 <= len(c) <= 30):
        return False
    if " " in c or "*" in c or not re.match(r"^[A-Za-z0-9._-]+$", c):
        return False
    return c.lower() not in {"tbc", "tbd", "na", "n/a", "none", "null"}


def mine_tenereteam_codes(html_text):
    """tenereteam store page ke 'Recent tests' section se sirf woh codes jo
    checkout test mein 'Worked' hue. Test row mein:
    <button title="Copy code and go to store">...<span class="truncate">CODE</span>
    ... uske baad status badge (Worked/Failed/Restricted/Expired).
    Page ka JSON-LD 'couponCode' ghalat hota hai (Failed code bhi dikhata hai),
    isliye use ignore karte hain."""
    found = {}
    positions = [m.start() for m in TNR_COPY_BTN.finditer(html_text)]
    for i, p in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else min(len(html_text), p + 4000)
        seg = html_text[p:end]
        cm = re.search(r'<span class="truncate">([^<]+)</span>', seg)
        if not cm:
            continue
        code = cm.group(1).strip().upper()
        st = TNR_STATUS_RE.search(seg)
        if st and st.group(1) == "Worked" and _tnr_code_ok(code):
            found[code] = "tenereteam_worked"
    return [{"code": c, "method": m} for c, m in found.items()]


def mine_codes(html_text, url=""):
    """Ek page se codes. Priority: attrs/JSON (site ke declared codes).
    Text tokens tab hi jab wo kaafi hon — junk pattern filter ke saath.
    tenereteam par sirf 'Worked' test codes (site-specific)."""
    if "tenereteam.com" in (url or "").lower():
        return mine_tenereteam_codes(html_text)
    found = {}   # code -> method

    def add(code, method):
        c = code.strip().upper()
        if not c or c in found:
            return
        if method in ("html_attr", "html_json"):
            if not looks_like_auth_code(c):
                return
        else:
            if not looks_like_code(c) or _text_noise(c):
                return
        found[c] = method

    # 1) data-* attributes (authoritative)
    for m in re.findall(
            r'(?:data-code|data-clipboard-text|data-coupon-code|data-promo-code'
            r'|data-voucher-code|data-clipboard)=["\']([^"\']{3,25})["\']', html_text):
        add(m, "html_attr")

    # 1b) class/id-based code containers (jaise <span class="coupon-code">CODE</span>)
    for m in re.findall(
            r'<(?:span|div|strong|b|em)[^>]*(?:class|id)="[^"]*'
            r'(?:coupon-code|promo-code|voucher-code|discount-code)[^"]*"'
            r'[^>]*>([^<]{2,40})</(?:span|div|strong|b|em)>', html_text, re.I):
        txt = m.strip()
        if re.search(r"(get|show|reveal|view|copy)\s+(promo\s*)?code", txt, re.I):
            continue
        if txt.lower() in ("code", "coupon code", "promo code"):
            continue
        add(txt, "html_attr")

    # 2) JSON/JS keys
    _code_keys = (r'(?:code|coupon_code|promo_code|voucher_code|couponCode'
                  r'|promoCode|voucherCode|couponcode)')
    for m in re.findall(
            r'["\']' + _code_keys + r'["\']\s*[:=]\s*["\']'
            r'([^"\']{3,25})["\']', html_text):
        add(m, "html_json")
    # 2b) escaped JSON (embedded in JS strings: \"code\":\"FIRST30\")
    for m in re.findall(
            r'\\["\']' + _code_keys + r'\\["\']\s*[:=]\s*\\["\']'
            r'([^"\\]{3,25})\\["\']', html_text):
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

    # 4) suffix-duplicate cleanup sirf TEXT-mined codes par (authoritative na hatao)
    if len(found) > 1:
        auth = [c for c, m in found.items() if m in ("html_attr", "html_json")]
        drop = set()
        for a, m in list(found.items()):
            if m != "text":
                continue
            for b in auth:
                if a != b and len(a) < len(b) and b.endswith(a):
                    drop.add(a)
                    break
        for d in drop:
            found.pop(d, None)

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
    # script/style ko same-length blanks se mask karo (JS ke "competitors:" par
    # galti se page cut na ho). Positions same rehti hain.
    def _mask(m):
        return "\x00" * len(m.group(0))
    masked = re.sub(r"<script\b.*?</script>", _mask, html, flags=re.S | re.I)
    masked = re.sub(r"<style\b.*?</style>", _mask, masked, flags=re.S | re.I)
    cut_at = None
    for m in COMPETITOR_PAT.finditer(masked):
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
                          headers={"User-Agent": UA},
                          transport=httpx.HTTPTransport(retries=RETRIES)) as client:
            r = client.get(url)
            if r.status_code == 200:
                return r.text
            return None
    except Exception:
        return None


def browser_fetch_pages(urls_by_origin, retries=RETRIES):
    """Blocked origins: Real Chrome headful + in-page fetch → HTML text.
    urls_by_origin: {origin: [urls]} → {url: html_text}
    Fail hui URLs ko isi origin page par reload ke saath retry karta hai."""
    out = {}
    try:
        with browser_context(headless=getattr(config, "HEADLESS", False)) as (ctx, browser, engine):
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
                pending = list(urls)
                for attempt in range(retries + 1):
                    if not pending:
                        break
                    still = []
                    for u in pending:
                        try:
                            txt = page.evaluate(js, u)
                            if txt and not txt.startswith("STATUS:"):
                                out[u] = txt
                            else:
                                still.append(u)
                        except Exception:
                            still.append(u)
                    pending = still
                    if pending and attempt < retries:
                        try:
                            page.reload(wait_until="domcontentloaded", timeout=30000)
                            page.wait_for_timeout(1200)
                        except Exception:
                            pass
                page.close()
    except Exception as e:
        print(f"   ⚠️ Browser fetch fail: {str(e)[:80]}")
    return out


def fetch_pages_for_brands(url_list, render=True):
    """httpx fast pass; blocked/SPA/challenge pages → apna renderer (direct nav)."""
    html_map = {}
    blocked = []
    for url in url_list:
        txt = fetch_httpx(url)
        if txt and not _is_challenge_page(txt):
            html_map[url] = txt
        else:
            blocked.append(url)
    still = list(blocked)
    if blocked and render:
        print(f"   🌐 Renderer fallback: {len(blocked)} URLs "
              f"(direct nav + challenge wait)...")
        rendered = renderer.render_many(blocked)
        for u, r in rendered.items():
            h = r.get("html") or ""
            if h and not _is_challenge_page(h):
                html_map[u] = h
        still = [u for u in blocked if u not in html_map]
        if still:
            print(f"   ↻ Renderer miss: {len(still)} URLs — old browser fallback")
    elif blocked:
        print(f"   ⏭️  Renderer off — old browser fallback: {len(blocked)} URLs")
    if still:
        by_origin = {}
        for u in still:
            by_origin.setdefault("/".join(u.split("/")[:3]), []).append(u)
        html_map.update(browser_fetch_pages(by_origin))
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
            "get promo", "show promo", "view promo", "reveal promo", "promo code",
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
        if (clicked >= 30) break;
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
    Return: {url: {"codes": {code: method}, "deals": [{title, url}]}}"""
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    except Exception as e:
        print(f"   ⚠️ crawl4ai available nahi ({str(e)[:50]}) — real-chrome fallback")
        return {}
    out = {}
    sem = asyncio.Semaphore(4)
    _proxy = getattr(config, "PROXY_URL", "") or ""
    bcfg = BrowserConfig(headless=getattr(config, "HEADLESS", True),
                         verbose=False, enable_stealth=True,
                         user_agent=UA, extra_args=["--no-sandbox",
                                                    "--disable-dev-shm-usage"],
                         **({"proxy_config": _proxy} if _proxy else {}))
    async with AsyncWebCrawler(config=bcfg) as crawler:
        async def one(u):
            async with sem:
                for attempt in range(RETRIES + 1):
                    try:
                        cfg = CrawlerRunConfig(
                            cache_mode=CacheMode.BYPASS,
                            js_code=REVEAL_JS,
                            page_timeout=60000,
                            delay_before_return_html=3.0,
                            verbose=False,
                        )
                        r = await crawler.arun(url=u, config=cfg)
                        html = getattr(r, "html", "") or ""
                        if r.success and html and not _is_challenge_page(html):
                            clean = cut_competitor_sections(html)
                            codes = {c["code"]: c["method"] for c in mine_codes(html, u)}
                            deals = mine_deals(clean, u)
                            if codes or deals:
                                out[u] = {"codes": codes, "deals": deals}
                                return
                    except Exception:
                        pass
                    if attempt < RETRIES:
                        await asyncio.sleep(1.5 * (attempt + 1))
        await asyncio.gather(*(one(u) for u in urls))
    return out


REVEAL_TEXT_RE = re.compile(
    r"(get\s*(promo\s*)?code|show\s*(promo\s*)?code|reveal\s*(promo\s*)?code"
    r"|view\s*(promo\s*)?code|copy\s*(promo\s*)?code|get\s*promo|show\s*promo"
    r"|view\s*promo|reveal\s*promo|get\s*coupon|show\s*coupon|unlock)", re.I)


async def render_reveal_pass(urls, enabled=True, limit=None):
    """Renderer: Pass 1 direct render; Pass 2 reveal (buttons) jahan zaroorat.
    Return: {url: {"codes": {code: method}, "deals": [{title, url}]}}"""
    if not urls or not enabled:
        return {}
    if limit:
        urls = urls[:limit]
    include_deals = bool(getattr(config, "INCLUDE_DEALS", False))
    out = {}
    incomplete = []

    # Pass 1: direct render - zyadatar codes DOM/JSON mein hote hain
    rendered = await asyncio.to_thread(renderer.render_many, urls, reveal=False)
    for u, r in rendered.items():
        html = r.get("html") or ""
        if not html or _is_challenge_page(html):
            continue
        clean = cut_competitor_sections(html)
        codes = {c["code"]: c["method"] for c in mine_codes(html, u)}
        deals = mine_deals(clean, u) if include_deals else []
        if codes or deals:
            out[u] = {"codes": codes, "deals": deals}
        # reveal chahiye: kam codes, ya page par reveal-buttons majood hain
        has_reveal = bool(REVEAL_TEXT_RE.search(html))
        if len(codes) < 3 or has_reveal:
            incomplete.append(u)

    # Pass 2: reveal buttons (Reveal-all pehle, phir individual; limit config)
    if incomplete:
        rev = await asyncio.to_thread(renderer.render_many, incomplete, reveal=True)
        for u, r in rev.items():
            html = r.get("html") or ""
            if not html or _is_challenge_page(html):
                continue
            clean = cut_competitor_sections(html)
            codes = {c["code"]: c["method"] for c in mine_codes(html, u)}
            deals = mine_deals(clean, u) if include_deals else []
            prev = out.get(u, {})
            merged_codes = dict(prev.get("codes", {}))
            merged_codes.update(codes)
            merged_deals = prev.get("deals", []) + deals
            if merged_codes or merged_deals:
                out[u] = {"codes": merged_codes, "deals": merged_deals}
    return out


# ============================================================
# MAIN PIPELINE
# ============================================================
async def run(brands, region="us", brand_site=True, sale_limit=250, types=None,
              render=True, render_limit=None):
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
    html_map = await asyncio.to_thread(fetch_pages_for_brands, all_urls, render)
    print(f"   ✅ {len(html_map)}/{len(all_urls)} pages mile")

    # ---- missing pages: fresh browser se ek retry pass ----
    missing = [u for u in all_urls if u not in html_map]
    if missing:
        print(f"   ↻ {len(missing)} missing pages — fresh browser retry...")
        by_origin = {}
        for u in missing:
            by_origin.setdefault("/".join(u.split("/")[:3]), []).append(u)
        html_map.update(await asyncio.to_thread(browser_fetch_pages, by_origin, 1))
        print(f"   ✅ {len(html_map)}/{len(all_urls)} pages mile (retry ke baad)")

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
        static_map[u] = mine_codes(cut_competitor_sections(html_text), u)
    if n_irrelevant:
        print(f"   🚫 {n_irrelevant} pages brand-relevant nahi the — mining skip")

    # ---- reveal pass (apna renderer; crawl4ai secondary fallback) ----
    # Sirf woh pages jahan static mining se kam codes mile (< threshold).
    candidates = [u for u in all_urls
                  if u in static_map and len(static_map[u]) < REVEAL_SKIP_THRESHOLD]
    if render_limit:
        candidates = candidates[:render_limit]
    print(f"\n🖥️  render reveal pass: {len(candidates)} pages "
          f"(direct nav + challenge wait + buttons auto-click)...")
    reveal_map = {}
    if candidates and render:
        reveal_map = await render_reveal_pass(candidates, enabled=render)
        print(f"   🖥️  renderer: {len(reveal_map)}/{len(candidates)} pages")
        missed = [u for u in candidates if u not in reveal_map]
        if missed:
            print(f"   🤖 renderer miss → crawl4ai fallback: {len(missed)} URLs")
            c4 = await crawl4ai_reveal_pass(missed)
            reveal_map.update(c4)
            missed = [u for u in missed if u not in reveal_map]
        if missed:
            print(f"   🌐 still missing → real-chrome fallback: {len(missed)} URLs")
            by_origin = {}
            for u in missed:
                by_origin.setdefault("/".join(u.split("/")[:3]), []).append(u)
            fb = await asyncio.to_thread(browser_fetch_pages, by_origin)
            for u, h in fb.items():
                if h:
                    clean = cut_competitor_sections(h)
                    reveal_map[u] = {
                        "codes": {c["code"]: c["method"] for c in mine_codes(h, u)},
                        "deals": mine_deals(clean, u)}

    # ---- url -> brand (deals attribution ke liye) ----
    url_brand = {}
    for b, lst in plan.items():
        for _, u in lst:
            url_brand.setdefault(u, b)

    # ---- coupon sites se DEALS (title + link) ----
    deals_by_brand = {}
    seen_deals = set()

    def add_deal(owner, title, link, page):
        key = (owner, title.lower(), link)
        if key in seen_deals:
            return
        seen_deals.add(key)
        deals_by_brand.setdefault(owner, []).append(
            {"title": title, "url": link, "page": page})

    # static HTML se
    for u in all_urls:
        html_text = html_map.get(u)
        if not html_text:
            continue
        owner = url_brand.get(u, u)
        for d in mine_deals(cut_competitor_sections(html_text), u):
            add_deal(owner, d["title"], d["url"], u)
    # reveal (JS-rendered) HTML se
    for u, payload in reveal_map.items():
        owner = url_brand.get(u, u)
        for d in (payload or {}).get("deals", []):
            add_deal(owner, d["title"], d["url"], u)

    # ---- merge per brand (codes + deals) ----
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []      # results.csv (codes)
    offers = []    # offers.csv (unified)
    print()
    for b, lst in plan.items():
        codes = {}   # code -> (method, source_url, via)
        for tier, u in lst:
            for c in static_map.get(u, []):
                if c["code"] not in codes:
                    codes[c["code"]] = (c["method"], u, "static")
            for c, method in (reveal_map.get(u) or {}).get("codes", {}).items():
                if c not in codes:
                    codes[c] = (method, u, "crawl4ai")
        n_deals = len(deals_by_brand.get(b, []))
        print(f"🎫 {b}: {len(codes)} codes, {n_deals} deals")
        for c, (method, src, via) in codes.items():
            rows.append({"brand": b, "code": c, "method": method,
                         "via": via, "source_url": src})
            offers.append({"brand": b, "type": "coupon", "offer": c,
                           "method": method, "via": via, "source_url": src,
                           "region": region, "found_at": now})
        for d in deals_by_brand.get(b, []):
            offers.append({"brand": b, "type": "deal", "title": d["title"],
                           "url": d["url"], "source_url": d["page"],
                           "method": "html", "via": "static",
                           "region": region, "found_at": now})

    # ---- brand ki apni site: sale / shipping / newsletter ----
    if brand_site:
        print("\n🏬 Brand sites (sale / shipping / newsletter)...")
        for b in plan:
            try:
                bs_rows = await asyncio.to_thread(
                    scrape_brand_site, b, region, sale_limit)
            except Exception:
                bs_rows = []
            for r in bs_rows:
                r["brand"] = b
                r["region"] = region
                r["found_at"] = now
                offers.append(r)
            print(f"   🏬 {b}: {len(bs_rows)} brand-site offers")

    # ---- type filter (optional) ----
    if types:
        offers = [o for o in offers if o.get("type") in types]

    # ---- save results.csv (codes) ----
    fields = ["brand", "code", "method", "via", "source_url"]
    out_results = _safe_write(RESULTS_CSV, fields, rows)

    # ---- save offers.csv (unified) ----
    out_offers = _safe_write(OFFERS_CSV, OFFER_FIELDS, offers)

    counts = {}
    for o in offers:
        counts[o["type"]] = counts.get(o["type"], 0) + 1
    print(f"\n{'=' * 50}")
    print(f"✅ DONE: {len(rows)} codes → {out_results}")
    print(f"✅ DONE: {len(offers)} offers → {out_offers}  {counts}")
    return offers


def _safe_write(path, fnames, data):
    """CSV likho; file locked ho to `_latest.csv` fallback."""
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(data)
        return path
    except PermissionError:
        fallback = path.rsplit(".", 1)[0] + "_latest.csv"
        print(f"⚠️  {path} locked — {fallback} mein likh rahe hain")
        with open(fallback, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(data)
        return fallback


# ============================================================
# GOOGLE (Serper) MODE
# ============================================================
ALWAYS_DOMAINS = {"simplycodes.com", "groupon.co.uk"}


def _domain_of(url):
    try:
        return url.split("/")[2].lower().replace("www.", "")
    except Exception:
        return ""


def _dedupe_domains(urls):
    seen, out = set(), []
    for u in urls:
        d = _domain_of(u)
        if not d or d in seen:
            continue
        seen.add(d)
        out.append(u)
    return out


def _always_urls(brand, index, add_simplycodes=True, add_groupon=True):
    """simplycodes + groupon store URLs (index se; na mile to construct)."""
    want = set()
    if add_simplycodes:
        want.add("simplycodes.com")
    if add_groupon:
        want.add("groupon.co.uk")
    urls = []
    try:
        hits = find_matches(brand, index)
    except Exception:
        hits = {}
    for dom, h in hits.items():
        if dom in want:
            urls.append(h["url"])
            want.discard(dom)
    dom = search.brand_domain(brand)
    slug = re.sub(r"[^a-z0-9]+", "-", search.brand_name(brand).lower()).strip("-")
    if "simplycodes.com" in want and dom:
        urls.append(f"https://simplycodes.com/store/{dom}")
    if "groupon.co.uk" in want and slug:
        urls.append(f"https://www.groupon.co.uk/discount-codes/{slug}")
    return urls


async def run_search(brands, region_override=None, max_results=None,
                     add_simplycodes=True, add_groupon=True,
                     brand_site=None, sale_limit=250, types=None,
                     render=True, render_limit=None):
    """Google (Serper) mode: search → page-1 sites + simplycodes/groupon → codes."""
    index = load_index()
    max_results = max_results or getattr(config, "SEARCH_MAX_RESULTS", 10)
    include_deals = bool(getattr(config, "INCLUDE_DEALS", False))
    if brand_site is None:
        brand_site = bool(getattr(config, "INCLUDE_BRAND_SITE", False))
    print(f"🗂️  Index: {len(index)} sites, "
          f"{sum(len(m) for m in index.values())} stores\n")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows, offers = [], []

    for raw in brands:
        name, dom, rhint = search.parse_brand(raw)
        b = f"{name}|{dom}" if dom else name
        eff_region = region_override or rhint
        print(f"\n🔎 {b}" + (f"  [region={eff_region}]" if eff_region else ""))
        # search_brand ko thread mein chalao (sync Playwright asyncio loop mein nahi chalta)
        region, results = await asyncio.to_thread(
            search.search_brand, raw, max_results, eff_region)
        bdom = search.brand_domain(raw)
        urls = _always_urls(b, index, add_simplycodes, add_groupon)
        # brand ki apni site (aur www variant) Google results se hata do
        urls += [r["url"] for r in results if _domain_of(r["url"]) != bdom]
        urls = _dedupe_domains(urls)
        if not urls:
            print("   ⚠️  koi URL nahi mila — skip")
            continue
        print(f"   🌐 {len(urls)} URLs (region={region})...")
        html_map = await asyncio.to_thread(fetch_pages_for_brands, urls, render)
        print(f"   ✅ {len(html_map)}/{len(urls)} pages mile")

        # static mining (brand ki apni site par coupon codes nahi — product names noise)
        static_map = {}
        for u in urls:
            h = html_map.get(u)
            if not h:
                continue
            # Google mode: competitor-cut nahi (valid codes bhi kat jate hain)
            static_map[u] = ([] if _domain_of(u) == bdom
                             else mine_codes(h, u))

        # reveal pass (renderer; crawl4ai/browser fallback)
        candidates = [u for u in urls
                      if u in static_map and _domain_of(u) != bdom
                      and (len(static_map[u]) < REVEAL_SKIP_THRESHOLD
                           or REVEAL_TEXT_RE.search(html_map.get(u) or ""))]
        if render_limit:
            candidates = candidates[:render_limit]
        reveal_map = {}
        if candidates and render:
            print(f"   🖥️  render reveal pass: {len(candidates)} pages...")
            reveal_map = await render_reveal_pass(candidates, enabled=render)
            missed = [u for u in candidates if u not in reveal_map]
            if missed:
                c4 = await crawl4ai_reveal_pass(missed)
                reveal_map.update(c4)
                missed = [u for u in missed if u not in reveal_map]
            if missed:
                by_origin = {}
                for u in missed:
                    by_origin.setdefault("/".join(u.split("/")[:3]), []).append(u)
                fb = await asyncio.to_thread(browser_fetch_pages, by_origin)
                for u, h in fb.items():
                    if h:
                        clean = cut_competitor_sections(h)
                        reveal_map[u] = {
                            "codes": {c["code"]: c["method"]
                                      for c in mine_codes(h, u)},
                            "deals": mine_deals(clean, u)}
            print(f"   🖥️  renderer: {len(reveal_map)}/{len(candidates)} pages")

        # deals (abhi off — client ke rules baad mein)
        deals, seen_d = [], set()
        if include_deals:
            for u in urls:
                h = html_map.get(u)
                if h:
                    for d in mine_deals(cut_competitor_sections(h), u):
                        k = (d["title"].lower(), d["url"])
                        if k not in seen_d:
                            seen_d.add(k)
                            deals.append({"title": d["title"], "url": d["url"], "page": u})
            for u, payload in reveal_map.items():
                for d in (payload or {}).get("deals", []):
                    k = (d["title"].lower(), d["url"])
                    if k not in seen_d:
                        seen_d.add(k)
                        deals.append({"title": d["title"], "url": d["url"], "page": u})

        # merge codes
        codes = {}
        for u in urls:
            for c in static_map.get(u, []):
                if c["code"] not in codes:
                    codes[c["code"]] = (c["method"], u, "static")
            for c, method in (reveal_map.get(u) or {}).get("codes", {}).items():
                if c not in codes:
                    codes[c] = (method, u, "crawl4ai")

        print(f"   🎫 {len(codes)} codes, {len(deals)} deals")
        for c, (method, src, via) in codes.items():
            rows.append({"brand": b, "code": c, "method": method,
                         "via": via, "source_url": src})
            offers.append({"brand": b, "type": "coupon", "offer": c,
                           "method": method, "via": via, "source_url": src,
                           "region": region, "found_at": now})
        for d in deals:
            offers.append({"brand": b, "type": "deal", "title": d["title"],
                           "url": d["url"], "source_url": d["page"],
                           "method": "html", "via": "static",
                           "region": region, "found_at": now})

        # brand ki apni site (sale/shipping/newsletter)
        if brand_site:
            try:
                bs = await asyncio.to_thread(scrape_brand_site, b, region, sale_limit)
            except Exception:
                bs = []
            for r in bs:
                r["brand"] = b
                r["region"] = region
                r["found_at"] = now
                offers.append(r)
            print(f"   🏬 {len(bs)} brand-site offers")

        await asyncio.sleep(getattr(config, "SEARCH_PAUSE", 1.0))

    if types:
        offers = [o for o in offers if o.get("type") in types]

    out_results = _safe_write(RESULTS_CSV,
                              ["brand", "code", "method", "via", "source_url"],
                              rows)
    out_offers = _safe_write(OFFERS_CSV, OFFER_FIELDS, offers)
    counts = {}
    for o in offers:
        counts[o["type"]] = counts.get(o["type"], 0) + 1
    print(f"\n{'=' * 50}")
    print(f"✅ DONE: {len(rows)} codes → {out_results}")
    print(f"✅ DONE: {len(offers)} offers → {out_offers}  {counts}")
    return offers


if __name__ == "__main__":
    # --file brands.txt  → brands file se list (format: Name|brandurl)
    file_arg = None
    source = "google"          # google (Serper) | index (purana sitemap flow)
    region = None              # None = auto (google) / config default (index)
    brand_site = None          # None = google: off (coupon-only) / index: config
    sale_limit = getattr(config, "SALE_LIMIT", 250)
    max_results = getattr(config, "SEARCH_MAX_RESULTS", 10)
    add_simplycodes = True
    add_groupon = True
    types = None
    render = True
    render_limit = None
    args = []
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--file" and i + 1 < len(argv):
            file_arg = argv[i + 1]
            i += 2
            continue
        if a == "--source" and i + 1 < len(argv):
            source = argv[i + 1].strip().lower()
            i += 2
            continue
        if a == "--region" and i + 1 < len(argv):
            region = argv[i + 1].strip().lower()
            i += 2
            continue
        if a == "--max-results" and i + 1 < len(argv):
            try:
                max_results = int(argv[i + 1])
            except Exception:
                pass
            i += 2
            continue
        if a == "--no-simplycodes":
            add_simplycodes = False
            i += 1
            continue
        if a == "--no-groupon":
            add_groupon = False
            i += 1
            continue
        if a == "--sale-limit" and i + 1 < len(argv):
            try:
                sale_limit = int(argv[i + 1])
            except Exception:
                pass
            i += 2
            continue
        if a == "--types" and i + 1 < len(argv):
            types = {t.strip() for t in argv[i + 1].split(",") if t.strip()}
            i += 2
            continue
        if a == "--no-brand-site":
            brand_site = False
            i += 1
            continue
        if a == "--brand-site":
            brand_site = True
            i += 1
            continue
        if a == "--no-render":
            render = False
            i += 1
            continue
        if a == "--render-limit" and i + 1 < len(argv):
            try:
                render_limit = int(argv[i + 1])
            except Exception:
                pass
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        if a.strip():
            args.append(a)
        i += 1
    if file_arg:
        with open(file_arg, "r", encoding="utf-8-sig") as f:
            args = [l.strip() for l in f if l.strip()]
    if not args:
        print('Usage: python page_scraper.py --file brands.txt '
              '[--source google|index] [--region us|uk] [--max-results 10] '
              '[--no-simplycodes] [--no-groupon] [--no-brand-site] '
              '[--sale-limit 250] [--types coupon,deal,sale,shipping,newsletter] '
              '[--no-render] [--render-limit N]')
        sys.exit(1)

    if source == "index":
        bs = getattr(config, "SCRAPE_BRAND_SITE", True) if brand_site is None else brand_site
        asyncio.run(run(args, region=(region or getattr(config, "REGION", "us")),
                        brand_site=bs, sale_limit=sale_limit, types=types,
                        render=render, render_limit=render_limit))
    else:
        # brand_site=None -> run_search config (INCLUDE_BRAND_SITE, default off)
        asyncio.run(run_search(args, region_override=region,
                               max_results=max_results,
                               add_simplycodes=add_simplycodes,
                               add_groupon=add_groupon,
                               brand_site=brand_site, sale_limit=sale_limit,
                               types=types, render=render,
                               render_limit=render_limit))
