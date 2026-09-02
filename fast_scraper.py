import asyncio
import csv
import re
import os
import time
from datetime import datetime
import sys
sys.stdout.reconfigure(encoding='utf-8')
from playwright.async_api import async_playwright

MAX_CONCURRENT = 5
URLS_CSV = "urls_uk.csv"
OUTPUT_CSV = "fast_results_uk.csv"

JUNK_WORDS = {"ABOUT", "CONTACT", "PRIVACY", "TERMS", "SEARCH", "LOGIN", "SIGNUP", 
              "PROMO", "CODE", "COUPON", "DISCOUNT", "VOUCHER", "OFFER", "CLICK", 
              "HERE", "APPLY", "SHOP", "NOW", "STORE", "SITEWIDE", "SALE", "REVEAL", 
              "DETAILS", "MORE", "LESS", "VIEW", "SHOW", "HIDDEN", "EXPIRES", "EXPIRED",
              "ENDS", "ONLY", "SELECT", "ITEMS", "SITE", "ORDERS", "ORDER", "CHECKOUT"}

def is_valid_code(word):
    word = word.strip().upper()
    if not (4 <= len(word) <= 25): return False
    if not re.match(r"^[A-Z0-9_\-]+$", word): return False
    if word in JUNK_WORDS: return False
    return True

def find_codes_in_text(text):
    if not text: return []
    codes = []
    words = re.findall(r"\b[A-Za-z0-9_\-]{4,25}\b", text)
    for w in words:
        if is_valid_code(w):
            if any(c.isdigit() for c in w) or w.isupper():
                codes.append(w.upper())
    return [c for c in codes if c not in JUNK_WORDS]

async def intercept_route(route):
    if route.request.resource_type in ["image", "stylesheet", "font", "media"]:
        await route.abort()
    else:
        await route.continue_()

async def click_and_extract(page, btn):
    try:
        # Give permission for clipboard just in case
        origin = "/".join(page.url.split("/")[:3])
        try:
            await page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
        except:
            pass

        before_text = await page.inner_text("body")
        
        # Click
        await btn.scroll_into_view_if_needed()
        await btn.click(timeout=3000)
        await page.wait_for_timeout(1500)
        
        # 1. Try clipboard
        try:
            clip = await page.evaluate("() => navigator.clipboard.readText()")
            if clip and is_valid_code(clip):
                return clip.strip().upper()
        except:
            pass
            
        # 2. Try Modals
        modal_selectors = [
            "[role='dialog']", "[class*='modal']", "[class*='Modal']",
            "[class*='popup']", "[class*='Popup']", "[class*='overlay']",
            "[class*='coupon']", "[class*='reveal']", "[id*='coupon']",
        ]
        for sel in modal_selectors:
            try:
                els = await page.query_selector_all(sel)
                for el in els:
                    if await el.is_visible():
                        text = await el.inner_text()
                        codes = find_codes_in_text(text)
                        if codes: return codes[0]
            except: pass
            
        # 3. Try new text on page
        after_text = await page.inner_text("body")
        new_words = set(after_text.split()) - set(before_text.split())
        diff_text = " ".join(new_words)
        codes = find_codes_in_text(diff_text)
        if codes: return codes[0]
        
    except Exception as e:
        pass
    
    return "UNKNOWN"

async def process_url(context, url, brand, results_list):
    print(f"[+] Processing: {url}")
    page = await context.new_page()
    await page.route("**/*", intercept_route)
    
    found_deals = []
    
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(4000) # Let JS frameworks load the deals
        
        title = await page.title()
        if "Just a moment" in title or "Cloudflare" in title:
            print(f"[-] Blocked by Cloudflare: {url}")
            await page.close()
            return
            
        buttons = await page.query_selector_all("button, a, [role='button']")
        for btn in buttons:
            try:
                text = (await btn.inner_text() or "").lower()
                if "code" in text or "promo" in text or "reveal" in text or "deal" in text or "coupon" in text:
                    print(f"[*] Found potential button: {text[:30]}")
                    
                    # Extract deal description by going up the DOM tree
                    parent = await btn.evaluate_handle("el => el.closest('div.coupon, li.coupon, .deal, .offer, .card, li, div') || el.parentElement.parentElement")
                    deal_desc = "Deal Description Not Found"
                    if parent:
                        try:
                            # get the text, remove the button's own text to keep it clean
                            full_text = await parent.inner_text()
                            btn_text = await btn.inner_text()
                            deal_desc = full_text.replace(btn_text, "").strip()
                            deal_desc = re.sub(r'\s+', ' ', deal_desc)[:200] # clean up spaces, max 200 chars
                        except: pass
                    
                    # Click and extract actual code
                    code = await click_and_extract(page, btn)
                    
                    if code and code != "UNKNOWN":
                        found_deals.append({
                            "brand": brand,
                            "deal_description": deal_desc,
                            "code": code,
                            "source_url": url,
                            "found_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })
                        print(f"    --> Found Code: {code} | Deal: {deal_desc[:50]}...")
                        
            except Exception as e:
                pass
                
    except Exception as e:
        print(f"[-] Error loading {url}: {str(e).splitlines()[0]}")
        
    await page.close()
    
    if found_deals:
        results_list.extend(found_deals)


async def main():
    print("="*50)
    print("🚀 FAST CONCURRENT COUPON SCRAPER")
    print("="*50)
    
    # 1. Read URLs and map to Brand
    url_to_brand = {}
    if not os.path.exists(URLS_CSV):
        print(f"Error: {URLS_CSV} not found!")
        return
        
    with open(URLS_CSV, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            next(reader) # skip header
        except: pass
        for row in reader:
            if len(row) >= 4:
                brand, url = row[0].strip(), row[3].strip()
                if url.startswith("http") and url not in url_to_brand:
                    url_to_brand[url] = brand
                    
    urls_to_process = list(url_to_brand.keys())
    # For testing, let's limit to 10. Once verified, we can remove this slice.
    urls_to_process = urls_to_process[:10]
    
    print(f"Total Unique URLs loaded: {len(urls_to_process)}")
    
    # 2. Setup output CSV
    write_header = not os.path.exists(OUTPUT_CSV)
    
    results_list = []
    
    start_time = time.time()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        
        # Concurrency semaphore
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        
        async def sem_process(url):
            async with semaphore:
                await process_url(context, url, url_to_brand[url], results_list)
                
        tasks = [sem_process(u) for u in urls_to_process]
        await asyncio.gather(*tasks)
        
        await browser.close()
        
    # 3. Write results to CSV
    if results_list:
        with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["brand", "deal_description", "code", "source_url", "found_at"])
            if write_header:
                writer.writeheader()
            for r in results_list:
                writer.writerow(r)
                
    print("\n" + "="*50)
    print(f"✅ DONE! Processed {len(urls_to_process)} URLs in {time.time() - start_time:.2f} seconds.")
    print(f"📁 Extracted {len(results_list)} deals. Saved to {OUTPUT_CSV}")
    print("="*50)

if __name__ == "__main__":
    # Windows event loop fix for playwright
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
