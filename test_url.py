import time
from playwright.sync_api import sync_playwright
import config

url = "https://www.groupon.com/coupons/dyson"

print(f"Extracting deal links from {url}...")
with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir="chrome_profile",
        executable_path=config.CHROME_PATH,
        headless=False,
        viewport={"width": 1920, "height": 1080},
        args=["--disable-blink-features=AutomationControlled"],
    )
    
    page = context.pages[0] if context.pages else context.new_page()
    deal_links = set()
    
    # Do not abort any routes, we just want to load the page
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)
    
    # Groupon usually puts deal links in 'a' tags
    buttons = page.query_selector_all("a, button, div[role='button']")
    for btn in buttons:
        try:
            text = (btn.inner_text() or "").lower()
            if "deal" in text or "shop" in text or "activate" in text or "see" in text:
                href = btn.get_attribute("href")
                if href:
                    if not href.startswith("http"):
                        href = "https://www.groupon.com" + href
                    # Make sure it looks like an affiliate or out link
                    if "out" in href.lower() or "click" in href.lower() or "dyson" in href.lower():
                        deal_links.add(href)
        except Exception as e:
            pass

    print("\n--- EXTRACTED DEAL LINKS ---")
    for link in set(deal_links):
        print(link)
        
    context.close()
