"""
DEAL MINER — coupon sites ke pages se DEALS (title + link) nikalta hai.

Coupon website par sirf codes nahi, "deals"/offers bhi hote hain (jaise
"Get Free Shipping", "Browse Clearance: Save up to 41% off", "Sign Up for
Emails"). Ye module HTML se woh offer text + link extract karta hai.

Usage:
    from deal_miner import mine_deals
    deals = mine_deals(html_text, url)   # [{"title", "url"}, ...]
"""
import html as html_mod
import re
from urllib.parse import urljoin

# Sirf yeh strong deal-phrases wale text deals mane jayenge (noise kam).
DEAL_RE = re.compile(
    r"(free\s+(shipping|delivery|returns)"
    r"|\d{1,3}\s*%\s*off"
    r"|save\s+(up\s+to\s+)?\d{1,3}\s*%"
    r"|\$\s?\d+\s+off|£\s?\d+\s+off|€\s?\d+\s+off"
    r"|clearance|flash\s+sale|deal\s+of\s+the\s+day"
    r"|bogo|buy\s+\d+\s+get\s+\d+"
    r"|first\s+order|new\s+customer"
    r"|\d{1,3}\s*%\s+(discount|off))", re.I)

# "Subscribe/Sign up" sirf tab deal jab koi value (% ya $ off) saath ho
SUBSCRIBE_RE = re.compile(
    r"(subscribe|sign\s*up|newsletter)[^.]{0,40}?"
    r"(\d{1,3}\s*%|[$£€]\s?\d+)", re.I)

# Competitor "Similar Deals" entries: "SomeOtherBrand: 30% off" — drop (doosre brand)
COMPETITOR_DEAL_RE = re.compile(
    r"^([A-Z][A-Za-z0-9'&.\-]*(?:\s+[A-Z][A-Za-z0-9'&.\-]*){0,4}):\s*"
    r"(\d{1,3}\s*%\s*off|[$£€]\s?\d+[.,]?\d*\s*off)", re.I)
GENERIC_PREFIX_RE = re.compile(
    r"^(top|best|verified|storewide|deal|offer|code|coupon|today|"
    r"exclusive|new|sitewide|extra|free)", re.I)

# Nav/footer junk jinhe deal na manein (browsing links)
JUNK_RE = re.compile(
    r"(promo\s+codes?|discount\s+codes?|voucher\s+codes?|coupon\s+codes?"
    r"|^stores?$|^categories?$|^blog$|sign\s*in|log\s*in|privacy|terms)",
    re.I)

ANCHOR_RE = re.compile(r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                       re.I | re.S)
BUTTON_RE = re.compile(r'<button\b[^>]*>(.*?)</button>', re.I | re.S)
LI_RE = re.compile(r'<li\b[^>]*>(.*?)</li>', re.I | re.S)

MAX_TITLE = 140
MAX_PER_PAGE = 25


def _clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html_mod.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_deal(title):
    if not title or not (6 <= len(title) <= MAX_TITLE):
        return False
    if JUNK_RE.search(title):
        return False
    m = COMPETITOR_DEAL_RE.match(title)
    if m and not GENERIC_PREFIX_RE.match(m.group(1)):
        return False
    return bool(DEAL_RE.search(title) or SUBSCRIBE_RE.search(title))


def mine_deals(html_text, url=""):
    """Ek coupon-site page se deals. Return: [{"title", "url"}, ...]"""
    if not html_text:
        return []
    found = {}
    order = []

    def add(title, href):
        title = _clean(title)
        if not _is_deal(title):
            return
        if href.startswith("/") and url:
            href = urljoin(url, href)
        if not (href or url):
            return
        key = (title.lower(), href)
        if key in found:
            return
        found[key] = True
        order.append({"title": title, "url": href or url})

    for m in ANCHOR_RE.finditer(html_text):
        add(m.group(2), m.group(1))
    for m in BUTTON_RE.finditer(html_text):
        add(m.group(1), "")
    for m in LI_RE.finditer(html_text):
        add(m.group(1), "")
    return order[:MAX_PER_PAGE]
