import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote, urlparse

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def search_ddg(query):
    url = f"https://html.duckduckgo.com/html/?q={query}"
    response = requests.post("https://html.duckduckgo.com/html/", data={"q": query}, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")
    
    links = []
    for a in soup.find_all("a", class_="result__url"):
        href = a.get("href")
        if href:
            # DuckDuckGo wraps links in //duckduckgo.com/l/?uddg=...
            if "uddg=" in href:
                actual_url = unquote(href.split("uddg=")[1].split("&")[0])
                links.append(actual_url)
            elif href.startswith("http"):
                links.append(href)
                
    return links

links = search_ddg("discount code commomy.com")
print(f"DuckDuckGo found {len(links)} links:")
for l in links[:10]:
    print("  -", l)
