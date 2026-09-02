import requests
import re
import csv
import time
import sys

sys.stdout.reconfigure(encoding='utf-8')

def scrape_with_jina(url):
    jina_url = f"https://r.jina.ai/{url}"
    headers = {
        # 'Authorization': 'Bearer YOUR_JINA_API_KEY_HERE', # Uncomment when you have an API key
        'X-Return-Format': 'markdown'
    }
    
    try:
        print(f"🌍 Fetching with Jina.ai: {url}")
        response = requests.get(jina_url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            return response.text
        elif response.status_code == 429:
            print("⚠️ Rate Limit Exceeded (429). Jina API Key is required for bulk scraping.")
            return None
        else:
            print(f"❌ Error {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return None

def extract_deals_from_markdown(markdown_text):
    # Basic Regex to find codes in the markdown (e.g., words in all caps near "Off" or "Code")
    codes_found = set(re.findall(r'\b[A-Z0-9]{4,15}\b', markdown_text))
    # Filter out common false positives
    bad_words = {'CODE', 'COUPON', 'DISCOUNT', 'PROMO', 'OFFER', 'SAVE', 'FREE', 'SHIPPING', 'HTTP', 'HTTPS', 'COM', 'WWW'}
    codes_found = {c for c in codes_found if c not in bad_words and not c.isdigit()}
    return list(codes_found)

def main():
    input_file = '../urls_uk.csv'
    urls = []
    
    with open(input_file, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if row:
                urls.append(row[0].strip())
    
    # Test on first 3 URLs
    print(f"Total URLs loaded: {len(urls)}. Testing top 3...")
    for i, url in enumerate(urls[:3]):
        print(f"\n--- URL {i+1}: {url} ---")
        markdown = scrape_with_jina(url)
        
        if markdown:
            print(f"✅ Extracted {len(markdown)} characters of Markdown text.")
            
            # Show a snippet of the text
            print("📝 Snippet:")
            print(markdown[:300] + "...\n")
            
            codes = extract_deals_from_markdown(markdown)
            print(f"🎫 Potential Codes found (Regex): {codes}")
        
        # Delay to avoid free tier rate limits
        time.sleep(2)

if __name__ == "__main__":
    main()
