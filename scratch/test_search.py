import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import unquote, quote_plus, urlparse

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9"
}

def search_bing(query):
    url = f"https://www.bing.com/search?q={quote_plus(query)}"
    resp = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    for h2 in soup.find_all("h2"):
        a = h2.find("a")
        if a and a.get("href"):
            href = a.get("href")
            if href.startswith("http") and "bing.com" not in href and "microsoft.com" not in href:
                links.append(href)
    return links

brands = ["divamelody.com", "stuartwiltshireglass.co.uk", "echoradios.com", "x-bows.com"]
for b in brands:
    query = f"discount code {b}"
    links = search_bing(query)
    print(f"Bing for {b}: found {len(links)} links")
    for l in links[:3]:
        print("  -", l)
    time.sleep(1.5)
