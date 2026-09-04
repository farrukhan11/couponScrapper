"""
ROUTER — brand (name ya URL) => har client site par uska brand-page URL.

Layers (cheapest-first):
  1   Local sitemap index  (site_profiles.json + data/sitemap_indexes/*)  — instant
  1b  Pattern probe        (robots hints / known patterns, e.g. groupon /discount-codes/{slug})
  2   Own search           (Google/Bing apne Playwright browser se — own_search.py, zero API)
  3   site_navigator       (browser automation fallback — optional --deep)

Cache: data/router_cache.json
"""
import asyncio
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlparse, quote_plus, unquote

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

PROFILES_FILE = "site_profiles.json"
INDEX_DIR = "data/sitemap_indexes"
CACHE_FILE = "data/router_cache.json"

# Ye coupon aggregator site nahi hai (client list se flag)
SKIP_SITES = {"myjuniper.co.uk"}

# Iski sitemap deals deti hai (brand pages nahi) — sirf pattern probe use hogi
EXCLUDE_SITEMAP_SITES = {"groupon.co.uk"}

# Layer 1b extra patterns (search ki zaroorat na pade)
EXTRA_PATTERNS = {
    "savoo.co.uk": "https://www.savoo.co.uk/brands/{slug}-discount-codes",
}

# Sitemap index jo brand pages nahi deti — known pattern se probe hogi
SITE_OVERRIDES = {
    "groupon.co.uk": {"pattern": "https://www.groupon.co.uk/discount-codes/{slug}"},
}

SUFFIXES = ("-voucher-codes", "-discount-codes", "-coupon-codes", "-coupons",
            "-promo-codes", "-vouchers", "-deals", "-discount-code",
            "-coupon-code", "-promo-code", "-voucher", "-discount", "-offers")

BRAND_FIRST_SEGMENTS = {
    "coupons", "coupon", "stores", "store", "shop", "shops", "brand",
    "brands", "voucher-codes", "discount-codes", "vouchers", "deals",
    "retailer", "retailers", "merchant", "merchants",
}

NON_BRAND_SLUGS = {
    "about", "about-us", "contact", "contact-us", "blog", "news", "terms",
    "privacy", "faq", "help", "login", "signup", "register", "search",
    "category", "categories", "jobs", "press", "advertise", "apps", "home",
    "black-friday", "cyber-monday", "christmas", "mothers-day-coupons",
    "fathers-day-coupons", "memorial-day-coupons", "halloween-coupons",
    "cyber-monday-coupons", "black-friday-coupons", "other",
}


# ============================================================
# BRAND INPUT NORMALIZATION
# ============================================================
def brand_to_slugs(brand):
    """Mixed input (name ya URL) -> candidate slugs.
    'shemed.co.uk' -> ['shemed'], 'Boohoo' -> ['boohoo']"""
    b = brand.strip().lower()
    domain = ""
    if "." in b:
        if "//" not in b:
            b = "https://" + b
        p = urlparse(b)
        domain = (p.netloc or "").lower()
        b = domain.replace("www.", "")
    base = re.sub(r"\.(com|co\.uk|org\.uk|uk|net|org|store|shop|io|co|us|site|online|deals)$", "", b)
    slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    slugs = []
    if slug:
        slugs.append(slug)
    j = slug.replace("-", "")
    if j and j not in slugs:
        slugs.append(j)
    return {"raw": brand.strip(), "domain": domain, "slugs": slugs}


def join_key(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ============================================================
# LAYER 1 — LOCAL INDEX
# ============================================================
def slug_from_url(url, domain):
    """Index URL se brand slug nikalo (pattern-agnostic)."""
    p = urlparse(url)
    host = p.netloc.lower().replace("www.", "", 1) if p.netloc.startswith("www.") else p.netloc.lower()
    base_domain = domain.replace("www.", "")
    if host.endswith("." + base_domain):
        pre = host[: -len("." + base_domain)]
        if pre and pre != "www" and "." not in pre:
            return pre
        return None
    if host not in (base_domain, "www." + base_domain):
        return None  # CDN/external sitemap pollution
    segs = [s for s in p.path.split("/") if s]
    if not segs:
        return None
    if len(segs) > 1 and segs[0].lower() in BRAND_FIRST_SEGMENTS:
        cand = segs[1]
    else:
        cand = segs[0]
    cand = cand.lower()
    cand = re.sub(r"\.(html?|php|aspx)$", "", cand)
    for suf in SUFFIXES:
        if cand.endswith(suf) and len(cand) > len(suf) + 1:
            cand = cand[: -len(suf)]
            break
    if not cand or cand in NON_BRAND_SLUGS or re.match(r"^\d+$", cand):
        return None
    if len(cand) < 2 or len(cand) > 60:
        return None
    return cand


class BrandIndex:
    INDEX_CACHE = "data/index_cache.json"

    def __init__(self):
        self.domains = {}   # domain -> {join_key: (raw_slug, url)}
        self.patterns = {}  # domain -> top pattern string

    def _sources_fresh(self, cache_ts):
        """Index/profile files cache se purane to nahi?"""
        paths = [PROFILES_FILE]
        if os.path.isdir(INDEX_DIR):
            paths += [os.path.join(INDEX_DIR, f) for f in os.listdir(INDEX_DIR)]
        for p in paths:
            if os.path.getmtime(p) > cache_ts:
                return False
        return True

    def build(self):
        # Fast path: prebuilt cache
        try:
            with open(self.INDEX_CACHE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if self._sources_fresh(cache.get("built", 0)):
                self.domains = {d: {k: tuple(v) for k, v in m.items()}
                                for d, m in cache["domains"].items()}
                self.patterns = cache.get("patterns", {})
                print(f"✅ Layer 1 index (cached): {len(self.domains)} sites, "
                      f"{sum(len(m) for m in self.domains.values())} brand slugs")
                return sum(len(m) for m in self.domains.values())
        except Exception:
            pass

        if not os.path.exists(PROFILES_FILE):
            print(f"❌ {PROFILES_FILE} nahi mila — pehle site_profiler.py chalao")
            return 0
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            profiles = json.load(f)
        domains = set()
        for p in profiles:
            domains.add(p["domain"])
            if p.get("sitemap", {}).get("top_patterns"):
                self.patterns[p["domain"]] = p["sitemap"]["top_patterns"][0]["pattern"]
        loaded = 0
        for dom in domains:
            if dom in EXCLUDE_SITEMAP_SITES:
                continue
            idx_path = os.path.join(INDEX_DIR, f"{dom}.json")
            if not os.path.exists(idx_path):
                continue
            try:
                with open(idx_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            mapping = {}
            for url in data.get("brand_urls", []) + data.get("root_slug_urls", []):
                slug = slug_from_url(url, dom)
                if slug:
                    mapping.setdefault(join_key(slug), (slug, url))
            if mapping:
                self.domains[dom] = mapping
                loaded += len(mapping)

        # Cache save for next runs
        try:
            os.makedirs("data", exist_ok=True)
            with open(self.INDEX_CACHE, "w", encoding="utf-8") as f:
                json.dump({"built": time.time(),
                           "domains": {d: {k: list(v) for k, v in m.items()}
                                       for d, m in self.domains.items()},
                           "patterns": self.patterns}, f, ensure_ascii=False)
        except Exception:
            pass
        print(f"✅ Layer 1 index: {len(self.domains)} sites, {loaded} brand slugs")
        return loaded

    def lookup(self, slugs):
        """Candidate slugs se har domain par best match."""
        hits = {}
        keys = [join_key(s) for s in slugs if s]
        for dom, idx in self.domains.items():
            best = None
            best_score = 0
            for key in keys:
                if key in idx:
                    best, best_score = idx[key], 100
                    break
            if not best and keys:
                for jk, val in idx.items():
                    for key in keys:
                        if len(key) < 4:
                            continue
                        if key in jk:
                            # index slug brand ko contain karta hai (best partial)
                            score = 70 - abs(len(jk) - len(key))
                        elif jk in key and len(jk) >= 5:
                            # index slug brand ka prefix/abbrev ho sakta hai
                            score = 50 - abs(len(jk) - len(key))
                        else:
                            continue
                        if score > best_score:
                            best, best_score = val, score
            if best:
                hits[dom] = {"slug": best[0], "url": best[1], "layer": 1}
        return hits


# ============================================================
# LAYER 1b — PATTERN PROBE
# ============================================================
def construct_pattern_url(pattern, slug, domain):
    """'{x}' tail placeholders hatao — sirf {slug} tak ka pattern rakho."""
    clean = pattern.split("/{x}")[0].split("{x}")[0]
    if "{slug}." in clean and "." + domain.replace("www.", "") in clean:
        return "https://" + clean.replace("{slug}", slug)
    return "https://" + domain + clean.replace("{slug}", slug)


def probe_url_ok(url):
    """URL zinda hai? 404/soft-404/challenge reject. (sync — to_thread se chalao)"""
    try:
        import requests
        r = requests.get(url, timeout=12, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
            allow_redirects=True)
        if r.status_code == 200:
            low = r.text[:6000].lower()
            bad = ("page not found", "404", "no coupons found", "0 coupons",
                   "we couldn't find", "no offers found", "nothing found")
            if any(b in low for b in bad):
                return False
            return True
        if r.status_code in (403, 503):
            return True  # tentative — scrape stage verify karegi
        return False
    except Exception:
        return False


async def resolve_pattern_sites(missing, brand_slugs, page, cache_key, cache):
    """robots hints / known patterns se URL construct + verify.
    Saare doms CONCURRENT probe hote hain (12s timeouts warna bahut lamba)."""
    profiles = {}
    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            for p in json.load(f):
                profiles[p["domain"]] = p
    except Exception:
        return {}

    async def probe_dom(dom, pattern):
        for slug in brand_slugs:
            url = construct_pattern_url(pattern, slug, dom)
            if await asyncio.to_thread(probe_url_ok, url):
                return dom, {"slug": slug, "url": url, "layer": "1b"}
        return None

    tasks = []
    for dom in missing:
        pattern = None
        if dom in SITE_OVERRIDES and "pattern" in SITE_OVERRIDES[dom]:
            pattern = SITE_OVERRIDES[dom]["pattern"]
        elif dom in EXTRA_PATTERNS:
            pattern = EXTRA_PATTERNS[dom]
        elif profiles.get(dom, {}).get("robots_hints"):
            pattern = list(profiles[dom]["robots_hints"].keys())[0]
        elif profiles.get(dom, {}).get("sitemap", {}).get("top_patterns"):
            top = profiles[dom]["sitemap"]["top_patterns"][0]["pattern"]
            if "{slug}" in top:
                pattern = top
        if pattern:
            tasks.append(probe_dom(dom, pattern))

    if not tasks:
        return {}
    results_list = await asyncio.gather(*tasks)
    return {r[0]: r[1] for r in results_list if r}


# ============================================================
# LAYER 2 — RESULT SCORING (own Google/Bing search own_search.py se)
# ============================================================
def score_result(url, domain, brand_slugs):
    """Domain match + brand slug host/path me zaroori — warna 0 (reject)."""
    try:
        p = urlparse(url)
        host = p.netloc.lower()
        if not (host == domain or host.endswith("." + domain)
                or host == "www." + domain):
            return 0
        path = p.path.lower()
        full = host + path
        # Brand slug zaroori (L2 false-positives roko)
        slug_hit = False
        for s in brand_slugs:
            if len(s) >= 3 and s in join_key(full):
                slug_hit = True
                break
        if not slug_hit:
            return 0
        score = 30
        if any(w in path for w in ("coupon", "voucher", "discount", "promo", "deal", "store", "shop", "brand")):
            score += 20
        if len(path) > 120:
            score -= 10
        return score
    except Exception:
        return 0


# ============================================================
# MAIN RESOLVER
# ============================================================
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def _load_target_domains():
    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            return [p["domain"] for p in json.load(f)]
    except Exception:
        return []


def _clean_site_url(url):
    """__cf/utm tokens hatao; homepage URLs reject ke liye path nikalo."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    sp = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(sp.query)
         if not k.startswith("__cf") and not k.startswith("utm_")]
    return urlunsplit((sp.scheme, sp.netloc, sp.path or "/", urlencode(q), ""))


async def resolve_brands(brands, sites_filter=None, site_search=True, use_google=False, verbose=True):
    """Return {brand: {domain: {"url","slug","layer"}}}"""
    index = BrandIndex()
    index.build()
    cache = load_cache()

    targets = [d for d in _load_target_domains() if d not in SKIP_SITES]
    if sites_filter:
        wanted = {s.lower().replace("www.", "") for s in sites_filter}
        targets = [d for d in targets if d in wanted]
    if verbose:
        print(f"🎯 Target sites: {len(targets)}")

    results = {}
    search_queue = []  # (brand, dom, info)

    # Pass 1: L1 lookups + cache (fast)
    pending_probes = []  # (brand, info, missing)
    for brand in brands:
        info = brand_to_slugs(brand)
        results[brand] = {}
        cached = cache.get(brand.lower(), {})
        missing = []
        for dom in targets:
            if dom in cached:
                results[brand][dom] = dict(cached[dom], cached=True)
            elif dom in index.domains:
                lookup = index.lookup(info["slugs"])
                if dom in lookup:
                    results[brand][dom] = lookup[dom]
                else:
                    missing.append(dom)
            else:
                missing.append(dom)
        if missing:
            pending_probes.append((brand, info, missing))

    # Pass 2: L1b probes — SAARE brands parallel
    async def probe_brand(brand, info, missing):
        resolved = await resolve_pattern_sites(missing, info["slugs"], None, brand, cache)
        return brand, info, missing, resolved

    if pending_probes:
        print(f"\n🧩 Layer 1b: {sum(len(m) for _, _, m in pending_probes)} "
              f"pattern-probes ({len(pending_probes)} brands parallel)...")
        probe_results = await asyncio.gather(*(probe_brand(b, i, m) for b, i, m in pending_probes))
        for brand, info, missing, resolved in probe_results:
            for dom, hit in resolved.items():
                results[brand][dom] = hit
            for dom in missing:
                if dom not in results[brand]:
                    search_queue.append((brand, dom, info))

    # Layer 2 — ON-SITE SEARCH (site ke apne search box se — 100% own infra)
    if search_queue and site_search:
        SITE_CAP = 24
        pairs = [(b, d) for b, d, _ in search_queue[:SITE_CAP]]
        if len(search_queue) > SITE_CAP:
            print(f"\n⚠️ On-site search: {len(search_queue)} pairs — cap {SITE_CAP} laga")
        print(f"\n🌐 Layer 2: {len(pairs)} pairs — site ke apne search se (own browser)...")
        url_map = await _deep_navigate(pairs)
        for (b, d), url in url_map.items():
            clean = _clean_site_url(url)
            path = urlparse(clean).path
            if path in ("", "/"):
                print(f"   ⛔ {b} @ {d} — homepage mila, brand page nahi (reject)")
                continue
            info = brand_to_slugs(b)
            results[b][d] = {"slug": info["slugs"][0] if info["slugs"] else "",
                             "url": clean, "layer": 2}

    # Layer 3 — OWN Google/Bing (optional — CAPTCHA manually solve karna pad sakta hai)
    remaining = [(b, d) for b in brands for d in targets if d not in results[b]]
    if use_google and remaining:
        print(f"\n🔍 Layer 3: {len(remaining)} pairs — own Google/Bing search...")
        from own_search import OwnSearch
        searcher = OwnSearch(max_queries=40)
        try:
            for i, (brand, dom) in enumerate(remaining, 1):
                if searcher.disabled:
                    print("   ⚠️ Search disabled — baaki skip")
                    break
                info = brand_to_slugs(brand)
                urls = await searcher.web_search(f'site:{dom} "{info["raw"]}"')
                slugs = [join_key(s) for s in info["slugs"] if s]
                best, best_score = None, 0
                for u in urls:
                    s = score_result(u, dom, slugs)
                    if s > best_score:
                        best, best_score = u, s
                if best:
                    results[brand][dom] = {"slug": info["slugs"][0] if info["slugs"] else "",
                                           "url": best, "layer": 3}
                    print(f"   [{i}/{len(remaining)}] ✅ {brand} @ {dom} → {best[:70]}")
                else:
                    print(f"   [{i}/{len(remaining)}] ∅  {brand} @ {dom}")
        finally:
            await searcher.close()

    # Cache write (sirf naye entries)
    for brand in brands:
        key = brand.lower()
        entry = cache.setdefault(key, {})
        for dom, hit in results[brand].items():
            if not hit.get("cached"):
                entry[dom] = {k: hit[k] for k in ("slug", "url", "layer")}
    save_cache(cache)
    return results


async def _deep_navigate(pairs):
    """Sync site_navigator ko thread me chalao."""
    import time as _time
    import site_navigator as sn
    from playwright.sync_api import sync_playwright

    def worker(pairs):
        out = {}
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception:
                import config
                browser = p.chromium.launch(headless=True, executable_path=config.CHROME_PATH)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
            page = context.new_page()
            for brand, site in pairs:
                site_url = f"https://{site}"
                try:
                    target = sn.find_brand_page(page, site_url, brand)
                    if target:
                        out[(brand, site)] = target
                        print(f"   🧭 {brand} @ {site} → {target[:70]}")
                except Exception as e:
                    print(f"   ⚠️ {brand} @ {site}: {str(e)[:60]}")
                _time.sleep(random.uniform(1, 2))
            browser.close()
        return out

    return await asyncio.to_thread(worker, pairs)


# ============================================================
# CLI (test)
# ============================================================
async def _main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--brands", default="brands.txt")
    parser.add_argument("--test", type=int, default=0)
    parser.add_argument("--sites", default="", help="comma-separated domain filter")
    parser.add_argument("--no-sitesearch", action="store_true", help="Layer 2 on-site search skip (fast)")
    parser.add_argument("--google", action="store_true", help="Layer 3: apne Chrome se Google/Bing (CAPTCHA ho sakta hai)")
    args = parser.parse_args()

    with open(args.brands, "r", encoding="utf-8-sig") as f:
        brands = [l.strip() for l in f if l.strip()]
    if args.test:
        brands = brands[:args.test]
    sites_filter = [s for s in args.sites.split(",") if s.strip()] if args.sites else None

    print("=" * 70)
    print(f"🧭 ROUTER — {len(brands)} brands")
    print("=" * 70)
    results = await resolve_brands(brands, sites_filter=sites_filter,
                                   site_search=not args.no_sitesearch,
                                   use_google=args.google)

    print("\n" + "=" * 70)
    for brand in brands:
        hits = results.get(brand, {})
        layers = {}
        for h in hits.values():
            layers[h["layer"]] = layers.get(h["layer"], 0) + 1
        print(f"\n🏷️  {brand}: {len(hits)} sites")
        for dom, h in sorted(hits.items(), key=lambda x: str(x[1]["layer"])):
            print(f"   L{h['layer']:<3} {dom:<32} {h['url'][:75]}")


if __name__ == "__main__":
    asyncio.run(_main())
