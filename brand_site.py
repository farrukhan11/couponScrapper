"""
BRAND SITE — brand ki APNI website se deals/intel nikalta hai:

  - sale      : sale page par sabse zyada discount wala product (% off) = deal
  - shipping  : poori shipping policy ka text (+ free-shipping threshold)
  - newsletter: newsletter signup offer ("10% off", "$10 off")

Shopify-first (products.json structured data) -> warna homepage se
sale/shipping/newsletter pages dhoond kar HTML/JSON-LD se parse.

Usage:
    from brand_site import scrape_brand_site, brand_domain
    rows = scrape_brand_site("Brodo Broth|brodo.com", region="us")
"""
import html as html_mod
import json
import re
import time
from urllib.parse import urljoin

import httpx

from brand_matcher import parse_brand

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CURRENCY = {"us": "$", "uk": "£", "gb": "£", "eu": "€"}

SALE_PATHS = ["/collections/sale", "/collections/clearance", "/collections/all",
              "/sale", "/deals", "/clearance", "/outlet"]
SHIPPING_PATHS = ["/policies/shipping-policy", "/shipping-policy",
                  "/pages/shipping", "/pages/shipping-policy",
                  "/shipping", "/help/shipping", "/delivery",
                  "/pages/delivery", "/faq", "/pages/faq"]
NEWSLETTER_PATHS = ["/pages/newsletter", "/newsletter", "/subscribe"]

SALE_LINK_RE = re.compile(r"sale|clearance|outlet|deals?|discount", re.I)
SHIP_LINK_RE = re.compile(r"shipping|delivery|returns?", re.I)
NEWS_LINK_RE = re.compile(r"newsletter|subscribe|sign[-_ ]?up|email", re.I)

MAX_DETAILS = 8000
HTTP_TIMEOUT = 25
DELAY = 0.4
MAX_LINK_FETCH = 6

A_RE = re.compile(r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)


# ============================================================
# HELPERS
# ============================================================
def brand_domain(brand):
    """'Brodo Broth|brodo.com' -> 'brodo.com'"""
    try:
        d = (parse_brand(brand).get("domain") or "").strip().lower()
        return d.replace("www.", "")
    except Exception:
        return ""


def _text_from_html(html):
    if not html:
        return ""
    html = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style\b.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html_mod.unescape(html)
    return re.sub(r"\s+", " ", html).strip()


def _client():
    return httpx.Client(http2=True, timeout=HTTP_TIMEOUT, follow_redirects=True,
                        headers={"User-Agent": UA})


def _get(client, url, retries=1):
    for attempt in range(retries + 1):
        try:
            r = client.get(url)
            if r.status_code == 200 and r.text:
                return r.text
            if r.status_code == 429 and attempt < retries:
                time.sleep(2.0)
                continue
            return None
        except Exception:
            if attempt < retries:
                time.sleep(1.0)
                continue
            return None
    return None


def _links(html, base):
    out = []
    for m in A_RE.finditer(html or ""):
        href = (m.group(1) or "").strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        text = _text_from_html(m.group(2))[:80]
        out.append((text, urljoin(base, href)))
    return out


def _pick_links(links, pattern, limit=MAX_LINK_FETCH):
    seen, out = set(), []
    for text, url in links:
        if pattern.search(text) or pattern.search(url):
            if url in seen:
                continue
            seen.add(url)
            out.append(url)
        if len(out) >= limit:
            break
    return out


def _to_float(v):
    try:
        return float(re.sub(r"[^0-9.]", "", str(v)))
    except Exception:
        return None


# ============================================================
# SALE (max % off)
# ============================================================
def _shopify_sale(client, domain, sale_limit):
    best = None
    page, scanned = 1, 0
    while scanned < sale_limit and page <= 10:
        txt = _get(client, f"https://{domain}/products.json?limit=250&page={page}")
        if not txt:
            break
        try:
            products = (json.loads(txt) or {}).get("products") or []
        except Exception:
            break
        if not products:
            break
        for p in products:
            scanned += 1
            if scanned > sale_limit:
                break
            title = (p.get("title") or "").strip()
            handle = p.get("handle") or ""
            for v in (p.get("variants") or []):
                price = _to_float(v.get("price"))
                compare = _to_float(v.get("compare_at_price"))
                if not price or not compare or compare <= price:
                    continue
                pct = round((compare - price) / compare * 100)
                if pct <= 0:
                    continue
                if not best or pct > best["percent_off"]:
                    best = {"title": title,
                            "url": f"https://{domain}/products/{handle}",
                            "price_original": compare, "price_sale": price,
                            "percent_off": pct, "method": "shopify_json"}
        page += 1
    return best


def _page_max_discount(html, domain, region):
    """Ek page se max (percent, original, sale) nikaalo — JSON-LD + visible."""
    sym = CURRENCY.get((region or "us").lower(), "$")
    best = None

    def consider(a, b):
        nonlocal best
        if a and b and b > a:
            pct = round((b - a) / b * 100)
            if pct > 0 and (not best or pct > best[0]):
                best = (pct, b, a)

    # JSON-LD AggregateOffer: lowPrice/highPrice
    for m in re.finditer(r'"lowPrice"\s*:\s*"?([\d.]+)"?.{0,80}?"highPrice"\s*:\s*"?([\d.]+)"?',
                         html, re.I | re.S):
        consider(_to_float(m.group(1)), _to_float(m.group(2)))
    # listPrice + price
    for m in re.finditer(r'"listPrice"\s*:\s*"?([\d.]+)"?.{0,60}?"price"\s*:\s*"?([\d.]+)"?',
                         html, re.I | re.S):
        consider(_to_float(m.group(2)), _to_float(m.group(1)))
    # visible "$80.00 $120.00"
    for m in re.finditer(re.escape(sym) + r"\s?([\d,]+(?:\.\d{2})?)"
                         r"[^0-9]{1,40}?" + re.escape(sym) + r"\s?([\d,]+(?:\.\d{2})?)",
                         html):
        consider(_to_float(m.group(1)), _to_float(m.group(2)))
    return best


def _html_sale(client, domain, region, home_html, links):
    urls = []
    urls += _pick_links(links, SALE_LINK_RE)
    urls += [f"https://{domain}{p}" for p in SALE_PATHS]
    seen = set()
    for u in urls[:MAX_LINK_FETCH]:
        if u in seen:
            continue
        seen.add(u)
        html = _get(client, u)
        if not html:
            continue
        res = _page_max_discount(html, domain, region)
        if res:
            pct, orig, sale = res
            return {"title": f"Sale item ({pct}% off)", "url": u,
                    "price_original": orig, "price_sale": sale,
                    "percent_off": pct, "method": "html_fallback"}
    return None


def extract_sale(client, domain, region, sale_limit, home_html, links):
    best = _shopify_sale(client, domain, sale_limit)
    if best:
        return best
    return _html_sale(client, domain, region, home_html, links)


# ============================================================
# SHIPPING (poora policy text + threshold)
# ============================================================
THRESHOLD_RE = re.compile(
    r"free\s+(?:standard\s+|express\s+|economy\s+)?(?:shipping|delivery|returns)"
    r"(?:\s+(?:on|for)\s+(?:all\s+)?(?:orders?|purchases?))?"
    r"(?:\s+(?:over|above|from|\+|of))?\s*([$£€])?\s*([\d,]+(?:\.\d+)?)", re.I)


def extract_shipping(client, domain, region, home_html, links):
    urls = [f"https://{domain}{p}" for p in SHIPPING_PATHS]
    urls += _pick_links(links, SHIP_LINK_RE)
    seen = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        html = _get(client, u)
        if not html:
            continue
        text = _text_from_html(html)
        low = text.lower()
        if ("ship" not in low and "deliver" not in low) or len(text) < 120:
            continue
        amount = None
        m = THRESHOLD_RE.search(text)
        if m:
            amount = f"{m.group(1) or ''}{m.group(2)}".strip()
        return {"url": u, "details": text[:MAX_DETAILS], "amount": amount}
    return None


# ============================================================
# NEWSLETTER (text-only)
# ============================================================
NEWSLETTER_RE = re.compile(
    r"(sign\s*up|subscribe|join|newsletter|unlock)[^.]{0,70}?"
    r"(get|save|receive|enjoy|extra|off|take)[^.]{0,40}?"
    r"(\d{1,3}\s*%\s*(?:off|discount|savings?)?"
    r"|[$£€]\s?\d+[.,]?\d*\s*(?:off|discount))", re.I)


def _newsletter_from(text, url):
    m = NEWSLETTER_RE.search(text or "")
    if not m:
        return None
    start = text.rfind(".", 0, m.start()) + 1
    end = text.find(".", m.end())
    snippet = text[start:end if end != -1 else m.end() + 40].strip()[:200]
    val = m.group(3)
    pct = re.sub(r"[^0-9]", "", val) if "%" in val else None
    amount = re.sub(r"[^0-9.,]", "", val) if "%" not in val else None
    return {"url": url, "text": snippet, "percent_off": pct, "amount": amount}


def extract_newsletter(client, domain, region, home_html, links):
    res = _newsletter_from(_text_from_html(home_html), f"https://{domain}/")
    if res:
        return res
    for u in [f"https://{domain}{p}" for p in NEWSLETTER_PATHS] \
            + _pick_links(links, NEWS_LINK_RE):
        html = _get(client, u)
        if not html:
            continue
        res = _newsletter_from(_text_from_html(html), u)
        if res:
            return res
    return None


# ============================================================
# MAIN
# ============================================================
def scrape_brand_site(brand, region="us", sale_limit=250):
    """Brand ki apni site se offer rows. Return: [dict, ...] (offers schema)."""
    domain = brand_domain(brand)
    if not domain:
        return []
    rows = []
    try:
        with _client() as client:
            home = _get(client, f"https://{domain}/")
            links = _links(home, f"https://{domain}/")
            time.sleep(DELAY)

            sale = extract_sale(client, domain, region, sale_limit, home, links)
            if sale:
                rows.append({"type": "sale", "title": sale["title"],
                             "url": sale["url"], "percent_off": sale["percent_off"],
                             "price_original": sale["price_original"],
                             "price_sale": sale["price_sale"],
                             "source_url": sale["url"], "method": sale["method"],
                             "via": "httpx"})

            ship = extract_shipping(client, domain, region, home, links)
            if ship:
                rows.append({"type": "shipping", "title": "Shipping policy",
                             "url": ship["url"], "amount": ship["amount"] or "",
                             "details": ship["details"], "source_url": ship["url"],
                             "method": "html_text", "via": "httpx"})

            news = extract_newsletter(client, domain, region, home, links)
            if news:
                rows.append({"type": "newsletter", "title": news["text"],
                             "url": news["url"], "percent_off": news["percent_off"] or "",
                             "amount": news["amount"] or "",
                             "source_url": news["url"], "method": "html_text",
                             "via": "httpx"})
    except Exception:
        pass
    return rows
