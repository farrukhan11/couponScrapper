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
            query = f"discount code {brand} {region.upper()}"
            print(f"\n🏷️ [{i}/{len(brands)}] Firecrawl Google Search: {query}")

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
                else:
                    print(f"  ❌ Firecrawl API Error {resp.status_code}: {resp.text[:100]}")

            except Exception as e:
                print(f"  ❌ Firecrawl Search error: {e}")

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
            query = f"discount code {brand}"
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
    """Scrape a single URL and save its Markdown."""
    async with semaphore:
        page = None
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

            await page.wait_for_timeout(JS_WAIT)

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

            # Save to file
            filename = url_to_filename(brand, url)
            filepath = os.path.join(MARKDOWN_DIR, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"<!-- brand: {brand} -->\n")
                f.write(f"<!-- url: {url} -->\n\n")
                f.write(markdown_text)

            print(f"  ✅ {brand} | {url[:60]}")
            return "Success"

        except Exception as e:
            print(f"  ❌ Error: {url[:60]} → {e}")
            return f"Error: {e}"
        finally:
            if page:
                await page.close()


# ============================================================
# STEP 3: Extract Codes from Markdown
# ============================================================
def step3_extract_codes():
    """Read all Markdown files and extract coupon codes."""
    print("\n" + "=" * 60)
    print("📌 STEP 3: Extracting codes from Markdown")
    print("=" * 60)

    if not os.path.isdir(MARKDOWN_DIR):
        print(f"❌ Markdown folder '{MARKDOWN_DIR}' not found. Run --step2 first.")
        return

    md_files = [f for f in os.listdir(MARKDOWN_DIR) if f.endswith(".md")]
    print(f"📂 Processing {len(md_files)} markdown files...")

    all_codes = []

    for filename in md_files:
        filepath = os.path.join(MARKDOWN_DIR, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Parse brand and url from comment headers
        brand = ""
        url = ""
        text = ""
        for line in lines:
            if line.startswith("<!-- brand:"):
                brand = line.replace("<!-- brand:", "").replace("-->", "").strip()
            elif line.startswith("<!-- url:"):
                url = line.replace("<!-- url:", "").replace("-->", "").strip()
            else:
                text += line

        if not brand or not text.strip():
            continue

        # Extract codes
        codes = extract_codes_smart(text)
        for code in codes:
            all_codes.append([brand, code, url])

    # Save raw codes
    with open(CODES_RAW_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["brand", "code", "source_url"])
        for row in all_codes:
            writer.writerow(row)

    print(f"\n✅ Step 3 Done! {len(all_codes)} raw codes extracted to {CODES_RAW_CSV}")


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
            # Strong signal — this is likely a real code
            valid_codes.append(code)
        elif has_letters and not has_digits:
            # Pure alphabetical — only keep if it looks code-like
            # Must be ALL CAPS and not a common word
            if code.isupper() and len(code) >= 5 and code not in BAD_CODE_WORDS:
                valid_codes.append(code)

    return valid_codes


# ============================================================
# STEP 4: Deduplicate Codes per Brand
# ============================================================
def step4_deduplicate():
    """Remove duplicate codes per brand and save final results."""
    print("\n" + "=" * 60)
    print("📌 STEP 4: Deduplicating codes per brand")
    print("=" * 60)

    if not os.path.exists(CODES_RAW_CSV):
        print(f"❌ {CODES_RAW_CSV} not found. Run --step3 first.")
        return

    # Read raw codes
    brand_codes = {}  # {brand: {code: source_url}}
    with open(CODES_RAW_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            brand = row["brand"].strip()
            code = row["code"].strip().upper()
            url = row["source_url"].strip()

            if brand not in brand_codes:
                brand_codes[brand] = {}
            if code not in brand_codes[brand]:
                brand_codes[brand][code] = url

    # Save final deduplicated codes
    total_codes = 0
    with open(CODES_FINAL_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["brand", "code", "source_url", "found_at"])
        for brand in sorted(brand_codes.keys()):
            codes = brand_codes[brand]
            for code, url in sorted(codes.items()):
                writer.writerow([brand, code, url, datetime.now().strftime("%Y-%m-%d %H:%M")])
                total_codes += 1

    # Print summary
    print(f"\n{'Brand':<35} {'Unique Codes':>12}")
    print("-" * 50)
    for brand in sorted(brand_codes.keys()):
        count = len(brand_codes[brand])
        print(f"  {brand:<33} {count:>10}")
    print("-" * 50)
    print(f"  {'TOTAL':<33} {total_codes:>10}")
    print(f"\n✅ Step 4 Done! {total_codes} unique codes saved to {CODES_FINAL_CSV}")


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
    parser.add_argument("--region", type=str, default="uk", help="Region to search: uk or us")
    parser.add_argument("--test", type=int, default=0, help="Limit URLs for testing")
    args = parser.parse_args()

    if not any([args.step1, args.step2, args.step3, args.step4, args.all]):
        parser.print_help()
        return

    selected_region = args.region.lower()
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
