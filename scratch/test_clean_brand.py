import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import unquote

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def search_ddg(brand):
    # Strip domain suffixes (.com, .co.uk, .store, .shop, etc.)
    clean_name = brand.replace(".com", "").replace(".co.uk", "").replace(".store", "").replace(".shop", "").replace(".co", "")
    query = f"discount code {clean_name}"
    
    resp = requests.post("https://html.duckduckgo.com/html/", data={"q": query}, headers=headers, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    for a in soup.find_all("a", class_="result__url"):
        href = a.get("href")
        if href and "uddg=" in href:
            actual_url = unquote(href.split("uddg=")[1].split("&")[0])
            if actual_url.startswith("http"):
                links.append(actual_url)
    return list(dict.fromkeys(links))

brands = ["divamelody.com", "stuartwiltshireglass.co.uk", "echoradios.com", "x-bows.com", "betterthan.shop"]

for b in brands:
    links = search_ddg(b)
    print(f"DDG for '{b}': found {len(links)} links")
    for l in links[:3]:
        print("  -", l)
    time.sleep(2)
