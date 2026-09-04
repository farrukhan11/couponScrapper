"""
SITE PROFILER — client ki 24 coupon sites ka "naksha" banata hai (hybrid router ki Layer 0).

Har site ke liye detect karta hai:
  1. Sitemap (robots.txt -> common paths probe) aur usme se brand-page URLs ka index
  2. Brand-page URL patterns (e.g. /store/{slug}, /discount-codes/{slug})
  3. Site ka apna search template (homepage form se, e.g. /search?q={q})
  4. Cloudflare/bot protection status

Output:
  site_profiles.json                  -> per-site profile
  data/sitemap_indexes/{domain}.json  -> brand URLs ka local index (router Layer 1 isse use karega)

Usage:
  python site_profiler.py                 # saari sites (sites.txt)
  python site_profiler.py --limit 6       # pehli 6 sites (test)
  python site_profiler.py --site https://www.savoo.co.uk/
"""
import asyncio
import gzip
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from urllib.parse import urlparse, urljoin

from playwright.async_api import async_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

SITES_FILE = "sites.txt"
PROFILES_FILE = "site_profiles.json"
INDEX_DIR = "data/sitemap_indexes"

CONCURRENCY = 4
REQ_TIMEOUT = 15000
PAGE_TIMEOUT = 25000
MAX_SITEMAP_FETCHES = 40      # per site (index children + urlsets)
MAX_TOTAL_URLS = 30000        # per site
MAX_INDEX_URLS = 20000        # saved brand URLs per site
MAX_ROOT_SLUGS = 20000        # root-level slug URLs saved for inspection
MAX_OTHER_SAMPLE = 400        # diagnostic sample of unclassified URLs

COMMON_SITEMAP_PATHS = [
    "/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml",
    "/wp-sitemap.xml", "/sitemap/sitemap.xml", "/sitemap.xml.gz",
    "/sitemap/sitemap-index.xml", "/sitemap/index.xml",
]

BRAND_SEGMENTS = {
    "store", "stores", "shop", "shops", "brand", "brands", "retailer",
    "retailers", "merchant", "merchants", "coupons", "coupon", "vouchers",
    "voucher", "discount-codes", "promo-codes", "promos", "deals", "offer",
    "offers", "all-stores", "all-brands", "brand-coupons", "shop-coupons",
    "voucher-codes", "discount-coupons", "coupon-codes", "retailer-coupons",
    "suppliers", "supplier", "shopping",
}
BRAND_HINT_RE = re.compile(
    r"(store|shop|brand|retailer|merchant|coupon|voucher|discount|promo|deal|supplier)", re.I)

NON_BRAND_ROOT = {
    "about", "about-us", "contact", "contact-us", "blog", "news", "terms",
    "terms-and-conditions", "terms-conditions", "privacy", "privacy-policy",
    "faq", "faqs", "help", "login", "signup", "sign-up", "register", "search",
    "categories", "category", "sitemap", "sitemap.xml", "jobs", "careers",
    "press", "advertise", "cookies", "cookie-policy", "accessibility",
    "apps", "android", "ios", "home", "black-friday", "christmas",
    "cyber-monday", "bank-holidays", "seasonal-offers", "students",
}

# Root-level slugs jinke end me ye suffixes hon wo brand pages hain
# e.g. shoppingspout.co.uk/adidas-voucher-codes
BRAND_SUFFIX_RE = re.compile(
    r"-(voucher-codes?|discount-codes?|coupon-codes?|coupons?|promo-codes?|"
    r"promo-code|vouchers?|discounts?|deals?|offers?)$", re.I)
SUFFIX_CANON = {
    "voucher-codes": "-voucher-codes", "voucher-code": "-voucher-codes",
    "discount-codes": "-discount-codes", "discount-code": "-discount-codes",
    "coupon-codes": "-coupon-codes", "coupon-code": "-coupon-codes",
    "coupons": "-coupons", "coupon": "-coupons",
    "promo-codes": "-promo-codes", "promo-code": "-promo-codes",
    "vouchers": "-vouchers", "voucher": "-vouchers",
    "discounts": "-discounts", "discount": "-discount",
    "deals": "-deals", "deal": "-deals",
    "offers": "-offers", "offer": "-offers",
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


# ============================================================
# FETCH HELPERS
# ============================================================
def is_challenge(text):
    head = text[:3000].lower()
    return ("just a moment" in head or "challenge-platform" in head
            or "captcha-delivery" in head or "px-captcha" in head)


async def req_text(req, url):
    """Browser-context request se text fetch (fast path)."""
    try:
        r = await req.get(url, timeout=REQ_TIMEOUT, headers={"User-Agent": UA})
        if r.status != 200:
            return None, r.status
        if (r.headers or {}).get("cf-mitigated") == "challenge":
            return None, 403
        body = await r.body()
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        text = body.decode("utf-8", errors="replace")
        if is_challenge(text):
            return None, 403
        return text, 200
    except Exception:
        return None, 0


def plain_text(url):
    """Pure HTTP fallback (kuch sites browser-fingerprint ko 403 karti hain
    lekin simple requests ko allow karti hain — e.g. simplycodes)."""
    try:
        import requests
        r = requests.get(url, timeout=12, headers={"User-Agent": UA}, allow_redirects=True)
        if r.status_code != 200 or "<html" in r.text[:600].lower():
            return None, r.status_code
        return r.text, 200
    except Exception:
        return None, 0


async def page_fetch_text(page, url):
    """403/challenge ho to real page navigation se fetch (slower path)."""
    for attempt in range(3):
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            if resp is None:
                return None, 0
            if resp.status in (403, 503) and attempt < 2:
                await page.wait_for_timeout(3000)
                continue
            if resp.status != 200:
                return None, resp.status
            body = await resp.body()
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            return body.decode("utf-8", errors="replace"), 200
        except Exception as e:
            if "ERR_NAME_NOT_RESOLVED" in str(e) and attempt < 2:
                await page.wait_for_timeout(3000)
                continue
            return None, 0
    return None, 403


async def smart_fetch(req, page, url):
    text, status = await req_text(req, url)
    if text is None and status in (0, 403, 404, 503):
        text, status = await page_fetch_text(page, url)
    if text is None and status in (0, 403, 503):
        text, status = plain_text(url)
    return text, status


# ============================================================
# SITEMAP PARSING
# ============================================================
def parse_sitemap(text):
    """Return (type, locs): type in {index, urlset, unknown}."""
    try:
        root = ET.fromstring(text.encode("utf-8"))
        tag = root.tag.split("}")[-1].lower()
        locs = []
        for el in root.iter():
            if el.tag.split("}")[-1].lower() == "loc" and el.text:
                locs.append(el.text.strip())
        if tag == "sitemapindex":
            return "index", locs
        if tag == "urlset":
            return "urlset", locs
        return "unknown", locs
    except Exception:
        if "<html" in text[:600].lower():
            return "unknown", []
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text, re.I)
        if locs:
            stype = "index" if re.search(r"<sitemap[>\s]", text, re.I) else "urlset"
            return stype, locs
        return "unknown", []


async def collect_sitemap_urls(req, page, sitemap_url, notes):
    """Index -> children (brand-hinted pehle), sab urlset URLs jama karo."""
    total, queue, seen = [], [sitemap_url], set()
    fetches = 0
    while queue and len(total) < MAX_TOTAL_URLS and fetches < MAX_SITEMAP_FETCHES:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        fetches += 1
        text, status = await smart_fetch(req, page, sm)
        if not text:
            notes.append(f"sitemap fetch fail({status}): {sm[-70:]}")
            continue
        stype, locs = parse_sitemap(text)
        if stype == "index":
            hinted = [l for l in locs if BRAND_HINT_RE.search(l)]
            others = [l for l in locs if l not in set(hinted)]
            queue.extend(hinted + others)
        elif stype == "urlset":
            total.extend(locs)
        else:
            notes.append(f"unparseable sitemap: {sm[-70:]}")
    return total, fetches


# Subdomains jo brand pages nahi hain (infra/CDN)
NON_BRAND_SUBDOMAINS = {
    "www", "cdn", "static", "assets", "img", "images", "media", "api",
    "mail", "smtp", "admin", "blog", "shop", "m", "mobile", "go", "link",
    "track", "support", "docs", "help", "files", "dl", "download", "amp",
}


def classify_urls(urls, base_domain=""):
    """Brand-like URLs + root-level slug URLs + pattern shapes."""
    brand, root_slugs, shapes = [], [], Counter()
    suffix_shapes = Counter()
    other_sample = []
    exts = (".html", ".htm", ".php", ".aspx", ".xml", ".css", ".js")
    sub_suffix = "." + base_domain if base_domain else None
    for u in urls:
        parsed = urlparse(u)
        netloc = parsed.netloc.lower()
        segs = [s for s in parsed.path.split("/") if s]
        if not segs:
            if (sub_suffix and netloc.endswith(sub_suffix)
                    and netloc != sub_suffix.lstrip(".")
                    and netloc != "www." + base_domain):
                slug = netloc[: -len(sub_suffix)]
                if slug and slug not in NON_BRAND_SUBDOMAINS and "." not in slug:
                    brand.append(u)
                    shapes[f"{{slug}}.{base_domain}"] += 1
            continue
        matched = False
        for i, s in enumerate(segs[:3]):
            if s.lower() in BRAND_SEGMENTS:
                rest = segs[i + 1:]
                shape = "/" + "/".join(segs[:i + 1]) + "/{slug}"
                if rest:
                    shape += "/" + "/".join(["{x}"] * len(rest))
                shapes[shape] += 1
                brand.append(u)
                matched = True
                break
        if matched:
            continue
        if len(segs) == 1:
            low = segs[0].lower()
            if low not in NON_BRAND_ROOT:
                m = BRAND_SUFFIX_RE.search(low)
                if m:
                    brand.append(u)
                    suffix_shapes["/{slug}" + SUFFIX_CANON[m.group(1).lower()]] += 1
                    continue
                if not low.endswith(exts) and not re.match(r"^\d", low):
                    root_slugs.append(u)
                    continue
        if len(other_sample) < MAX_OTHER_SAMPLE:
            other_sample.append(u)
    shapes.update(suffix_shapes)
    return brand, root_slugs, shapes, other_sample


# ============================================================
# SEARCH TEMPLATE DETECTION
# ============================================================
SEARCH_INPUT_NAMES = {"s", "q", "query", "search", "keyword", "keywords",
                      "search_query", "term", "searchword", "search_term",
                      "store-search", "searchparam", "k"}


def find_search_template(home_url, html):
    for m in re.finditer(r"<form\b([^>]*)>(.*?)</form>", html, re.S | re.I):
        attrs, body = m.group(1), m.group(2)
        names = re.findall(r"<input[^>]+name=[\"']([^\"']+)[\"']", body, re.I)
        sname = next((n for n in names if n.lower() in SEARCH_INPUT_NAMES), None)
        if not sname:
            continue
        act = re.search(r"action=[\"']([^\"']+)[\"']", attrs, re.I)
        act = act.group(1) if act else ""
        if act.startswith("http"):
            base = act
        elif act:
            base = urljoin(home_url + "/", act)
        else:
            base = home_url
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}{sname}={{q}}"
    for m in re.finditer(r"href=[\"']([^\"']*\/search[^\"']*)[\"']", html, re.I):
        base = urljoin(home_url + "/", m.group(1).split("?")[0])
        return f"{base}?q={{q}}"
    return None


def robots_pattern_hints(robots):
    """robots.txt ke Disallow rules se brand-page patterns ka ishara
    e.g. Disallow: /stores/*/review  ->  /stores/{slug} brand page pattern."""
    hints = Counter()
    for m in re.finditer(r"(?im)^\s*(?:dis)?allow:\s*/?([a-z0-9\-_]+)/\*", robots):
        seg = m.group(1).lower()
        if seg in BRAND_SEGMENTS:
            hints[f"/{seg}/{{slug}}"] += 1
    return dict(hints)


async def probe_search(req, template):
    if not template:
        return False
    try:
        text, status = await req_text(req, template.format(q="nike"))
        return status == 200 and text is not None
    except Exception:
        return False


# ============================================================
# PER-SITE PROFILING
# ============================================================
async def profile_site(pw_ctx, site_url, sem):
    async with sem:
        page = await pw_ctx.new_page()
        req = pw_ctx.request
        notes = []
        domain = urlparse(site_url).netloc.lower().replace("www.", "")
        print(f"\n🔎 [{site_url}] profiling...")

        profile = {
            "site_url": site_url, "domain": domain,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "homepage": {"status": "error", "title": ""},
            "sitemap": {"found": False, "source": None, "index_url": None,
                        "fetches": 0, "total_urls": 0, "brand_urls": 0,
                        "root_slug_urls": 0, "top_patterns": []},
            "search": {"template": None, "confirmed": False},
            "index_file": None, "notes": notes,
        }

        try:
            # ---------- Homepage ----------
            try:
                await page.goto(site_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            except Exception as e:
                notes.append(f"homepage goto: {str(e)[:80]}")
            html = await page.content()
            if is_challenge(html):
                await page.wait_for_timeout(8000)
                html = await page.content()
                if is_challenge(html):
                    await page.wait_for_timeout(7000)
                    html = await page.content()
                    if is_challenge(html):
                        profile["homepage"]["status"] = "challenge"
                        notes.append("Cloudflare challenge — headless pass nahi hua")
            if profile["homepage"]["status"] != "challenge":
                profile["homepage"]["status"] = "ok"
                profile["homepage"]["title"] = (await page.title())[:100]
                profile["search"]["template"] = find_search_template(page.url, html)

            # ---------- Sitemap discovery ----------
            robots, st = await smart_fetch(req, page, urljoin(site_url, "/robots.txt"))
            hints = {}
            if robots:
                hints = robots_pattern_hints(robots)
                profile["robots_hints"] = hints
            sitemap_urls = []
            robots_stale = False
            if robots:
                sitemap_urls = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots)
                if sitemap_urls:
                    profile["sitemap"]["source"] = "robots.txt"
            if not sitemap_urls:
                robots_stale = True
            for path in COMMON_SITEMAP_PATHS:
                text, status = await smart_fetch(req, page, urljoin(site_url, path))
                if text:
                    stype, locs = parse_sitemap(text)
                    if stype in ("index", "urlset"):
                        profile["sitemap"]["source"] = f"probe:{path}"
                        sitemap_urls = [urljoin(site_url, path)]
                        robots_stale = False
                        break

            # ---------- Sitemap collection ----------
            if sitemap_urls:
                profile["sitemap"]["found"] = True
                profile["sitemap"]["index_url"] = sitemap_urls[0]
                all_urls = []
                for sm in sitemap_urls[:3]:
                    urls, fetches = await collect_sitemap_urls(req, page, sm, notes)
                    all_urls.extend(urls)
                    profile["sitemap"]["fetches"] += fetches
                    if len(all_urls) >= MAX_TOTAL_URLS:
                        break
                if not all_urls and robots_stale:
                    notes.append("robots.txt sitemap stale (0 urls) — probe bhi fail")
                seen, uniq = set(), []
                for u in all_urls:
                    if u not in seen:
                        seen.add(u)
                        uniq.append(u)
                brand, root_slugs, shapes, other_sample = classify_urls(uniq, domain)
                profile["sitemap"]["total_urls"] = len(uniq)
                profile["sitemap"]["brand_urls"] = len(brand)
                profile["sitemap"]["root_slug_urls"] = len(root_slugs)
                profile["sitemap"]["other_urls"] = max(0, len(uniq) - len(brand) - len(root_slugs))
                profile["sitemap"]["top_patterns"] = [
                    {"pattern": p, "count": c} for p, c in shapes.most_common(5)]

                if brand or root_slugs or other_sample:
                    os.makedirs(INDEX_DIR, exist_ok=True)
                    idx_path = os.path.join(INDEX_DIR, f"{domain}.json")
                    with open(idx_path, "w", encoding="utf-8") as f:
                        json.dump({
                            "domain": domain, "fetched": profile["checked_at"],
                            "sitemap": sitemap_urls[0],
                            "brand_urls": brand[:MAX_INDEX_URLS],
                            "root_slug_urls": root_slugs[:MAX_ROOT_SLUGS],
                            "other_sample": other_sample,
                            "url_sample": uniq[:100],
                        }, f, ensure_ascii=False, indent=1)
                    profile["index_file"] = idx_path
            else:
                notes.append("sitemap nahi mila")

            # ---------- Search probe ----------
            profile["search"]["confirmed"] = await probe_search(req, profile["search"]["template"])

        except Exception as e:
            notes.append(f"error: {str(e)[:120]}")
        finally:
            try:
                await page.close()
            except Exception:
                pass

        s = profile["sitemap"]
        top = s["top_patterns"][0]["pattern"] if s["top_patterns"] else "-"
        if not s["top_patterns"] and profile.get("robots_hints"):
            top = "robots:" + ",".join(list(profile["robots_hints"])[:2])
        print(f"   {domain:<28} {'✅' if s['found'] else '❌'} urls={s['total_urls']:>6} "
              f"brand={s['brand_urls']:>5} root={s['root_slug_urls']:>5} | {top[:34]:<34} | "
              f"search={'✔' if profile['search']['confirmed'] else '✘'}")
        return profile


# ============================================================
# MAIN
# ============================================================
def load_sites():
    if not os.path.exists(SITES_FILE):
        print(f"❌ {SITES_FILE} nahi mila")
        return []
    with open(SITES_FILE, "r", encoding="utf-8-sig") as f:
        sites = [line.strip().rstrip("/") for line in f if line.strip()]
    seen, uniq = set(), []
    for s in sites:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def print_summary(profiles):
    print("\n" + "=" * 110)
    print(f"{'SITE':<30} {'SITEMAP':<9} {'BRAND':>6} {'ROOT':>6} {'OTHER':>7} {'TOP PATTERN':<28} {'SEARCH':<22}")
    print("-" * 110)
    for p in profiles:
        s = p["sitemap"]
        top = s["top_patterns"][0]["pattern"][:26] if s["top_patterns"] else "-"
        tmpl = (p["search"]["template"] or "-")
        tmpl = tmpl.replace("https://" + p["domain"], "").replace("https://www." + p["domain"], "")[:20]
        other = s.get("other_urls", 0)
        print(f"{p['domain']:<30} {'YES' if s['found'] else 'NO':<9} "
              f"{s['brand_urls']:>6} {s['root_slug_urls']:>6} {other:>7} {top:<28} {'✔ ' + tmpl if p['search']['confirmed'] else '✘':<22}")
    print("-" * 110)
    covered = sum(1 for p in profiles if p["sitemap"]["brand_urls"] > 0 or p["sitemap"]["root_slug_urls"] > 0)
    searched = sum(1 for p in profiles if p["search"]["confirmed"])
    print(f"  Sitemap+brand index: {covered}/{len(profiles)} | Confirmed search: {searched}/{len(profiles)}")


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--site", type=str, default="", help="single site ya comma-separated list")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY)
    args = parser.parse_args()

    if args.site:
        sites = [s.strip().rstrip("/") for s in args.site.split(",") if s.strip()]
    else:
        sites = load_sites()
        if args.limit > 0:
            sites = sites[:args.limit]
    print("=" * 70)
    print(f"🗺️  SITE PROFILER — {len(sites)} sites")
    print("=" * 70)

    profiles = []
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception:
            try:
                import config
                browser = await p.chromium.launch(
                    headless=True, executable_path=config.CHROME_PATH)
            except Exception:
                browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent=UA, locale="en-GB",
            viewport={"width": 1440, "height": 900})
        sem = asyncio.Semaphore(args.concurrency)

        tasks = [profile_site(context, s, sem) for s in sites]
        results = await asyncio.gather(*tasks)
        profiles = [r for r in results if r]

        await context.close()
        await browser.close()

    # Merge with existing profiles (single-site runs preserve others)
    existing = {}
    if os.path.exists(PROFILES_FILE) and args.site:
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                for ep in json.load(f):
                    existing[ep["domain"]] = ep
        except Exception:
            pass
    for prof in profiles:
        existing[prof["domain"]] = prof

    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(list(existing.values()), f, ensure_ascii=False, indent=1)

    print_summary(profiles)
    print(f"\n💾 Profiles: {PROFILES_FILE} | Indexes: {INDEX_DIR}/")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
