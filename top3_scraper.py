"""
GOOGLE TOP-3 SCRAPER

Input: n8n/Google discovery JSON containing up to 4 brands, each with at most
3 ranked organic result URLs. This module does URL consumption only; it does
not perform Google searches itself.

Expected JSON:
{
  "brands": [
    {
      "brand": "Brand Name",
      "domain": "brand.com",
      "results": [
        {"rank": 1, "url": "https://..."},
        {"rank": 2, "url": "https://..."},
        {"rank": 3, "url": "https://..."}
      ]
    }
  ]
}

Usage:
  python top3_scraper.py --input top3_input.json --output results.csv --region us
"""
import argparse
import asyncio
import csv
import json
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from page_scraper import (
    REVEAL_SKIP_THRESHOLD,
    browser_fetch_pages,
    crawl4ai_reveal_pass,
    cut_competitor_sections,
    fetch_pages_for_brands,
    mine_codes,
    page_is_relevant,
    render_reveal_pass,
)

MAX_BRANDS_PER_BATCH = 4
MAX_URLS_PER_BRAND = 3
RESULT_FIELDS = ["brand", "domain", "rank", "code", "method", "via", "source_url"]


def _valid_http_url(value):
    try:
        p = urlparse((value or "").strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def normalize_payload(payload):
    """Validate n8n payload and return normalized brand/url plan."""
    if not isinstance(payload, dict):
        raise ValueError("payload JSON object hona chahiye")

    raw_brands = payload.get("brands")
    if not isinstance(raw_brands, list) or not raw_brands:
        raise ValueError("'brands' non-empty array hona chahiye")
    if len(raw_brands) > MAX_BRANDS_PER_BATCH:
        raise ValueError(
            f"Ek batch mein max {MAX_BRANDS_PER_BATCH} brands allowed hain"
        )

    plan = []
    for item in raw_brands:
        if not isinstance(item, dict):
            continue
        brand = str(item.get("brand") or item.get("name") or "").strip()
        domain = str(item.get("domain") or "").strip().lower()
        label = brand or domain
        if not label:
            continue

        raw_results = item.get("results") or item.get("urls") or []
        if not isinstance(raw_results, list):
            raw_results = []

        candidates = []
        for pos, result in enumerate(raw_results, start=1):
            if isinstance(result, str):
                url = result.strip()
                rank = pos
            elif isinstance(result, dict):
                url = str(result.get("url") or "").strip()
                try:
                    rank = int(result.get("rank") or pos)
                except Exception:
                    rank = pos
            else:
                continue
            if not _valid_http_url(url):
                continue
            candidates.append((rank, pos, url))

        candidates.sort(key=lambda x: (x[0], x[1]))
        seen = set()
        results = []
        for rank, _, url in candidates:
            if url in seen:
                continue
            seen.add(url)
            results.append({"rank": rank, "url": url})
            if len(results) >= MAX_URLS_PER_BRAND:
                break

        if results:
            plan.append({
                "brand": label,
                "domain": domain,
                "match_brand": f"{brand}|{domain}" if brand and domain else label,
                "results": results,
            })

    if not plan:
        raise ValueError("Kisi brand ke liye valid Google result URL nahi mila")
    return plan


async def scrape_top3(payload, output_path="results.csv", render=True):
    plan = normalize_payload(payload)

    print(f"🔎 Google top-3 mode: {len(plan)} brands")
    for item in plan:
        print(f"🏷️  {item['brand']}: {len(item['results'])} ranked URLs")

    all_urls = []
    seen_urls = set()
    for item in plan:
        for result in item["results"]:
            u = result["url"]
            if u not in seen_urls:
                seen_urls.add(u)
                all_urls.append(u)

    print(f"\n🌐 Fetching {len(all_urls)} Google-ranked pages...")
    html_map = await asyncio.to_thread(fetch_pages_for_brands, all_urls, render)
    print(f"   ✅ {len(html_map)}/{len(all_urls)} pages mile")

    missing = [u for u in all_urls if u not in html_map]
    if missing:
        print(f"   ↻ {len(missing)} missing pages — fresh browser retry...")
        by_origin = {}
        for u in missing:
            by_origin.setdefault("/".join(u.split("/")[:3]), []).append(u)
        html_map.update(await asyncio.to_thread(browser_fetch_pages, by_origin, 1))
        print(f"   ✅ {len(html_map)}/{len(all_urls)} pages mile (retry ke baad)")

    owner_by_url = {}
    for item in plan:
        for result in item["results"]:
            owner_by_url.setdefault(result["url"], item)

    static_map = {}
    for u in all_urls:
        html = html_map.get(u)
        if not html:
            continue
        owner = owner_by_url[u]
        if not page_is_relevant(html, u, owner["match_brand"]):
            print(f"   🚫 {owner['brand']} rank URL irrelevant — skip: {u}")
            continue
        static_map[u] = mine_codes(cut_competitor_sections(html), u)

    candidates = [
        u for u in all_urls
        if u in static_map and len(static_map[u]) < REVEAL_SKIP_THRESHOLD
    ]

    reveal_map = {}
    if candidates and render:
        print(
            f"\n🖥️  Reveal pass: {len(candidates)} pages "
            "(renderer → crawl4ai → browser fallback)"
        )
        reveal_map = await render_reveal_pass(candidates, enabled=True)

        missed = [u for u in candidates if u not in reveal_map]
        if missed:
            reveal_map.update(await crawl4ai_reveal_pass(missed))
            missed = [u for u in missed if u not in reveal_map]

        if missed:
            by_origin = {}
            for u in missed:
                by_origin.setdefault("/".join(u.split("/")[:3]), []).append(u)
            fallback = await asyncio.to_thread(browser_fetch_pages, by_origin)
            for u, html in fallback.items():
                if html:
                    reveal_map[u] = {
                        "codes": {
                            c["code"]: c["method"]
                            for c in mine_codes(cut_competitor_sections(html), u)
                        },
                        "deals": [],
                    }

    rows = []
    for item in plan:
        brand_seen = set()
        for result in item["results"]:
            rank = result["rank"]
            u = result["url"]

            merged = {}
            for c in static_map.get(u, []):
                merged.setdefault(c["code"], (c["method"], "static"))
            for code, method in (reveal_map.get(u) or {}).get("codes", {}).items():
                merged.setdefault(code, (method, "render"))

            for code, (method, via) in merged.items():
                if code in brand_seen:
                    continue
                brand_seen.add(code)
                rows.append({
                    "brand": item["brand"],
                    "domain": item["domain"],
                    "rank": rank,
                    "code": code,
                    "method": method,
                    "via": via,
                    "source_url": u,
                })

        print(f"🎫 {item['brand']}: {len(brand_seen)} unique codes")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✅ DONE: {len(rows)} codes → {output_path}")
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="n8n top-3 JSON file")
    parser.add_argument("--output", default="results.csv")
    parser.add_argument("--region", default="us")
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8-sig") as f:
        payload = json.load(f)

    asyncio.run(
        scrape_top3(payload, output_path=args.output, render=not args.no_render)
    )


if __name__ == "__main__":
    main()
