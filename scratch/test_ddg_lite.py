import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import unquote

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def search_ddg_lite(query, session):
    url = "https://lite.duckduckgo.com/lite/"
    resp = session.post(url, data={"q": query}, headers=headers, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    for a in soup.find_all("a", class_="result-snippet"):
        pass
    for a in soup.find_all("a"):
        href = a.get("href")
        if href and "uddg=" in href:
            actual_url = unquote(href.split("uddg=")[1].split("&")[0])
            if actual_url.startswith("http") and "duckduckgo.com" not in actual_url:
                links.append(actual_url)
    return list(dict.fromkeys(links))

session = requests.Session()
brands = ["divamelody.com", "stuartwiltshireglass.co.uk", "echoradios.com", "x-bows.com", "betterthan.shop"]

for b in brands:
    query = f"discount code {b}"
    links = search_ddg_lite(query, session)
    print(f"DDG Lite for {b}: found {len(links)} links")
    for l in links[:3]:
        print("  -", l)
    time.sleep(2.5)
