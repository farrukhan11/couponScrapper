"""
COUPON PIPELINE v2 — Router (Layer 1/1b/2/3) + Extraction.

Purana flow:  Google search -> URLs -> markdown -> regex
Naya flow:    Client sites -> Router resolve -> brand page scrape -> codes/deals

Usage:
  python coupon_pipeline.py                 # brands.txt ke saare brands
  python coupon_pipeline.py --test 2        # pehle 2 brands (test)
  python coupon_pipeline.py --sites savoo.co.uk,groupon.co.uk   # specific sites
  python coupon_pipeline.py --google        # Layer 3 apne Chrome se Google search
  python coupon_pipeline.py --no-sitesearch # Layer 2 on-site search skip (fast)
  python coupon_pipeline.py --fresh         # cache/seen-codes ignore karke chalao
"""
import asyncio
import csv
import os
import random
import re
import sys
from datetime import datetime

from playwright.async_api import async_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scraper import (mine_from_html, mine_from_text, looks_like_code,
                     clean_coupons, REVEAL_WORDS, DEAL_WORDS, MODAL_SELECTORS,
                     is_duplicate, mark_as_seen)
import router as site_router

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

BRANDS_FILE = "brands.txt"
REGION = "uk"
RESULTS_CSV = f"results_v2_{REGION}.csv"
SUMMARY_CSV = f"summary_v2_{REGION}.csv"
SEEN_FILE = "data/seen_codes_v2.json"
EXT_DIR = f"extension_codes_v2_{REGION}"


def set_region(region):
    """Output filenames region ke hisaab se."""
    global REGION, RESULTS_CSV, SUMMARY_CSV, EXT_DIR
    REGION = region.lower()
    RESULTS_CSV = f"results_v2_{REGION}.csv"
    SUMMARY_CSV = f"summary_v2_{REGION}.csv"
    EXT_DIR = f"extension_codes_v2_{REGION}"

CONCURRENCY = 5
PAGE_TIMEOUT = 25000
JS_WAIT = 3000
MAX_CLICKS = 15


# ============================================================
# EXTRACTION (scraper.py ke layers, async port)
# ============================================================
async def safe_wait(page, ms):
    try:
        await page.wait_for_timeout(ms)
    except Exception:
        pass


async def read_clipboard_async(page):
    try:
        txt = await page.evaluate("() => navigator.clipboard.readText()")
        return (txt or "").strip()
    except Exception:
        return ""


async def extract_after_click_async(page, before_text):
    codes = []
    clip = await read_clipboard_async(page)
    if clip:
        for part in re.split(r"\s+", clip):
            if looks_like_code(part):
                codes.append({"code": part, "method": "clipboard"})
    for sel in MODAL_SELECTORS:
        try:
            for el in await page.query_selector_all(sel):
                try:
                    if await el.is_visible():
                        codes.extend(mine_from_text(await el.inner_text(), require_digit=False))
                except Exception:
                    continue
        except Exception:
            pass
    try:
        html = await page.content()
        codes.extend(mine_from_html(html))
        for m in re.findall(r'<input[^>]+value=["\']([^"\']+)["\']', html):
            if looks_like_code(m):
                codes.append({"code": m, "method": "input_value"})
    except Exception:
        pass
    try:
        after_text = await page.inner_text("body")
        new_words = set(after_text.split()) - set(before_text.split())
        if new_words:
            codes.extend(mine_from_text(" ".join(new_words), require_digit=False))
    except Exception:
        pass
    return codes


async def click_reveal_buttons_async(page, brand, source_url, rows):
    """Reveal buttons click -> clipboard/modal/diff; deal links record."""
    try:
        buttons = await page.query_selector_all("button, a, [role='button']")
    except Exception:
        return
    clicked = 0
    for btn in buttons:
        if clicked >= MAX_CLICKS:
            break
        try:
            txt = ((await btn.inner_text()) or "").strip()
        except Exception:
            continue
        if not txt or len(txt) > 40:
            continue
        low = txt.lower()
        is_reveal = any(w in low for w in REVEAL_WORDS)
        is_deal = any(w in low for w in DEAL_WORDS)
        if not is_reveal and not is_deal:
            continue

        if is_reveal:
            try:
                before_text = await page.inner_text("body")
            except Exception:
                before_text = ""
            pages_before = len(page.context.pages)
            try:
                await btn.scroll_into_view_if_needed()
                await btn.click(timeout=5000)
                clicked += 1
                await safe_wait(page, random.randint(1200, 2200))
            except Exception:
                try:
                    await btn.click(timeout=3000, force=True)
                    clicked += 1
                    await safe_wait(page, 1500)
                except Exception:
                    continue
            if len(page.context.pages) > pages_before:
                new_page = page.context.pages[-1]
                try:
                    await new_page.wait_for_load_state("domcontentloaded", timeout=8000)
                    rows.extend(mine_from_html(await new_page.content()))
                except Exception:
                    pass
                try:
                    await new_page.close()
                except Exception:
                    pass
            rows.extend(await extract_after_click_async(page, before_text))
        elif is_deal:
            # Strict phrases — nav links ("Shoppingspout US", "Disclaimer") skip
            strict = ("get deal", "get offer", "shop now", "go to store",
                      "get reward", "activate deal", "claim deal", "get discount",
                      "see deal", "view deal", "use deal", "redeem",
                      "get code", "get coupon", "visit store", "grab deal")
            try:
                href = await btn.get_attribute("href")
                if href and href.startswith("http") and any(p in low for p in strict):
                    rows.append({"__deal__": True, "url": href, "label": txt})
            except Exception:
                continue


def is_challenge(html):
    head = html[:4000].lower()
    return ("just a moment" in head or "challenge-platform" in head
            or "access denied" in head or "px-captcha" in head)


async def brand_page_relevant(page, brand, hit):
    """Page usi brand ka hai? (generic store-list false-attribution roko)"""
    slug = (hit.get("slug") or "").lower()
    core = re.sub(r"[^a-z0-9]", "", brand.lower().split(".")[0])
    tokens = {t for t in (slug, core) if len(t) >= 4}
    if not tokens:
        return True
    try:
        title = (await page.title()).lower()
    except Exception:
        title = ""
    url_low = (page.url or "").lower()
    for t in tokens:
        if t in title or t in url_low:
            return True
    try:
        body = (await page.inner_text("body")).lower()
        joined = re.sub(r"[^a-z0-9]", "", body)
        for t in tokens:
            if joined.count(t) >= 3:
                return True
    except Exception:
        pass
    return False


ANALYTICS_RE = re.compile(r"^(GTM|UA|AW|DC|G|FB|IG)-?[A-Z0-9]{4,}$", re.I)
EXTRA_BAD = {"activated", "copied", "applied", "recommended", "update", "menu",
             "subscribe", "newsletter", "checkout", "wishlist"}
HIGH_CONFIDENCE_METHODS = {"clipboard", "html_attr", "input_value", "revealed_text"}


def code_noise(code):
    """Phrase/hash/widget junk reject."""
    if code in {b.upper() for b in EXTRA_BAD}:
        return True
    if code.count("-") > 1:
        return True
    if "-" in code:
        tail = code.split("-")[-1]
        if not any(ch.isdigit() for ch in tail):
            return True  # AT-HOME, UK-REGISTERED junk | HRC-15 theek
    digits = re.sub(r"\D", "", code)
    if len(digits) >= 4 and code.endswith(digits):
        return True  # widget IDs: DISCOUNTSEEKER7709
    if code.isalpha() and len(code) > 10:
        return True  # TESTIMONIALS, EMPFEHLUNGEN
    if re.match(r"^[A-F0-9]{12,}$", code):
        return True  # hex hash A35C4DCBCE1372DA
    return False


def post_process_codes(codes):
    """COPIED-prefix strip, noise reject, confidence tag, dedupe."""
    seen = set()
    out = []
    for c in codes:
        code = c["code"].strip().upper()
        if code.startswith("COPIED") and len(code) > 6:
            code = code[6:]
        if not looks_like_code(code) or ANALYTICS_RE.match(code) or code_noise(code):
            continue
        if code in seen:
            continue
        seen.add(code)
        conf = "high" if c.get("method") in HIGH_CONFIDENCE_METHODS else "low"
        out.append({"code": code, "method": c.get("method", "text"), "confidence": conf})
    return out


async def scrape_brand_page(context, brand, dom, hit, sem, results, seen, stats):
    async with sem:
        url = hit["url"]
        rows = []
        deals = []
        status = "ok"
        page = await context.new_page()
        try:
            await page.route("**/*", lambda route: (
                route.abort() if route.request.resource_type in ("image", "media", "font")
                else route.continue_()))
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            except Exception as e:
                msg = str(e)
                if "ERR_NAME_NOT_RESOLVED" in msg:
                    status, stats["dead"] = "dead-domain", stats.get("dead", 0) + 1
                    print(f"   ☠️  {brand} @ {dom} — dead domain")
                    return
                status = "load-error"
            await safe_wait(page, JS_WAIT)

            html = await page.content()
            if is_challenge(html):
                status, stats["blocked"] = "blocked", stats.get("blocked", 0) + 1
                print(f"   ⛔ {brand} @ {dom} — challenge/blocked")
                return

            # Brand-relevance: generic store-list pages se wrong attribution roko
            if not await brand_page_relevant(page, brand, hit):
                stats["irrelevant"] = stats.get("irrelevant", 0) + 1
                print(f"   🚫 {brand} @ {dom:<32} — brand page nahi (generic)")
                return

            try:
                origin = "/".join(page.url.split("/")[:3])
                await context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
            except Exception:
                pass

            # Scroll — lazy content
            try:
                for _ in range(3):
                    await page.mouse.wheel(0, 1200)
                    await safe_wait(page, 600)
            except Exception:
                pass

            # Layer 1+2: HTML attrs/JSON + visible text
            html = await page.content()
            rows.extend(mine_from_html(html))
            try:
                rows.extend(mine_from_text(await page.inner_text("body")))
            except Exception:
                pass

            # Layer 3: reveal buttons + deals
            await click_reveal_buttons_async(page, brand, url, rows)

        except Exception as e:
            status = f"error: {str(e)[:60]}"
        finally:
            try:
                await page.close()
            except Exception:
                pass

        codes = post_process_codes([r for r in rows if not r.get("__deal__")])
        deals = [r for r in rows if r.get("__deal__")]

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_codes = 0
        for c in codes:
            if not is_duplicate(brand, c["code"], seen):
                mark_as_seen(brand, c["code"], seen)
                results.append({"brand": brand, "type": "Code", "value": c["code"],
                                "source_url": url, "method": c["method"],
                                "confidence": c["confidence"], "found_at": now})
                new_codes += 1
        seen_deal_urls = set()
        for d in deals:
            if d["url"] in seen_deal_urls:
                continue
            seen_deal_urls.add(d["url"])
            results.append({"brand": brand, "type": "Deal", "value": d["label"][:120],
                            "source_url": url, "method": d["url"],
                            "confidence": "high", "found_at": now})

        stats["pages"] = stats.get("pages", 0) + 1
        stats["codes"] = stats.get("codes", 0) + new_codes
        stats["deals"] = stats.get("deals", 0) + len(seen_deal_urls)
        print(f"   {'✅' if (new_codes or deals) else '◦'} {brand} @ {dom:<32} "
              f"+{new_codes} codes, +{len(seen_deal_urls)} deals [{status}]")


# ============================================================
# STORAGE
# ============================================================
def load_seen():
    if os.path.exists(SEEN_FILE) and os.environ.get("FRESH") != "1":
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return json_load(f)
        except Exception:
            return {}
    return {}


def json_load(f):
    import json
    return json.load(f)


def save_seen(seen):
    import json
    os.makedirs("data", exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=1)


def save_results(results):
    if not results:
        return
    fields = ["brand", "type", "value", "source_url", "method", "confidence", "found_at"]
    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow(r)


def save_summary(results, resolutions):
    brand_data = {}
    for r in results:
        bd = brand_data.setdefault(r["brand"], {"codes": [], "deals": [], "urls": set()})
        if r["type"] == "Code":
            if r["value"] not in bd["codes"]:
                bd["codes"].append(r["value"])
        else:
            bd["deals"].append(r["value"])
        bd["urls"].add(r["source_url"])

    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["brand", "total_codes", "coupon_codes", "total_deals", "deals",
                    "resolved_sites", "last_updated"])
        for brand in sorted(brand_data):
            bd = brand_data[brand]
            w.writerow([brand, len(bd["codes"]), " | ".join(bd["codes"]) or "None",
                        len(bd["deals"]), " | ".join(bd["deals"][:15]) or "None",
                        len(resolutions.get(brand, {})),
                        datetime.now().strftime("%Y-%m-%d %H:%M")])

    os.makedirs(EXT_DIR, exist_ok=True)
    for old in os.listdir(EXT_DIR):
        if old.endswith(".txt"):
            os.remove(os.path.join(EXT_DIR, old))
    for brand, bd in brand_data.items():
        safe = re.sub(r"[^\w.-]", "_", brand)
        with open(os.path.join(EXT_DIR, f"{safe}.txt"), "w", encoding="utf-8") as tf:
            tf.write("\n".join(sorted(bd["codes"])) + ("\n" if bd["codes"] else ""))


# ============================================================
# MAIN
# ============================================================
async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--brands", default=BRANDS_FILE)
    parser.add_argument("--test", type=int, default=0)
    parser.add_argument("--sites", default="", help="comma-separated domain filter")
    parser.add_argument("--google", action="store_true",
                        help="Layer 3: apne Chrome se Google/Bing search (CAPTCHA ho sakta hai)")
    parser.add_argument("--no-sitesearch", action="store_true",
                        help="Layer 2 on-site search skip (fast)")
    parser.add_argument("--fresh", action="store_true", help="seen-codes skip karke fresh run")
    parser.add_argument("--region", default="uk", help="uk / us (output naming)")
    args = parser.parse_args()
    set_region(args.region)
    if args.fresh:
        os.environ["FRESH"] = "1"

    with open(args.brands, "r", encoding="utf-8-sig") as f:
        brands = [l.strip() for l in f if l.strip()]
    if args.test:
        brands = brands[:args.test]
    sites_filter = [s for s in args.sites.split(",") if s.strip()] if args.sites else None

    print("=" * 70)
    print("🎫 COUPON PIPELINE v2 (Router + Extraction)")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')} | brands={len(brands)}")
    print("=" * 70)

    # ---------- ROUTE ----------
    print("\n🧭 STEP 1: Routing (Layer 1 → 1b → 2 on-site"
          + (" → 3 Google" if args.google else "") + ")")
    resolutions = await site_router.resolve_brands(
        brands, sites_filter=sites_filter, site_search=not args.no_sitesearch,
                                   use_google=args.google)

    total_urls = sum(len(v) for v in resolutions.values())
    print(f"\n📌 Routing done: {total_urls} brand-page URLs across {len(brands)} brands")

    # ---------- SCRAPE ----------
    print(f"\n🌐 STEP 2: Scraping {total_urls} pages ({CONCURRENCY} concurrent)...")
    results = []
    seen = load_seen()
    stats = {}
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception:
            import config
            browser = await p.chromium.launch(headless=True, executable_path=config.CHROME_PATH)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-GB", viewport={"width": 1440, "height": 900})
        sem = asyncio.Semaphore(CONCURRENCY)
        tasks = []
        for brand in brands:
            for dom, hit in resolutions.get(brand, {}).items():
                tasks.append(scrape_brand_page(context, brand, dom, hit, sem,
                                               results, seen, stats))
        if tasks:
            await asyncio.gather(*tasks)
        await context.close()
        await browser.close()

    # ---------- STORE ----------
    save_seen(seen)
    save_results(results)
    save_summary(results, resolutions)

    print("\n" + "=" * 70)
    print(f"🏁 DONE! pages={stats.get('pages', 0)} blocked={stats.get('blocked', 0)} "
          f"dead={stats.get('dead', 0)}")
    print(f"🎫 codes: {stats.get('codes', 0)} | deals: {stats.get('deals', 0)}")
    print(f"📄 {RESULTS_CSV} | {SUMMARY_CSV} | {EXT_DIR}/")
    print("=" * 70)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
