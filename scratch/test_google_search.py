import asyncio
import sys
from urllib.parse import quote_plus
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding='utf-8')

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-GB"
        )
        page = await context.new_page()
        
        url = f"https://www.google.com/search?q={quote_plus('discount code commomy.com')}&gl=gb&hl=en&num=20"
        print(f"Going to: {url}")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        
        # Print URL and title
        print(f"Current Page URL: {page.url}")
        print(f"Current Page Title: {await page.title()}")
        
        # Check all 'a' tags
        links = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a')).map(a => ({
                href: a.getAttribute('href'),
                text: a.innerText.strip ? a.innerText.strip() : ''
            }));
        }""")
        
        print(f"Total <a> elements found on page: {len(links)}")
        valid_http_links = [l['href'] for l in links if l['href'] and l['href'].startswith('http') and 'google.' not in l['href']]
        print(f"Valid external http links found: {len(valid_http_links)}")
        for l in valid_http_links[:10]:
            print("  - ", l)

        await context.close()
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
