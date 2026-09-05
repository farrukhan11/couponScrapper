"""
SITEMAP CRAWLER — saari client sites ke STORE sitemaps fetch karke ek unified
store-index banata hai:  data/stores_index.json

Fetch ladder (Firecrawl-style, apna infra):
  1. httpx (HTTP/2, fast)                          — simple sites
  2. Playwright real-Chrome + in-page fetch()      — Cloudflare wali sites
  3. Firecrawl API (optional, config mein key)     — last resort

Sitemap sources:
  - Direct list:      [("url", "gz"), ...]
  - Sitemap index:    {"index": ".../sitemap.xml", "filter": "stores"}
      (tenereteam: /sitemap.xml → 708 children, stores_0..stores_700)

Usage:
  python sitemap_crawler.py                     # sab rebuild/merge
  python sitemap_crawler.py --site tenereteam.com   # sirf ye site (baaki index intact)
"""
import base64
import gzip
import json
import os
import re
import sys
from urllib.parse import urlparse

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

INDEX_FILE = "data/stores_index.json"
OLD_INDEX_DIR = "data/sitemap_indexes"

# ------------------------------------------------------------
# STORE SITEMAP SOURCES
# ------------------------------------------------------------
SITEMAP_SOURCES = {
    "simplycodes.com": [
        (f"https://simplycodes.com/sitemaps/sitemap-stores-active-{i}.xml.gz", "gz")
        for i in (1, 2, 3)
    ],
    "promopro.co.uk": [
        (f"https://www.promopro.co.uk/promopro_sitemap_store_{g}_{n}.xml", "xml")
        for g, n in [("a", 1), ("b", 1), ("c", 1), ("c", 2), ("d", 1), ("d", 2), ("z", 1)]
    ],
    # tenereteam: sitemap INDEX → saari stores_* children crawl karo
    "tenereteam.com": {
        "index": "https://www.tenereteam.com/sitemap.xml",
        "filter": "stores",
    },
}

BATCH = 25   # browser fetch chunk size


# ============================================================
# SLUG EXTRACTION (store URL → brand slug)
# ============================================================
SUFFIXES = ("-voucher-codes", "-discount-codes", "-coupon-codes", "-coupons",
            "-promo-codes", "-vouchers", "-deals", "-discount-code",
            "-coupon-code", "-promo-code", "-voucher", "-discount", "-offers",
            "-coupons-deals")

NON_BRAND = {
    "about", "about-us", "contact", "contact-us", "blog", "news", "terms",
    "privacy", "faq", "help", "login", "signup", "register", "search",
    "category", "categories", "jobs", "press", "advertise", "apps", "home",
    "black-friday", "cyber-monday", "christmas", "stores", "store", "shops",
    "shop", "brands", "brand", "all", "popular", "new", "trending", "other",
}

CATEGORY_SEGS = {"store", "stores", "shop", "shops", "brand", "brands",
                 "coupons", "coupon", "vouchers", "deals", "discount-codes",
                 "voucher-codes", "promo-codes", "retailer", "retailers"}


def join_key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def store_slug(url, domain):
    """Store URL se brand slug nikalo:
    /store/goonzquad.com        → goonzquad
    cotswoldco.promopro.co.uk   → cotswoldco
    pawarts.tenereteam.com/coupons → pawarts
    /boohoo-voucher-codes       → boohoo
    """
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower().replace("www.", "")
        base = domain.replace("www.", "")
    except Exception:
        return None

    # Subdomain store ({store}.{domain})
    if host.endswith("." + base):
        pre = host[: -len("." + base)]
        if pre and pre not in ("www", "m") and "." not in pre and len(pre) >= 3:
            return pre
        return None
    if host not in (base, "www." + base):
        return None

    segs = [s for s in (p.path or "").split("/") if s]
    if not segs:
        return None
    cand = None
    for s in segs:
        if s.lower() in CATEGORY_SEGS:
            continue
        cand = s
        break
    if not cand:
        return None
    cand = cand.lower()
    cand = re.sub(r"\.(html?|php|aspx)$", "", cand)
    cand = re.sub(r"\.(com|co\.uk|uk|us|net|org|io|store|shop|de)$", "", cand)
    for suf in SUFFIXES:
        if cand.endswith(suf) and len(cand) > len(suf) + 1:
            cand = cand[: -len(suf)]
            break
    if not cand or cand in NON_BRAND or re.match(r"^\d+$", cand):
        return None
    if len(cand) < 3 or len(cand) > 60:
        return None
    return cand


# ============================================================
# FETCH HELPERS
# ============================================================
BATCH_JS = """async (urls) => {
    const out = {};
    for (const url of urls) {
        try {
            const r = await fetch(url, {credentials: 'include'});
            if (!r.ok) { out[url] = 'STATUS:' + r.status; continue; }
            const buf = new Uint8Array(await r.arrayBuffer());
            let bin = '';
            for (let i = 0; i < buf.length; i += 8192) {
                bin += String.fromCharCode.apply(null, buf.subarray(i, i + 8192));
            }
            out[url] = btoa(bin);
        } catch (e) { out[url] = 'ERR:' + e.message; }
    }
    return out;
}"""


def fetch_httpx(url):
    try:
        with httpx.Client(http2=True, timeout=25, follow_redirects=True,
                          headers={"User-Agent": UA}) as client:
            r = client.get(url)
            if r.status_code == 200:
                return r.content, 200
            return None, r.status_code
    except Exception:
        return None, 0


def decode_body(body):
    try:
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        text = body.decode("utf-8", errors="replace")
        return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)
    except Exception:
        return None


def browser_fetch_batch(urls, origin):
    """Real Chrome headful + in-page fetch — saari urls batches mein.
    Return: {url: bytes}"""
    out = {}
    try:
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
            page = ctx.new_page()
            try:
                page.goto(origin, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
            except Exception:
                pass
            ok = fail = 0
            for i in range(0, len(urls), BATCH):
                chunk = urls[i:i + BATCH]
                try:
                    res = page.evaluate(BATCH_JS, chunk) or {}
                except Exception:
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=30000)
                        res = page.evaluate(BATCH_JS, chunk) or {}
                    except Exception:
                        res = {}
                for u, v in res.items():
                    if v and not v.startswith(("STATUS:", "ERR:")):
                        try:
                            out[u] = base64.b64decode(v)
                            ok += 1
                        except Exception:
                            fail += 1
                    else:
                        fail += 1
                done = min(i + BATCH, len(urls))
                print(f"      {done}/{len(urls)} fetched (ok={ok} fail={fail})")
            browser.close()
    except Exception as e:
        print(f"   ⚠️ Browser fetch fail: {str(e)[:60]}")
    return out


def fetch_firecrawl(url):
    try:
        import config
        key = (getattr(config, "FIRECRAWL_API_KEY", "") or "").strip()
        if not key:
            return None
        import requests
        r = requests.post("https://api.firecrawl.dev/v1/scrape",
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"},
                          json={"url": url, "formats": ["rawHtml"]}, timeout=90)
        if r.status_code == 200:
            data = (r.json() or {}).get("data") or {}
            return (data.get("rawHtml") or data.get("markdown") or "").encode("utf-8")
    except Exception:
        pass
    return None


# ============================================================
# SITE CRAWL
# ============================================================
def resolve_index_children(dom, src):
    """Sitemap index fetch karo → children URLs (filter ke saath)."""
    index_url = src["index"]
    word = src.get("filter", "")
    # httpx pehle
    body, status = fetch_httpx(index_url)
    locs = decode_body(body) if body else None
    if not locs:
        print(f"   [httpx {status}] index browser se: {index_url.rsplit('/', 1)[-1]}")
        origin = "/".join(index_url.split("/")[:3])
        got = browser_fetch_batch([index_url], origin)
        body = got.get(index_url)
        locs = decode_body(body) if body else None
    if not locs:
        body = fetch_firecrawl(index_url)
        locs = decode_body(body) if body else None
    if not locs:
        print(f"   ⛔ sitemap index hi nahi mila: {index_url}")
        return []
    children = [l for l in locs if word in l.lower()] if word else locs
    print(f"   🗂️  index: {len(locs)} children, filter '{word}': {len(children)} files")
    return children


def crawl_site(dom, src):
    """Ek site ke saare store URLs nikalo (httpx → browser → firecrawl)."""
    origin = f"https://{dom}"
    if isinstance(src, dict):
        urls = resolve_index_children(dom, src)
        kinds = [None] * len(urls)
    else:
        urls = [u for u, _ in src]
        kinds = [k for _, k in src]

    locs_all = []
    pending = list(urls)

    # --- httpx pass (simple sites) ---
    still = []
    for u in pending:
        body, status = fetch_httpx(u)
        got = decode_body(body) if body else None
        if got:
            locs_all.extend(got)
        else:
            still.append(u)
    if pending and not still:
        print(f"   ✅ httpx: {len(locs_all)} URLs ({len(pending)} sitemaps)")
        return locs_all

    # --- browser batch pass ---
    if still:
        print(f"   🌐 Browser fetch: {len(still)} sitemaps...")
        fetched = browser_fetch_batch(still, origin)
        for u, body in fetched.items():
            got = decode_body(body)
            if got:
                locs_all.extend(got)
        still = [u for u in still if u not in fetched]

    # --- Firecrawl pass ---
    for u in still:
        body = fetch_firecrawl(u)
        got = decode_body(body) if body else None
        if got:
            locs_all.extend(got)
            print(f"   🔥 Firecrawl: {u.rsplit('/', 1)[-1]}")
        else:
            print(f"   ⛔ {u.rsplit('/', 1)[-1]} — saare layers fail")
    return locs_all


# ============================================================
# INDEX BUILD
# ============================================================
def build_index(only_sites=None):
    """Unified store-index: {domain: {join_key: [slug, url]}}"""
    index = {}

    # Partial rebuild: existing index load karo, sirf target sites replace
    if only_sites and os.path.exists(INDEX_FILE):
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                index = json.load(f)
        except Exception:
            index = {}
        for dom in only_sites:
            index.pop(dom, None)

    # --- 1) OLD index files (un-crawled sites) ---
    if not only_sites and os.path.isdir(OLD_INDEX_DIR):
        for fn in os.listdir(OLD_INDEX_DIR):
            if not fn.endswith(".json"):
                continue
            dom = fn[:-5]
            if dom in SITEMAP_SOURCES:
                continue   # fresh crawl hota hai sources se
            try:
                with open(os.path.join(OLD_INDEX_DIR, fn), "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            mapping = {}
            for url in data.get("brand_urls", []) + data.get("root_slug_urls", []):
                slug = store_slug(url, dom)
                if slug:
                    mapping.setdefault(join_key(slug), [slug, url])
            if mapping:
                index[dom] = mapping

    # --- 2) SITEMAP_SOURCES (fresh crawl) ---
    for dom, src in SITEMAP_SOURCES.items():
        if only_sites and dom not in only_sites:
            continue
        if isinstance(src, dict):
            print(f"\n🌐 {dom} — sitemap index crawl...")
        else:
            print(f"\n🌐 {dom} — {len(src)} store sitemaps...")
        locs = crawl_site(dom, src)
        mapping = {}
        for url in locs:
            slug = store_slug(url, dom)
            if slug:
                mapping.setdefault(join_key(slug), [slug, url])
        if mapping:
            index[dom] = mapping
            print(f"   📦 {len(mapping)} unique stores")

    # --- save ---
    os.makedirs("data", exist_ok=True)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    total = sum(len(m) for m in index.values())
    print(f"\n{'=' * 60}")
    print(f"💾 {INDEX_FILE}: {len(index)} sites, {total} stores")
    for dom, m in sorted(index.items(), key=lambda x: -len(x[1])):
        print(f"   {dom:<34} {len(m):>6}")
    return index


if __name__ == "__main__":
    only = None
    if len(sys.argv) > 2 and sys.argv[1] == "--site":
        only = {s.strip() for s in sys.argv[2].split(",") if s.strip()}
    build_index(only)
