"""
SEARCH — Google (Serper.dev) client.

Client ka manual flow: Google par "<brand> coupon code" (US) ya
"<brand> discount code / voucher code" (UK) search → page-1 organic sites.

Usage:
    import search
    region, results = search.search_brand("Corsair|corsair.com")
    # region = "us"|"uk" ; results = [{"title","url","snippet"}]
"""
import re
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

import config

# Google SERP se sponsored + organic structured nikalne wala JS
GOOGLE_SERP_JS = r"""() => {
  const out = {ads: [], organic: []};
  const seen = new Set();
  document.querySelectorAll('div[data-text-ad]').forEach(block => {
    const a = block.querySelector('a[href]');
    if (!a) return;
    const h = block.querySelector('h3, [role="heading"]');
    const title = (h ? h.innerText : (a.innerText || '')).trim();
    const href = a.href;
    // destination (bina click): data-rw mein adurl, ya data-pcu
    const rw = a.getAttribute('data-rw') || '';
    const pcu = a.getAttribute('data-pcu') || '';
    const dtld = block.getAttribute('data-dtld') || a.getAttribute('data-dtld') || '';
    if (href && !seen.has(href)) {
      seen.add(href);
      out.ads.push({title, href, rw, pcu, dtld});
    }
  });
  const rso = document.querySelector('#rso') || document;
  rso.querySelectorAll('a').forEach(a => {
    const h3 = a.querySelector('h3');
    if (!h3) return;
    const href = a.href;
    if (!href || seen.has(href)) return;
    seen.add(href);
    out.organic.push({title: h3.innerText.trim(), href});
  });
  return out;
}"""

SERPER_URL = "https://google.serper.dev/search"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

DEFAULT_SUFFIX = {"us": ["coupon code"],
                  "uk": ["discount code", "voucher code"]}


# ============================================================
# BRAND HELPERS
# ============================================================
TLD_RE = re.compile(
    r"\.(com|co\.uk|org\.uk|uk|us|net|org|io|co|store|shop|de|ca|au|eu)$", re.I)


def parse_brand(brand):
    """`Name|domain` ya `Name|domain|uk` -> (name, domain, region_hint)."""
    parts = [p.strip() for p in (brand or "").split("|")]
    name = parts[0] if parts else ""
    domain = parts[1] if len(parts) > 1 else ""
    region = None
    if len(parts) > 2 and parts[2].lower() in ("us", "uk"):
        region = parts[2].lower()
    return name, domain, region


def brand_name(brand):
    return parse_brand(brand)[0]


def brand_domain(brand):
    _, d, _ = parse_brand(brand)
    d = (d or "").strip().lower()
    d = re.sub(r"^https?://", "", d).strip("/")
    d = d.split("/", 1)[0]
    return d.replace("www.", "")


def region_for(brand):
    _, _, hint = parse_brand(brand)
    if hint in ("us", "uk"):
        return hint
    gl = (getattr(config, "SEARCH_GL", "") or "").strip().lower()
    if gl in ("us", "uk"):
        return gl
    d = brand_domain(brand)
    if d.endswith(".co.uk") or d.startswith("uk.") or ".co.uk" in d:
        return "uk"
    return "us"


# ============================================================
# BRAND-RELEVANCE (ambiguity: "Doulton" vs "Royal Doulton")
# ============================================================
def _brand_keys(brand):
    name, dom, _ = parse_brand(brand)
    core = TLD_RE.sub("", dom.replace("www.", "")) if dom else ""
    keys = set()
    for s in (name, core):
        k = re.sub(r"[^a-z0-9]", "", (s or "").lower())
        if len(k) >= 4:
            keys.add(k)
    return keys


def _tokens(s):
    """Hyphen/underscore ko word-continuation maano:
    'royal-doulton' -> 'royaldoulton' (taake Royal Doulton detect ho),
    par dot/slash separator rahen ('doulton.com' -> 'doulton')."""
    parts = re.split(r"[^a-z0-9\-_]+", (s or "").lower())
    return [re.sub(r"[-_]", "", p) for p in parts if p]


def _same_site(url, brand):
    try:
        d = url.split("/")[2].lower().replace("www.", "")
    except Exception:
        return False
    return d == brand_domain(brand)


def is_other_brand_url(url, brand):
    """True agar URL kisi doosre (longer) brand ka ho — jaise 'royaldoulton'."""
    keys = _brand_keys(brand)
    if not keys:
        return False
    toks = _tokens(url)
    standalone = any(t in keys for t in toks)
    conflict = False
    for t in toks:
        if t in keys:
            continue
        for k in keys:
            if k in t and len(t) > len(k):
                conflict = True
    return conflict and not standalone


def _suffixes(region):
    raw = getattr(config, "SEARCH_QUERY_UK" if region == "uk"
                  else "SEARCH_QUERY_US", "") or ""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or DEFAULT_SUFFIX.get(region, ["coupon code"])


def build_queries(brand, region_override=None):
    region = region_override if region_override in ("us", "uk") else region_for(brand)
    name = brand_name(brand)
    return region, [f"{name} {s}" for s in _suffixes(region)]


# ============================================================
# SERPER
# ============================================================
def _is_adclick(low):
    return any(x in low for x in (
        "googleadservices", "/aclk", "google.com/goto", "google.com/pagead",
        "doubleclick.net", "googleadsservices"))


def _safe_ad_url(link):
    """Ad link se destination nikalo; Google ad-click links skip (cost se bachne)."""
    if not link or not link.startswith("http"):
        return None
    low = link.lower()
    if "adurl=" in low:
        try:
            q = parse_qs(urlparse(link).query)
            dest = (q.get("adurl") or q.get("u") or [""])[0]
            if dest:
                return unquote(dest)
        except Exception:
            pass
    if _is_adclick(low):
        return None
    return link


def _decode_google_href(href):
    if not href:
        return None
    try:
        p = urlparse(href)
        qs = parse_qs(p.query)
    except Exception:
        return href
    for k in ("adurl", "q", "url"):
        if k in qs and qs[k]:
            val = unquote(qs[k][0])
            if val.startswith("http"):
                return val
    return href


_TRACK_PARAMS = ("gad_", "gclid", "gbraid", "wbraid", "srsltid", "utm_",
                 "campaignid", "adgroupid", "adid", "sig", "gad_source")


def _strip_tracking(url):
    try:
        from urllib.parse import urlencode, urlunparse
        p = urlparse(url)
        qs = parse_qs(p.query)
        keep = {k: v for k, v in qs.items()
                if not any(k.lower().startswith(t) for t in _TRACK_PARAMS)}
        return urlunparse((p.scheme, p.netloc, p.path, p.params,
                           urlencode(keep, doseq=True), p.fragment))
    except Exception:
        return url


BAD_AD_HOSTS = ("clickcease.com", "ppcprotect.com", "doubleclick.net",
                "googleadservices.com", "googlesyndication.com", "2mdn.net",
                "adservice.google.com", "google.com")


def _clean_dest(dest):
    if not dest:
        return None
    dest = dest.split(",")[0].strip()     # comma-separated junk se bacho
    try:
        p = urlparse(dest)
    except Exception:
        return None
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    host = p.netloc.lower()
    if any(host == h or host.endswith("." + h) for h in BAD_AD_HOSTS):
        return None
    return _strip_tracking(dest)


def _ad_destination(a):
    """Ad block se destination (bina click): data-rw ka adurl > data-pcu > dtld."""
    rw = a.get("rw") or ""
    m = re.search(r"[?&]adurl=([^&]+)", rw)
    if m:
        d = _clean_dest(unquote(m.group(1)))
        if d:
            return d
    d = _clean_dest((a.get("pcu") or "").strip())
    if d:
        return d
    dtld = (a.get("dtld") or "").strip()
    if dtld and "." in dtld and " " not in dtld:
        return _clean_dest("https://" + dtld)
    return None


def google_ads(query, gl="us", num=10):
    """Google SERP render karke sponsored destinations nikaalo (bina ad-click)."""
    try:
        import renderer
    except Exception:
        return []
    url = ("https://www.google.com/search?q=" + quote_plus(query)
           + f"&num={num}&hl=en&gl={gl}&pws=0")
    r = renderer.render(url, eval_js=GOOGLE_SERP_JS, use_cache=False)
    data = r.get("data") or {}
    ads = []
    for a in (data.get("ads") or []):
        dest = _ad_destination(a)
        if dest:
            ads.append({"title": (a.get("title") or "").strip(),
                        "url": dest, "snippet": "", "is_ad": True})
    return ads


def serper_search(query, num=10, gl="us", include_ads=None):
    key = (getattr(config, "SERPER_API_KEY", "") or "").strip()
    if not key:
        raise RuntimeError("SERPER_API_KEY missing (.env mein daalo)")
    r = httpx.post(
        SERPER_URL,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={"q": query, "num": num, "gl": gl, "hl": "en"},
        timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Serper {r.status_code}: {r.text[:140]}")
    data = r.json() or {}
    out = []
    for item in (data.get("organic") or []):
        link = (item.get("link") or "").strip()
        if link.startswith("http"):
            out.append({"title": (item.get("title") or "").strip(),
                        "url": link,
                        "snippet": (item.get("snippet") or "").strip(),
                        "is_ad": False})
    if include_ads is None:
        include_ads = getattr(config, "SEARCH_INCLUDE_ADS", True)
    if include_ads:
        for item in (data.get("ads") or []):
            safe = _safe_ad_url((item.get("link") or item.get("url") or "").strip())
            if not safe:
                continue
            out.append({"title": (item.get("title") or "").strip(),
                        "url": safe,
                        "snippet": (item.get("description")
                                    or item.get("snippet") or "").strip(),
                        "is_ad": True})
    return out


def search_brand(brand, num=None, region_override=None):
    """Return (region, results) — saari queries merge + dedupe."""
    if num is None:
        num = getattr(config, "SEARCH_MAX_RESULTS", 10)
    region, queries = build_queries(brand, region_override)
    include_ads = getattr(config, "SEARCH_INCLUDE_ADS", True)
    results, seen = [], set()
    skipped_brand = 0
    for q in queries:
        hits = []
        try:
            hits = serper_search(q, num=num, gl=region)
            print(f"   🔎 '{q}' [{region}] → {len(hits)} organic")
        except Exception as e:
            print(f"   ⚠️ search fail '{q}': {str(e)[:100]}")
        if include_ads:
            try:
                gads = google_ads(q, gl=region, num=num)
                if gads:
                    print(f"      🅰️  sponsored: {len(gads)}")
                hits = hits + gads
            except Exception as e:
                print(f"      ⚠️ google ads fail: {str(e)[:80]}")
        for h in hits:
            u = h["url"]
            if u in seen:
                continue
            if _same_site(u, brand):
                continue    # brand ki apni site
            if is_other_brand_url(u, brand):
                skipped_brand += 1
                continue    # doosra brand (jaise Royal Doulton)
            seen.add(u)
            results.append(h)
    if skipped_brand:
        print(f"   🚫 {skipped_brand} other-brand results skip"
              f" (ambiguity filter)")
    return region, results


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for b in sys.argv[1:] or ["Corsair|corsair.com"]:
        reg, res = search_brand(b)
        print(f"\n{b} [{reg}] -> {len(res)} results")
        for r in res:
            print("   ", r["url"][:100])
