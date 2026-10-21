# CouponScrapper — Usage Guide

Brands ki list do → ye **Google (Serper.dev)** par search karke page-1 ki sites se
**coupon codes** nikalta hai, saath mein **simplycodes** aur **groupon** bhi check karta hai.

> **CLI only.** Web UI ab use nahi hota.
> **Abhi coupon-only:** deals aur brand-site (sale/shipping/newsletter) **off** hain
> (client ke rules ke baad on honge).
> **Sponsored ads** include hote hain — Google SERP hamare renderer se render hota hai,
> aur ad ka **destination URL** (`data-rw`/`data-pcu`) nikalte hain. Google ad-click
> link kabhi nahi khulta → **advertiser par cost nahi**.
> (Serper ka standard API ads nahi deta, isliye ads renderer se.)

---

## 1. Setup (ek dafa)

```powershell
cd C:\Users\farrukh.saleeem\Documents\GitHub\couponScrapper
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m patchright install chromium
```
> `pip` na chale to `python -m pip` use karo.

`.env` mein Serper key daalo:
```env
SERPER_API_KEY=<tumhari key>
```

---

## 2. Brands file format

`brands.txt` / `brands_us.txt` — **ek line = ek brand**:
```
Shop LC|shoplc.com
mileseeygolf.com
Jo Malone
```
- `Naam|domain` (best) · sirf domain bhi chalega.
- Max 200 brands ek run mein.

---

## 3. Scrape chalana

### Default: Google (Serper)
```powershell
python page_scraper.py --file brands_us.txt
```
Har brand ke liye:
1. Google query (Serper):
   - **US** → `"<brand> coupon code"`
   - **UK** (`.co.uk` / `uk.` ya `Name|domain|uk`) → `"<brand> discount code"` + `"<brand> voucher code"`
2. Page-1 results: **organic + sponsored** (top 10).
3. **+ simplycodes + groupon** (sitemap index se resolve).
4. Brand ki apni site skip; doosre brand (jaise Royal Doulton) skip.
5. Har site render → codes mine (reveal buttons ≤30 clicks) → dedupe.

### Fallback: purana sitemap-index mode
```powershell
python page_scraper.py --file brands_us.txt --source index
```

### Options
| Flag | Matlab |
|---|---|
| `--source google\|index` | google (default) / index (purana flow) |
| `--region us\|uk` | Google region + output label (khali = auto) |
| `--max-results 10` | Google page-1 results |
| `--no-simplycodes` / `--no-groupon` | always-sites skip |
| `--no-brand-site` | brand site (sale/shipping) skip |
| `--no-render` | renderer off — sirf httpx (fast test) |
| `--render-limit N` | sirf N pages render (debug) |
| `--types coupon,deal,sale,shipping,newsletter` | type filter |

Examples:
```powershell
python page_scraper.py --file brands_us.txt                      # Google default
python page_scraper.py "Corsair|corsair.com" "Farnell|uk.farnell.com"
python page_scraper.py --file brands_us.txt --source index       # fallback
```

---

## 4. Output

| File | Kya hai |
|---|---|
| **`offers.csv`** | **Sab kuch** (unified) |
| `results.csv` | Sirf coupon codes |
| `offers_summary.csv` | Brand-wise counts |

### `offers.csv` columns
```
brand, type, title, offer, url, percent_off, amount,
price_original, price_sale, details, source_url, method, via, region, found_at
```
| `type` | Bika hua |
|---|---|
| `coupon` | `offer` = code |
| `deal` | `title` + `url` |
| `sale` | `title`, `percent_off` (max), prices, `url` |
| `shipping` | `details` = poori policy, `amount` = threshold |
| `newsletter` | `title` = offer text |

Summary:
```powershell
python summary.py offers.csv offers_summary.csv
```

---

## 5. Index (simplycodes/groupon + index fallback)

`data/stores_index.json` = coupon sites ke store pages.
```powershell
python sitemap_crawler.py            # rebuild (~10-20 min)
python sitemap_crawler.py --site couponreals.com
```
- Google mode ise sirf **simplycodes + groupon** ke store URL nikalne ke liye use karta hai.
- `--source index` ise poora use karta hai.
- Roz rebuild ki zaroorat nahi (weekly/monthly kaafi).

---

## 6. Renderer (apna "Firecrawl-like", no proxy)

`renderer.py` — real Chrome se page render karta hai:
- Direct navigation + **warm-up** (403 par homepage → `cf_clearance`)
- Challenge wait, SPA detect, reveal buttons, cache (`.render_cache/`)

```powershell
python renderer.py "https://simplycodes.com/store/shopathluna.com"
```
Tuning (`.env`): `RENDER_TIMEOUT`, `RENDER_WAIT`, `RENDER_CHALLENGE_WAIT`, `RENDER_CACHE_TTL`.

---

## 7. Config (`.env`)
```env
# Search (Google via Serper)
SERPER_API_KEY=
SEARCH_MAX_RESULTS=10
SEARCH_GL=                      # khali = auto (.co.uk/uk. -> uk, warna us)
SEARCH_QUERY_US=coupon code
SEARCH_QUERY_UK=discount code,voucher code
SEARCH_PAUSE=1.0
SEARCH_INCLUDE_ADS=1            # sponsored results (bina ad-click)

# Coupon-only mode
REVEAL_CLICK_LIMIT=30           # per site reveal clicks
INCLUDE_DEALS=0                 # abhi off
INCLUDE_BRAND_SITE=0            # abhi off

# General
SCRAPER_REGION=us
SALE_LIMIT=250
SCRAPER_HEADLESS=0              # local 0 (visible), server/xvfb 1
PROXY_URL=                      # optional
```

---

## 8. Flow (Google mode)
```
brand
  ├─ Serper: "<brand> coupon code"  (US) / "<brand> discount/voucher code" (UK)
  ├─ page-1 organic (top 10)  — brand ki apni site skip
  ├─ + simplycodes + groupon (index se)
  ├─ dedupe (domain-wise) → renderer → mine codes → dedupe
  └─ offers.csv (type=coupon)
        phir agla brand
```

---

## 9. Common problems
| Problem | Hal |
|---|---|
| `SERPER_API_KEY missing` | `.env` mein key daalo |
| Serper 401/403 | Key galat/expired; credits check karo |
| Codes kam | Site block/challenge; renderer retry; `--max-results` badhao |
| Brand index mein nahi | `python sitemap_crawler.py` (simplycodes/groupon ke liye) |
| `pip` not recognized | `python -m pip ...` |
| Unicode/emoji error | `$env:PYTHONIOENCODING="utf-8"` |
