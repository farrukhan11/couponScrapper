import asyncio, re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

URLS = [
    "https://simplycodes.com/store/mileseeygolf.com",
    "https://couponreals.com/store/shoplc",
]
WANT = ["SAMHELINGMIN", "BREAKINGEIGHTY", "GSONICBP15X", "JESSPLAYSGOLF10", "GOLFLOVER",
        "HELLO20", "GOLD20", "DEAL20", "SHOP25", "EXTRA25", "SHOPSAVE", "SALE20", "SAVE25", "BEST15"]


async def main():
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    bcfg = BrowserConfig(headless=True, verbose=False, enable_stealth=True)
    async with AsyncWebCrawler(config=bcfg) as crawler:
        for u in URLS:
            print("=" * 70)
            print("URL:", u)
            try:
                r = await crawler.arun(url=u, config=CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS, page_timeout=60000,
                    delay_before_return_html=3.0, verbose=False))
                html = getattr(r, "html", "") or ""
                md = ""
                try:
                    md = r.markdown.raw_markdown if r.markdown else ""
                except Exception:
                    pass
                print(f"success={r.success} html={len(html)} md={len(md)}")
                import os
                os.makedirs("logs", exist_ok=True)
                safe = re.sub(r"[^a-z0-9]+", "_", u)[:60]
                open(f"logs/diag_{safe}.html", "w", encoding="utf-8").write(html)
                open(f"logs/diag_{safe}.md", "w", encoding="utf-8").write(md)
                for w in WANT:
                    in_html = w in html
                    in_md = w in md
                    if in_html or in_md:
                        idx = html.find(w)
                        ctx = html[max(0, idx-160):idx+80].replace("\n", " ") if idx >= 0 else ""
                        print(f"  {w:<16} html={in_html} md={in_md}")
                        if ctx:
                            print(f"      ctx: ...{ctx[-180:]}")
                # code-like tokens count
                toks = set(re.findall(r'\b[A-Z0-9]{4,20}\b', re.sub(r'<[^>]+>', ' ', html)))
                print(f"  uppercase-token sample: {sorted(list(toks))[:15]}")
            except Exception as e:
                print("  ERR:", str(e)[:120])

asyncio.run(main())
