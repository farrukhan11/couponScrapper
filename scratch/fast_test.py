import asyncio
import csv
import re
import time
import sys
sys.stdout.reconfigure(encoding='utf-8')
from playwright.async_api import async_playwright

async def intercept_route(route):
    # Block images, CSS, and fonts to load page instantly
    if route.request.resource_type in ["image", "stylesheet", "font", "media"]:
        await route.abort()
    else:
        await route.continue_()

async def extract_codes(page, url):
    try:
        # Load page with short timeout
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        
        # Check Cloudflare
        title = await page.title()
        if "Just a moment" in title or "Cloudflare" in title:
            return ["Blocked by Cloudflare"]
            
        # Basic clicking logic
        buttons = await page.query_selector_all("button, a, [role='button']")
        clicks = 0
        for btn in buttons:
            if clicks > 5: break
            try:
                text = (await btn.inner_text()).lower()
                if "show code" in text or "get code" in text or "reveal" in text:
                    await btn.click(timeout=2000)
                    await page.wait_for_timeout(1000)
                    clicks += 1
            except:
                pass
                
        # Extract potential codes (Uppercase words with numbers, or just uppercase)
        page_text = await page.inner_text("body")
        codes = set()
        for word in re.findall(r"\b[A-Za-z0-9_-]{4,20}\b", page_text):
            if word.isupper() and word.isalpha() == False:  # Contains at least a number/dash
                codes.add(word)
        
        # Also grab standard uppercase words if they look like coupons
        for word in re.findall(r"\b[A-Z]{5,15}\b", page_text):
             codes.add(word)
             
        # Filter out common junk
        junk = {"ABOUT", "CONTACT", "PRIVACY", "TERMS", "SEARCH", "LOGIN", "SIGNUP"}
        codes = {c for c in codes if c not in junk}
        
        return list(codes)
        
    except Exception as e:
        return [f"Error: {str(e).splitlines()[0]}"]

async def process_url(context, url):
    page = await context.new_page()
    await page.route("**/*", intercept_route)
    result = await extract_codes(page, url)
    await page.close()
    return url, result

async def main():
    print("Starting Fast Test on Top 10 URLs...")
    start_time = time.time()
    
    # 1. Read URLs and remove duplicates
    urls = []
    with open("urls_uk.csv", "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader) # skip header
        for row in reader:
            if len(row) >= 4 and row[3].startswith("http"):
                urls.append(row[3])
                
    # Handle duplicates by converting to dictionary keys (preserves order in Python 3.7+)
    unique_urls = list(dict.fromkeys(urls))[:10]
    print(f"Total Unique URLs loaded for test: {len(unique_urls)}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        
        # 2. Run all 10 URLs CONCURRENTLY
        tasks = [process_url(context, url) for url in unique_urls]
        results = await asyncio.gather(*tasks)
        
        await browser.close()
        
    print(f"\nTime taken: {time.time() - start_time:.2f} seconds\n")
    print("--- RESULTS ---")
    for url, codes in results:
        print(f"URL: {url}")
        print(f"Found: {codes}\n")

if __name__ == "__main__":
    asyncio.run(main())
