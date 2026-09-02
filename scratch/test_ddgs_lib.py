from duckduckgo_search import DDGS
import time

ddgs = DDGS()

with open("../brands.txt", "r", encoding="utf-8") as f:
    brands = [l.strip() for l in f if l.strip()]

print(f"Testing DDGS library on {len(brands)} brands...")

for b in brands[:10]:
    query = f"discount code {b}"
    results = list(ddgs.text(query, max_results=10))
    print(f"[{b}] Found {len(results)} links")
    for r in results[:2]:
        print("  -", r.get("href"))
    time.sleep(1)
