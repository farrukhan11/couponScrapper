import scraper
from playwright.sync_api import sync_playwright

url = "https://simplycodes.com/store/cafeappliances.com"
brand = "cafeappliances"
region = "us"

print(f"Testing URL: {url}")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    codes, deals = scraper.extract_codes_from_page(page, url, brand, region)
    print("\n--- EXTRACTED CODES ---")
    for code in codes:
        print(code)
        
    print("\n--- EXTRACTED DEALS ---")
    for deal in deals:
        print(deal)
        
    browser.close()
