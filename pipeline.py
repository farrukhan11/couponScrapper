"""
Coupon Scraping Pipeline
========================
Usage:
  python pipeline.py --step1              # Search Google for coupon URLs
  python pipeline.py --step2              # Scrape URLs to Markdown (Our Firecrawl)
  python pipeline.py --step3              # Extract codes from Markdown
  python pipeline.py --step4              # Deduplicate codes per brand
  python pipeline.py --all                # Run all steps
  python pipeline.py --step2 --test 10    # Test step 2 on first 10 URLs
"""

import asyncio
import argparse
import csv
import hashlib
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlparse, urlunparse, quote_plus, parse_qs, unquote

from bs4 import BeautifulSoup
from markdownify import markdownify as md_convert
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# Fix Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


# ============================================================
# CONFIGURATION
# ============================================================
REGION = "uk"
BRANDS_FILE = "brands.txt"
URLS_CSV = f"urls_{REGION}.csv"
MARKDOWN_DIR = f"markdown_{REGION}"
CODES_RAW_CSV = f"codes_raw_{REGION}.csv"
CODES_FINAL_CSV = f"codes_final_{REGION}.csv"

MAX_CONCURRENT = 5       # concurrent browser pages
PAGE_TIMEOUT = 20000     # ms
JS_WAIT = 3000           # ms to wait for JS rendering

# Sites to skip (not coupon sites)
SKIP_DOMAINS = [
    "youtube.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "wikipedia.org", "reddit.com", "pinterest.com", "tiktok.com",
    "google.com", "bing.com", "trustpilot.com", "linkedin.com",
    "amazon.com", "amazon.co.uk", "ebay.com", "ebay.co.uk",
]

# Words that look like codes but aren't
BAD_CODE_WORDS = {
    # Common English words
    "ABOUT", "ABOVE", "AFTER", "AGAIN", "ALSO", "BACK", "BEEN", "BEFORE",
    "BELOW", "BEST", "BOTH", "BROWSE", "CASH", "CATEGORIES", "CATEGORY",
    "CHANGE", "CLICK", "CLOSE", "CODE", "CODES", "CONDITIONS", "CONTACT",
    "COPY", "COUPON", "COUPONS", "CUSTOMER", "DEAL", "DEALS", "DETAILS",
    "DISCOUNT", "DOWN", "EACH", "EDIT", "EMAIL", "ENDS", "ENTER", "ERROR",
    "EVERY", "EXCLUSIONS", "EXPIRED", "EXPIRES", "EXPLORE", "FALSE", "FIND",
    "FIRST", "FOLLOW", "FOUND", "FREE", "FROM", "FULL", "GOOD", "GREAT",
    "HAVE", "HELP", "HERE", "HIGH", "HOME", "HTTPS", "HTTP", "INFO",
    "ITEM", "ITEMS", "JOIN", "JUST", "KEEP", "KNOW", "LAST", "LATEST",
    "LEFT", "LESS", "LIKE", "LIMITED", "LIST", "LIVE", "LOAD", "LOGIN",
    "LOOK", "MAKE", "MANY", "MENU", "MORE", "MOST", "MUCH", "MUST",
    "NAME", "NEED", "NEWS", "NEXT", "NONE", "NOTE", "OFFER", "OFFERS",
    "ONLY", "OPEN", "ORDER", "ORDERS", "OTHER", "OVER", "PAGE", "PAST",
    "PICK", "PLUS", "POPULAR", "POST", "PRICE", "PRIVACY", "PRODUCT",
    "PRODUCTS", "PROMO", "RATING", "READ", "RELATED", "REVEAL", "REVIEW",
    "RIGHT", "SALE", "SALES", "SAVE", "SAVINGS", "SEARCH", "SEEN", "SEND",
    "SHARE", "SHARES", "SHIP", "SHIPPING", "SHOP", "SHOW", "SIGN", "SIGNUP",
    "SITE", "SITEWIDE", "SIZE", "SOME", "SORT", "SPECIAL", "START", "STOP",
    "STORE", "STORES", "STYLE", "SUBMIT", "SUPPORT", "TAKE", "TERMS",
    "TEST", "TEXT", "THAT", "THEM", "THEN", "THERE", "THESE", "THIS",
    "TIME", "TODAY", "TOOL", "TOTAL", "TRUE", "TURN", "TYPE", "UNDER",
    "UPDATED", "USED", "USER", "VALID", "VERIFIED", "VIEW", "VOUCHER",
    "WANT", "WAYS", "WEEK", "WEEKS", "WELL", "WHAT", "WHEN", "WITH",
    "WORK", "WORKING", "YEAR", "YOUR",
    # Web/tech
    "CAPTCHA", "CHROME", "CLICK", "COOKIE", "COOKIES", "IFRAME", "JAVASCRIPT",
    "LINK", "NULL", "SPAN", "TRUE", "FALSE", "UNDEFINED", "WINDOW",
    # Site-specific noise
    "NAGA", "TARRAN", "IPTV",
    # More false positives found in testing
    "EXCLUSIVE", "LIFESPAN", "TYPICAL", "MINERVA", "GETOFF",
    "PAIRI", "DAIZA", "TINASP", "GRACE", "FIJIGA", "HANIDEC",
    "NYDEAL", "WELCOME", "COMMOMY",
}


def get_region_query(brand, region):
    r = region.lower()
    if r == "us":
        return f"coupon code {brand} working 2026"
    elif r == "uk":
        return f"discount code {brand} working 2026"
    else:
        return f"discount code {brand} {region.upper()} working 2026"


# ============================================================
# STEP 1: Search GOOGLE for Coupon URLs (UK/US Region)
# ============================================================
async def step1_search_urls(region="uk"):
    """Search Google specifically for coupon URLs per brand for UK/US."""
    print("\n" + "=" * 60)
    print(f"📌 STEP 1: Searching GOOGLE for coupon URLs (Region: {region.upper()})")
    print("=" * 60)

    brands = load_brands()
    if not brands:
        return

    all_results = []
    seen_urls = set()

    import config
    firecrawl_key = getattr(config, "FIRECRAWL_API_KEY", "").strip()

    if firecrawl_key:
        print("🔥 FIRECRAWL SEARCH ENABLED! Fetching Google search URLs via Firecrawl API (Zero CAPTCHA)...")
        import requests

        for i, brand in enumerate(brands, 1):
            query = get_region_query(brand, region)
            print(f"\n🏷️ [{i}/{len(brands)}] Firecrawl Google Search ({region.upper()}): {query}")

            for attempt in range(3):
                try:
                    resp = requests.post(
                        "https://api.firecrawl.dev/v1/search",
                        headers={
                            "Authorization": f"Bearer {firecrawl_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "query": query,
                            "limit": 10
                        },
                        timeout=15
                    )

                    if resp.status_code == 200:
                        data = resp.json()
                        search_items = data.get("data", [])
                        brand_urls = 0
                        for item in search_items:
                            raw_url = item.get("url", "")
                            if not raw_url or not raw_url.startswith("http"):
                                continue

                            clean = normalize_url(raw_url)
                            if not clean:
                                continue

                            parsed = urlparse(clean)
                            domain = parsed.netloc.lower()

                            if any(skip in domain for skip in SKIP_DOMAINS):
                                continue

                            key = (brand.lower(), clean.lower())
                            if key in seen_urls:
                                continue
                            seen_urls.add(key)

                            all_results.append([brand, clean])
                            brand_urls += 1

                        print(f"  ✅ Found {brand_urls} unique Google URLs for {brand}")
                        break
                    elif resp.status_code == 429:
                        print(f"  ⏳ Firecrawl Free Tier Rate Limit (10 req/min). Waiting 60s for reset (Attempt {attempt+1}/3)...")
                        time.sleep(60)
                    else:
                        print(f"  ❌ Firecrawl API Error {resp.status_code}: {resp.text[:100]}")
                        break

                except Exception as e:
                    print(f"  ❌ Firecrawl Search error: {e}")
                    break

            time.sleep(1)

        output_csv = f"urls_{region.lower()}.csv"
        with open(output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["brand", "url"])
            for row in all_results:
                writer.writerow(row)

        print(f"\n✅ Step 1 Done! {len(all_results)} Google URLs saved to {output_csv}")
        return

    # Fallback to Playwright Google Search if no Firecrawl Key
    print("🌐 Playwright Google Search Mode (Provide FIRECRAWL_API_KEY in config.py for instant zero-CAPTCHA search)")

    async with async_playwright() as p:
        # Use persistent context so cookies/profile bypass CAPTCHA after first solve
        user_data_dir = os.path.join(os.getcwd(), "chrome_profile")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            viewport={"width": 1920, "height": 1080},
            locale=locale_setting,
            args=["--disable-blink-features=AutomationControlled"]
        )

        page = context.pages[0] if context.pages else await context.new_page()

        for i, brand in enumerate(brands, 1):
            query = get_region_query(brand, region)
            print(f"\n🏷️ [{i}/{len(brands)}] Searching Google ({region.upper()}): {query}")

            search_url = f"{google_domain}/search?q={quote_plus(query)}&gl={gl_param}&hl=en&num=20"

            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(2000)

                # Check for CAPTCHA
                content = await page.content()
                if "/sorry/" in page.url or "unusual traffic" in content.lower():
                    print("  🛑 Google CAPTCHA detected! Solve it in the opened Chrome browser window...")
                    for _ in range(60):  # Wait up to 2 min for user to solve
                        await page.wait_for_timeout(2000)
                        if "/sorry/" not in page.url and "unusual traffic" not in (await page.content()).lower():
                            print("  ✅ CAPTCHA solved! Waiting for Google search results...")
                            await page.wait_for_timeout(4000)
                            break
                    else:
                        print("  ⚠️ CAPTCHA timeout, skipping brand")
                        continue

                # Extract links from Google search results
                links = await page.evaluate("""() => {
                    const results = [];
                    // Extract from Google search result containers
                    const elements = document.querySelectorAll('#search a[href], #rso a[href], div.g a[href], a:has(h3)');
                    elements.forEach(a => {
                        let href = a.getAttribute('href');
                        if (!href) return;
                        if (href.startsWith('/url?')) {
                            try {
                                const params = new URLSearchParams(href.split('?')[1]);
                                href = params.get('q') || params.get('url') || href;
                            } catch(e) {}
                        }
                        if (href && typeof href === 'string' && href.startsWith('http') && !href.includes('google.') && !href.includes('google.co')) {
                            results.push(href);
                        }
                    });
                    return Array.from(new Set(results));
                }""")

                # Fallback if container selector missed
                if not links:
                    links = await page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('a[href]'))
                            .map(a => a.getAttribute('href'))
                            .filter(h => h && typeof h === 'string' && h.startsWith('http') && !h.includes('google.'));
                    }""")

                brand_urls = 0
                for url in links:
                    clean = normalize_url(url)
                    if not clean:
                        continue
                    parsed = urlparse(clean)
                    domain = parsed.netloc.lower()

                    # Skip non-coupon domains
                    if any(skip in domain for skip in SKIP_DOMAINS):
                        continue

                    key = (brand.lower(), clean.lower())
                    if key in seen_urls:
                        continue
                    seen_urls.add(key)

                    all_results.append([brand, clean])
                    brand_urls += 1

                print(f"  ✅ Found {brand_urls} unique Google URLs for {brand}")

            except Exception as e:
                print(f"  ❌ Google Search error: {e}")

            await page.wait_for_timeout(1500)

        await context.close()

    # Save to CSV
    output_csv = f"urls_{region.lower()}.csv"
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["brand", "url"])
        for row in all_results:
            writer.writerow(row)

    print(f"\n✅ Step 1 Done! {len(all_results)} Google URLs saved to {output_csv}")


# ============================================================
# STEP 2: Scrape URLs to Markdown (Our Firecrawl)
# ============================================================
async def step2_scrape_markdown(test_limit=0):
    """Load each URL in Playwright, convert HTML to clean Markdown, save to files."""
    print("\n" + "=" * 60)
    print("📌 STEP 2: Scraping URLs to Markdown (Our Firecrawl)")
    print("=" * 60)

    urls_data = load_urls_csv()
    if not urls_data:
        print("❌ No URLs found. Run --step1 first.")
        return

    if test_limit > 0:
        urls_data = urls_data[:test_limit]
        print(f"🧪 TEST MODE: Processing only {test_limit} URLs")

    os.makedirs(MARKDOWN_DIR, exist_ok=True)

    print(f"🚀 Processing {len(urls_data)} URLs with {MAX_CONCURRENT} concurrent pages...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        tasks = []
        for brand, url in urls_data:
            tasks.append(scrape_single_url(context, brand, url, semaphore))

        results = await asyncio.gather(*tasks)

        await context.close()
        await browser.close()

    # Summary
    success = sum(1 for r in results if r == "Success")
    blocked = sum(1 for r in results if r == "Cloudflare")
    errors = sum(1 for r in results if r not in ("Success", "Cloudflare"))
    print(f"\n✅ Step 2 Done! Success: {success} | Blocked: {blocked} | Errors: {errors}")
    print(f"📂 Markdown files saved to: {MARKDOWN_DIR}/")


async def scrape_single_url(context, brand, url, semaphore):
    """Scrape a single URL and save its Markdown with resume support & retries."""
    filename = url_to_filename(brand, url)
    filepath = os.path.join(MARKDOWN_DIR, filename)

    # Resume support: if already downloaded, skip
    if os.path.exists(filepath) and os.path.getsize(filepath) > 100:
        print(f"  ⏩ Cached: {brand} | {url[:60]}")
        return "Success"

    async with semaphore:
        page = None
        for attempt in range(2):
            try:
                page = await context.new_page()

                # Block heavy resources
                await page.route("**/*", lambda route: (
                    route.abort() if route.request.resource_type in ["image", "media", "font", "stylesheet"]
                    else route.continue_()
                ))

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                except PlaywrightTimeoutError:
                    pass  # continue with whatever loaded
                except Exception as e:
                    err_msg = str(e)
                    if "ERR_NAME_NOT_RESOLVED" in err_msg:
                        print(f"  ⚠️ Dead domain (DNS not resolved): {url[:60]}")
                        return "Dead Domain"
                    elif "ERR_NETWORK_CHANGED" in err_msg:
                        print(f"  🔄 Network changed, retrying in 2s: {url[:60]}")
                        await asyncio.sleep(2)
                        continue
                    else:
                        raise e

                await page.wait_for_timeout(JS_WAIT)

                try:
                    html = await page.content()
                except Exception:
                    await page.wait_for_timeout(1000)
                    html = await page.content()

                # Cloudflare check
                if "Just a moment..." in html or "challenge-running" in html:
                    print(f"  ❌ Cloudflare: {url[:70]}")
                    return "Cloudflare"

                # Clean HTML → Markdown
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "iframe", "svg",
                                 "noscript", "meta", "link", "header", "form"]):
                    tag.decompose()

                markdown_text = md_convert(str(soup), strip=["a", "img"], heading_style="ATX")

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(f"<!-- brand: {brand} -->\n")
                    f.write(f"<!-- url: {url} -->\n\n")
                    f.write(markdown_text)

                print(f"  ✅ {brand} | {url[:60]}")
                return "Success"

            except Exception as e:
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                print(f"  ❌ Error: {url[:60]} → {e}")
                return f"Error: {e}"
            finally:
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass


# ============================================================
# STEP 3: Extract Codes and Deals from Markdown
# ============================================================
def step3_extract_codes():
    """Read all Markdown files and extract coupon codes and deals."""
    print("\n" + "=" * 60)
    print("📌 STEP 3: Extracting codes & deals from Markdown")
    print("=" * 60)

    if not os.path.isdir(MARKDOWN_DIR):
        print(f"❌ Markdown folder '{MARKDOWN_DIR}' not found. Run --step2 first.")
        return

    md_files = [f for f in os.listdir(MARKDOWN_DIR) if f.endswith(".md")]
    active_brands = set(load_brands())
    print(f"📂 Processing {len(md_files)} markdown files (Filtering for {len(active_brands)} active brands in {BRANDS_FILE})...")

    all_items = []

    for filename in md_files:
        filepath = os.path.join(MARKDOWN_DIR, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Parse brand and url from comment headers
        brand = ""
        url = ""
        text_lines = []
        for line in lines:
            if line.startswith("<!-- brand:"):
                brand = line.replace("<!-- brand:", "").replace("-->", "").strip()
            elif line.startswith("<!-- url:"):
                url = line.replace("<!-- url:", "").replace("-->", "").strip()
            else:
                text_lines.append(line)

        if not brand or not text_lines:
            continue

        # Skip if this brand is not in active brands.txt
        if active_brands and brand not in active_brands:
            continue

        text = "".join(text_lines)

        # 1. Extract coupon codes
        codes = extract_codes_smart(text)
        for code in codes:
            all_items.append([brand, "Code", code, url])

        # 2. Extract deals (Get Deal / Direct Offers)
        deals = extract_deals_smart(text_lines)
        for deal in deals:
            all_items.append([brand, "Deal", deal, url])

    # Save raw items
    with open(CODES_RAW_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["brand", "type", "value", "source_url"])
        for row in all_items:
            writer.writerow(row)

    codes_count = sum(1 for r in all_items if r[1] == "Code")
    deals_count = sum(1 for r in all_items if r[1] == "Deal")
    print(f"\n✅ Step 3 Done! {codes_count} codes & {deals_count} deals extracted to {CODES_RAW_CSV}")


def extract_codes_smart(text):
    """Smart extraction of coupon codes from markdown text."""
    # Find all uppercase alphanumeric words 4-20 chars
    candidates = set(re.findall(r'\b[A-Z0-9]{4,20}\b', text))

    valid_codes = []
    for code in candidates:
        # Skip if it's a known bad word
        if code in BAD_CODE_WORDS:
            continue

        # Skip pure digits (e.g., 2026, 1234)
        if code.isdigit():
            continue

        # Skip if too short
        if len(code) < 4:
            continue

        # Skip product SKU patterns: single letter + 3 digits (H218, PM006)
        if re.match(r'^[A-Z]{1,2}\d{3,}$', code):
            continue

        # Skip patterns like TSDD049 (all caps prefix + digits)
        if re.match(r'^[A-Z]{2,4}\d{3,}$', code):
            continue

        # Skip patterns like 0GET, 2GET, 5GET (digit + common word)
        if re.match(r'^\d[A-Z]{2,4}$', code):
            continue

        # Skip patterns like 1STYLE, 2STYLE, 1PM003 (digit prefix + word)
        if re.match(r'^\d+[A-Z]+\d*$', code):
            continue

        # Good indicators: mix of letters and numbers (DAY15, V12, MAZZX30)
        has_letters = any(c.isalpha() for c in code)
        has_digits = any(c.isdigit() for c in code)

        if has_letters and has_digits:
            valid_codes.append(code)
        elif has_letters and not has_digits:
            if code.isupper() and len(code) >= 5 and code not in BAD_CODE_WORDS:
                valid_codes.append(code)

    return valid_codes


def extract_deals_smart(lines):
    """Extract deals and promotions (Get Deal / Direct Offers)."""
    deals = set()
    deal_triggers = {'get deal', 'activate deal', 'view deal', 'grab deal', 'claim deal', 'get this deal', 'shop deal'}
    ignore_words = ['how do', 'review', 'about this', 'terms', 'privacy', 'similar stores', 'submit', 'faq', 't-mobile', 'expedia', 'wayfair', 'amazon']

    for i, line in enumerate(lines):
        clean_l = line.strip().lower()
        if clean_l in deal_triggers:
            # Look back up to 8 lines for the heading or deal title
            for j in range(i - 1, max(-1, i - 9), -1):
                prev = lines[j].strip()
                if prev.startswith('###') or prev.startswith('##') or prev.startswith('* **'):
                    title = re.sub(r'^[#*_\s]+|[#*_\s]+$', '', prev).strip()
                    lower_t = title.lower()
                    if len(title) >= 8 and not any(w in lower_t for w in ignore_words):
                        deals.add(title)
                        break
    return list(deals)


# ============================================================
# STEP 4: Deduplicate and Group by Brand (1 Row Per Brand)
# ============================================================
def step4_deduplicate():
    """Remove duplicate codes and deals, grouped cleanly per brand (1 row per brand)."""
    print("\n" + "=" * 60)
    print("📌 STEP 4: Deduplicating and Grouping by Brand")
    print("=" * 60)

    if not os.path.exists(CODES_RAW_CSV):
        print(f"❌ {CODES_RAW_CSV} not found. Run --step3 first.")
        return

    # Structure: {brand: {'codes': set(), 'deals': set(), 'urls': set()}}
    brand_data = {}

    with open(CODES_RAW_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            brand = row.get("brand", "").strip()
            item_type = row.get("type", "Code").strip()
            val = row.get("value", row.get("code", "")).strip()
            url = row.get("source_url", "").strip()

            if not brand or not val:
                continue

            if brand not in brand_data:
                brand_data[brand] = {"codes": set(), "deals": set(), "urls": set()}

            if item_type.lower() == "deal":
                brand_data[brand]["deals"].add(val)
            else:
                brand_data[brand]["codes"].add(val.upper())

            if url:
                brand_data[brand]["urls"].add(url)

    # 1. Save final summary CSV (1 row per brand with totals)
    total_codes = 0
    total_deals = 0

    with open(CODES_FINAL_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["brand", "total_codes", "coupon_codes", "total_deals", "deals", "source_urls", "last_updated"])

        for brand in sorted(brand_data.keys()):
            codes_list = sorted(list(brand_data[brand]["codes"]))
            deals_list = sorted(list(brand_data[brand]["deals"]))
            urls_list = sorted(list(brand_data[brand]["urls"]))

            total_codes += len(codes_list)
            total_deals += len(deals_list)

            writer.writerow([
                brand,
                len(codes_list),
                " | ".join(codes_list) if codes_list else "None",
                len(deals_list),
                " | ".join(deals_list) if deals_list else "None",
                " | ".join(urls_list),
                datetime.now().strftime("%Y-%m-%d %H:%M")
            ])

    # 2. Save individual .txt files per brand for direct Copy-Paste into Extension!
    ext_dir = f"extension_codes_{REGION}"
    os.makedirs(ext_dir, exist_ok=True)
    # Clear old txt files in extension_codes directory to avoid stale brands
    for old_file in os.listdir(ext_dir):
        if old_file.endswith(".txt"):
            try:
                os.remove(os.path.join(ext_dir, old_file))
            except Exception:
                pass

    for brand in sorted(brand_data.keys()):
        safe_name = re.sub(r'[^\w\.-]', '_', brand)
        txt_path = os.path.join(ext_dir, f"{safe_name}.txt")
        codes_list = sorted(list(brand_data[brand]["codes"]))
        with open(txt_path, "w", encoding="utf-8") as tf:
            tf.write("\n".join(codes_list) + "\n")

    # 3. Also save line-by-line CSV (brand, code, type)
    line_by_line_csv = f"codes_line_by_line_{REGION}.csv"
    with open(line_by_line_csv, "w", newline="", encoding="utf-8") as lf:
        lwriter = csv.writer(lf)
        lwriter.writerow(["brand", "type", "code_or_deal"])
        for brand in sorted(brand_data.keys()):
            for c in sorted(list(brand_data[brand]["codes"])):
                lwriter.writerow([brand, "Code", c])
            for d in sorted(list(brand_data[brand]["deals"])):
                lwriter.writerow([brand, "Deal", d])

    # Print summary table
    print(f"\n{'Brand':<32} {'Codes':>8} {'Deals':>8} {'Total':>8}")
    print("-" * 60)
    for brand in sorted(brand_data.keys()):
        c_count = len(brand_data[brand]["codes"])
        d_count = len(brand_data[brand]["deals"])
        print(f"  {brand:<30} {c_count:>8} {d_count:>8} {c_count + d_count:>8}")
    print("-" * 60)
    print(f"  {'TOTAL':<30} {total_codes:>8} {total_deals:>8} {total_codes + total_deals:>8}")
    print(f"\n✅ Step 4 Done! Brand-wise grouped data saved to {CODES_FINAL_CSV}")
    print(f"📂 Extension ready files (1 code per line) saved to: {ext_dir}/")
    print(f"📄 Line-by-line CSV saved to: {line_by_line_csv}")


# ============================================================
# HELPER FUNCTIONS
# ============================================================
def load_brands():
    """Load brands from brands.txt."""
    if not os.path.exists(BRANDS_FILE):
        print(f"❌ {BRANDS_FILE} not found!")
        return []
    with open(BRANDS_FILE, "r", encoding="utf-8") as f:
        brands = [line.strip() for line in f if line.strip()]
    print(f"✅ {len(brands)} brands loaded")
    return brands


def load_urls_csv():
    """Load URLs from the CSV file."""
    if not os.path.exists(URLS_CSV):
        print(f"❌ '{URLS_CSV}' not found!")
        other_region = "us" if REGION == "uk" else "uk"
        other_csv = f"urls_{other_region}.csv"
        if os.path.exists(other_csv):
            print(f"💡 HINT: Found '{other_csv}'. Did you forget to add '--region {other_region}'?")
        return []
    urls = []
    with open(URLS_CSV, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)  # skip header if present
        for row in reader:
            if len(row) >= 2:
                urls.append((row[0].strip(), row[1].strip()))
            elif len(row) == 1 and row[0].startswith("http"):
                urls.append(("unknown", row[0].strip()))
    return urls


def normalize_url(raw_url):
    """Strip fragments, tracking params, trailing slashes."""
    try:
        parsed = urlparse(raw_url)
        # Remove fragments
        clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                           parsed.params, parsed.query, ""))
        return clean.rstrip("/") if clean != "/" else clean
    except Exception:
        return raw_url


def url_to_filename(brand, url):
    """Create a safe filename from brand + url."""
    url_hash = hashlib.md5(url.encode()).hexdigest()[:10]
    safe_brand = re.sub(r'[^\w]', '_', brand)[:20]
    return f"{safe_brand}_{url_hash}.md"


def clean_workspace(region):
    """Delete old markdown, extension files, and CSVs for a clean new run."""
    print("\n" + "=" * 60)
    print(f"🧹 CLEANING WORKSPACE FOR REGION: {region.upper()}")
    print("=" * 60)

    md_dir = f"markdown_{region}"
    ext_dir = f"extension_codes_{region}"

    if os.path.isdir(md_dir):
        for f in os.listdir(md_dir):
            try:
                os.remove(os.path.join(md_dir, f))
            except Exception:
                pass
        print(f"  🗑️ Cleared: {md_dir}/")

    if os.path.isdir(ext_dir):
        for f in os.listdir(ext_dir):
            try:
                os.remove(os.path.join(ext_dir, f))
            except Exception:
                pass
        print(f"  🗑️ Cleared: {ext_dir}/")

    for csv_file in [f"urls_{region}.csv", f"codes_raw_{region}.csv", f"codes_final_{region}.csv", f"codes_line_by_line_{region}.csv"]:
        if os.path.exists(csv_file):
            try:
                os.remove(csv_file)
                print(f"  🗑️ Removed: {csv_file}")
            except Exception:
                pass

    print("✨ Clean complete!\n")


# ============================================================
# MAIN
# ============================================================
async def async_main():
    parser = argparse.ArgumentParser(description="Coupon Scraping Pipeline")
    parser.add_argument("--step1", action="store_true", help="Search Google for URLs")
    parser.add_argument("--step2", action="store_true", help="Scrape URLs to Markdown")
    parser.add_argument("--step3", action="store_true", help="Extract codes from Markdown")
    parser.add_argument("--step4", action="store_true", help="Deduplicate codes")
    parser.add_argument("--all", action="store_true", help="Run all steps")
    parser.add_argument("--clean", action="store_true", help="Wipe old markdown and results before running")
    parser.add_argument("--region", type=str, default="uk", help="Region to search: uk or us")
    parser.add_argument("--test", type=int, default=0, help="Limit URLs for testing")
    args = parser.parse_args()

    if not any([args.step1, args.step2, args.step3, args.step4, args.all, args.clean]):
        parser.print_help()
        return

    global REGION, URLS_CSV, MARKDOWN_DIR, CODES_RAW_CSV, CODES_FINAL_CSV
    selected_region = args.region.lower()
    REGION = selected_region
    URLS_CSV = f"urls_{REGION}.csv"
    MARKDOWN_DIR = f"markdown_{REGION}"
    CODES_RAW_CSV = f"codes_raw_{REGION}.csv"
    CODES_FINAL_CSV = f"codes_final_{REGION}.csv"

    if args.clean:
        clean_workspace(selected_region)

    print("=" * 60)
    print("🎫 COUPON SCRAPING PIPELINE")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"🌍 Target Region: {selected_region.upper()}")
    print("=" * 60)

    if args.step1 or args.all:
        await step1_search_urls(region=selected_region)

    if args.step2 or args.all:
        await step2_scrape_markdown(test_limit=args.test)

    if args.step3 or args.all:
        step3_extract_codes()

    if args.step4 or args.all:
        step4_deduplicate()

    print("\n🏁 Pipeline complete!")


if __name__ == "__main__":
    asyncio.run(async_main())
