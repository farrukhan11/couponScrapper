import asyncio
import sys
from urllib.parse import quote_plus
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding='utf-8')

async def main():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir="./chrome_profile",
            headless=False,
            viewport={"width": 1920, "height": 1080},
            locale="en-GB"
        )
        page = context.pages[0] if context.pages else await context.new_page()
        
        url = f"https://www.google.co.uk/search?q={quote_plus('discount code commomy.com')}&gl=uk&hl=en&num=20"
        print(f"Navigating to: {url}")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        
        print("Page Title:", await page.title())
        print("Page URL:", page.url)
        
        # Check for Google Cookie Consent button
        buttons = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('button')).map(b => b.innerText);
        }""")
        print("Buttons found on page:", buttons)
        
        # Check text in body
        text = await page.inner_text("body")
        print("Snippet of body text:", text[:300].replace('\n', ' '))
        
        await context.close()

if __name__ == '__main__':
    asyncio.run(main())
