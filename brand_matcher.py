"""
BRAND MATCHER — brand name ko store-index se dhundta hai.

Match tiers (best pehle):
  exact    — "timmmedical" == index key
  prefix   — "timmmedical" starts with index key (ya ulta), min 4 chars
  contains — ek doosre mein contain (min 5 chars, cautious)
  fuzzy    — difflib ratio >= 0.85 (typos ke liye)

Usage:
  python brand_matcher.py "Timm Medical|timmmedical.com" ...
"""
import difflib
import json
import re
import sys

INDEX_FILE = "data/stores_index.json"

TLD_RE = re.compile(
    r"\.(com|co\.uk|org\.uk|uk|net|org|store|shop|io|co|us|site|online|deals)$", re.I)

# brand name ke saath query-words likh de to strip ho jate hain
BRAND_STOPWORDS = {
    "promo", "promos", "coupons", "coupon", "vouchers", "voucher",
    "discount", "discounts", "code", "codes", "deal", "deals",
    "offer", "offers", "sales", "sale",
}


def join_key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def sanitize_name(name):
    words = [w for w in (name or "").split()
             if w.lower().strip(",.|-") not in BRAND_STOPWORDS]
    return " ".join(words).strip() if words else (name or "").strip()


def parse_brand(raw):
    """'Timm Medical|timmmedical.com' | 'Timm Medical' | 'timmmedical.com'"""
    s = (raw or "").strip()
    name, domain = s, ""
    if "|" in s:
        name, domain = s.split("|", 1)
    name = sanitize_name(name.strip())
    domain = domain.strip().lower().replace("https://", "").replace("http://", "").strip("/")
    if not domain and "." in name and " " not in name and TLD_RE.search(name):
        domain = name.lower()
        name = TLD_RE.sub("", domain).replace("-", " ").strip()
    return {"name": name, "domain": domain}


def brand_keys(brand):
    """Brand ke saare candidate keys (variations + domain)."""
    pb = parse_brand(brand)
    name = pb["name"]
    keys = []

    def add(k):
        k = join_key(k)
        if k and k not in keys:
            keys.append(k)

    add(name)
    # camelCase/underscore split: Arq8 -> arq8, MobilePixels -> mobilepixels
    spaced = re.sub(r"[-_]+", " ", name)
    for part in spaced.split():
        add(part)
    add(spaced)
    if pb["domain"]:
        dcore = TLD_RE.sub("", pb["domain"].replace("www.", ""))
        add(dcore)
        for part in re.split(r"[-_.]", dcore):
            add(part)
    return {"name": pb["name"], "domain": pb["domain"], "keys": keys}


def find_matches(brand, index, partials=True):
    """Ek brand ke liye har site par best match.
    Return: {domain: {"tier", "slug", "url"}}
    Fast path: exact dict-lookup (O(1)) — partial scan sirf bina-exact domains par."""
    info = brand_keys(brand)
    keys = [k for k in info["keys"] if len(k) >= 3]
    hits = {}
    for dom, mapping in index.items():
        # ---- Pass 1: exact lookups ----
        best, best_tier, best_score = None, None, 0
        for i, k in enumerate(keys):
            val = mapping.get(k)
            if val:
                score = 100 + len(keys) - i
                if score > best_score:
                    best, best_tier, best_score = (val[0], val[1]), "exact", score
        if best:
            hits[dom] = {"tier": best_tier, "slug": best[0], "url": best[1]}
            continue
        if not partials:
            continue
        # ---- Pass 2: prefix/contains/fuzzy scan ----
        for key, (slug, url) in mapping.items():
            for i, k in enumerate(keys):
                tier, score = _score(k, key)
                if not score:
                    continue
                score += len(keys) - i
                if score > best_score:
                    best, best_tier, best_score = (slug, url), tier, score
        if best:
            hits[dom] = {"tier": best_tier, "slug": best[0], "url": best[1]}
    return hits


def _score(bkey, ikey):
    """(tier, score) — bkey=brand key, ikey=index key."""
    if not bkey or not ikey:
        return None, 0
    if bkey == ikey:
        return "exact", 100
    # prefix dono taraf (min 4) — topper/betopper jaisa suffix-fuzzy yahan nahi aata
    if len(bkey) >= 4 and len(ikey) >= 4:
        if ikey.startswith(bkey):
            return "prefix", 80 - abs(len(ikey) - len(bkey))
        if bkey.startswith(ikey) and len(ikey) >= 5:
            return "prefix", 60 - abs(len(ikey) - len(bkey))
    # contains (dono >=5)
    if len(bkey) >= 5 and len(ikey) >= 5:
        if bkey in ikey:
            return "contains", 50 - abs(len(ikey) - len(bkey))
        if ikey in bkey:
            return "contains", 40 - abs(len(ikey) - len(bkey))
    # fuzzy (typos)
    ratio = difflib.SequenceMatcher(None, bkey, ikey).ratio()
    if ratio >= 0.85 and min(len(bkey), len(ikey)) >= 5:
        return "fuzzy", int(ratio * 30)
    return None, 0


def load_index():
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    brands = [a for a in sys.argv[1:] if a.strip() and not a.startswith("--")]
    partials = "--no-partials" not in sys.argv
    if not brands:
        print('Usage: python brand_matcher.py "Timm Medical|timmmedical.com" [--no-partials]')
        sys.exit(1)
    index = load_index()
    print(f"🗂️  Index: {len(index)} sites, {sum(len(m) for m in index.values())} stores\n")
    for b in brands:
        hits = find_matches(b, index, partials=partials)
        info = brand_keys(b)
        print(f"🏷️  {info['name']}: {len(hits)} sites")
        for dom, h in sorted(hits.items()):
            print(f"   [{h['tier']:<8}] {dom:<34} {h['url'][:70]}")
        print()
