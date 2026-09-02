import asyncio
import re
import csv
import sys
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# Fix Windows console unicode issues
sys.stdout.reconfigure(encoding='utf-8')

def extract_deals_from_markdown(markdown_text):
    """Basic extraction of coupon codes using Regex"""
    codes_found = set(re.findall(r'\b[A-Z0-9]{4,20}\b', markdown_text))
    # Filter common false positives
    bad_words = {
        'CODE', 'COUPON', 'DISCOUNT', 'PROMO', 'OFFER', 'SAVE', 'FREE', 'SHIPPING', 
        'HTTP', 'HTTPS', 'COM', 'WWW', 'CLICK', 'HERE', 'REVEAL', 'SHOP', 'NOW', 'TERMS', 
        'CONDITIONS', 'APPLY', 'DEAL', 'MORE', 'LESS', 'DETAILS', 'VERIFIED', 'FALSE', 'TRUE'
    }
    # Keep only things that aren't purely digits, aren't bad words, and are > 4 chars
    clean_codes = []
    for c in codes_found:
        if c not in bad_words and not c.isdigit() and len(c) > 3:
            clean_codes.append(c)
    return clean_codes

async def clean_and_convert_html(html_content):
    """Cleans HTML and converts it to Markdown"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Remove unwanted tags to reduce noise
    for element in soup(["script", "style", "nav", "footer", "iframe", "svg", "noscript", "meta", "link", "header"]):
        element.decompose()
        
    cleaned_html = str(soup)
    # Convert to markdown
    markdown_text = md(cleaned_html, strip=['a', 'img'], heading_style="ATX")
    return markdown_text

async def scrape_url(context, url, semaphore):
    async with semaphore:
        page = None
        try:
            print(f"🌍 Starting: {url}")
            page = await context.new_page()
            
            # Block images/fonts to load faster
            await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_())
            
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except PlaywrightTimeoutError:
                print(f"  ⏳ Timeout on {url}, continuing with loaded DOM")
                
            # Wait a little bit for JS rendering
            await page.wait_for_timeout(3000)
            
            html_content = await page.content()
            
            # Check for Cloudflare Challenge manually (if we didn't bypass it)
            if "Just a moment..." in html_content or "challenge-running" in html_content:
                print(f"  ❌ Cloudflare blocked: {url}")
                return url, [], "Cloudflare Blocked"
                
            markdown = await clean_and_convert_html(html_content)
            
            if markdown:
                codes = extract_deals_from_markdown(markdown)
                print(f"  ✅ Found {len(codes)} codes on {url}")
                return url, codes, "Success"
            else:
                return url, [], "Empty Markdown"
                
        except Exception as e:
            print(f"  ❌ Error on {url}: {e}")
            return url, [], f"Error: {e}"
        finally:
            if page:
                await page.close()

async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", type=int, help="Number of URLs to test", default=0)
    args = parser.parse_args()

    input_file = 'urls_uk.csv'
    output_file = 'results_markdown_uk.csv'
    
    urls = []
    with open(input_file, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if row:
                urls.append(row[0].strip())
                
    if args.test > 0:
        urls = urls[:args.test]
        print(f"🧪 TESTING ON {args.test} URLs...")
        
    print(f"🚀 Starting Local Markdown Scraper on {len(urls)} URLs")
    
    # Initialize Playwright
    async with async_playwright() as p:
        # Launch Chromium (Headless by default, you can change to False for debugging)
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        semaphore = asyncio.Semaphore(5) # 5 concurrent pages
        tasks = []
        
        for url in urls:
            tasks.append(asyncio.create_task(scrape_url(context, url, semaphore)))
            
        results = await asyncio.gather(*tasks)
        
        await context.close()
        await browser.close()
        
    # Save results
    total_codes = 0
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Source URL", "Extracted Codes", "Status"])
        for url, codes, status in results:
            writer.writerow([url, ", ".join(codes), status])
            total_codes += len(codes)
            
    print(f"🎉 Done! Total codes found: {total_codes}. Saved to {output_file}")

if __name__ == "__main__":
    # Required for Windows async playwright
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
