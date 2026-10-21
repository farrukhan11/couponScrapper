# CouponScrapper — Usage Guide

Brands ki list do → har brand ke **coupon codes**, **deals**, **sale %**,
**free-shipping policy** aur **newsletter offer** ikattha karke `offers.csv` banata hai.

Do hisse:
1. **Coupon sites** (24 sites: tenereteam, couponreals, savingsays, simplycodes...) → codes + deals
2. **Brand ki apni site** (jaise shoplc.com) → sale / shipping / newsletter

---

## 1. Ek dafa setup (local)

```powershell
cd C:\Users\farrukh.saleeem\Documents\GitHub\couponScrapper
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m patchright install chromium
```

> `pip` na chale to `python -m pip` use karo.

---

## 2. Brands file ka format

`brands.txt` / `brands_us.txt` — **ek line = ek brand**:

```
Shop LC|shoplc.com
mileseeygolf.com
Jo Malone
```

- `Naam|domain` (best) — pipe ke aage naam, peeche brand ki website.
- Sirf domain bhi chalega (`mileseeygolf.com`).
- Max 200 brands ek run mein.

---

## 3. Index (store list) — kaam ka reference

`data/stores_index.json` = 17 coupon sites ke store pages ki list.

```powershell
# naya index banao (~10-20 min)
python sitemap_crawler.py

# ya sirf ek site refresh
python sitemap_crawler.py --site couponreals.com
```

**Kitni baar?** Har roz **nahi**. Weekly/monthly kaafi hai, ya jab koi brand
index mein na mile. Codes roz badalte hain, par index ka content dheere badalta hai.

> **Server note:** Contabo (datacenter IP) par Cloudflare sites index build ke
> waqt block karti hain. Behtar: **local par index banao**, phir server par copy:
> `scp data/stores_index.json root@81.17.100.61:/var/www/couponScrapper/data/`

---

## 4. Scrape chalana (asli kaam)

```powershell
# file se (recommended)
python page_scraper.py --file brands_us.txt --region us

# ek-do brand direct
python page_scraper.py "Shop LC|shoplc.com" "Zinus|zinus.com" --region us
```

### Options
| Flag | Matlab |
|---|---|
| `--region us\|uk` | Currency + output (`$` / `£`). Default `us`. |
| `--no-brand-site` | Sirf coupon sites (brand site skip) — fast |
| `--sale-limit 250` | Brand site par kitne products scan |
| `--types coupon,deal,sale,shipping,newsletter` | Sirf ye types |
| `--no-render` | Renderer off — sirf httpx (fast test) |
| `--render-limit N` | Sirf N pages renderer se (debug/fast) |

Examples:
```powershell
python page_scraper.py --file brands_us.txt --region uk --no-brand-site
python page_scraper.py --file brands_us.txt --region us --types coupon,deal
```

---

## 5. Output files

| File | Kya hai |
|---|---|
| **`offers.csv`** | **Sab kuch** (unified — niche schema) |
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
| `deal` | `title` + `url` (deal link) |
| `sale` | `title`, `percent_off` (**max**), `price_original`, `price_sale`, `url` |
| `shipping` | `details` = **poori policy text**, `amount` = free-shipping threshold |
| `newsletter` | `title` = offer text, `percent_off`/`amount` |

### Summary banao
```powershell
python summary.py offers.csv offers_summary.csv
```
Columns: `brand, coupons, deals, sale, shipping, newsletter, max_sale_off, shipping_amount`

---

## 5b. Renderer tool (apna "Firecrawl-like", no proxy)

`renderer.py` — asli Chrome se page **render** karta hai (JS/SPA/Cloudflare),
apne aap:
- **Direct navigation** (origin+fetch nahi)
- **Warm-up**: 403/challenge par pehle homepage visit → `cf_clearance` set
- **Challenge wait** + SPA detect + reveal buttons
- **Cache** (`.render_cache/`)

Standalone test:
```powershell
python renderer.py "https://simplycodes.com/store/shopathluna.com"
```
Env tuning (`.env`): `RENDER_TIMEOUT`, `RENDER_WAIT`, `RENDER_CHALLENGE_WAIT`, `RENDER_CACHE_TTL`.

> Scraper isko automatically use karta hai: httpx block/403/SPA → renderer.
> Isi se simplycodes (Cloudflare) aur couponreals (SPA) bina proxy ke scrape hote hain.

## 6. Web UI (optional)

```powershell
python web_ui.py      # http://localhost:8000
```
`.env` mein `UI_PASSWORD` na ho to login nahi maangega.
Server deploy details: **`DEPLOY.md`**.

---

## 7. Config (`.env`)

```env
SCRAPER_REGION=us          # default region
SCRAPE_BRAND_SITE=1        # 0 = brand site skip
SALE_LIMIT=250
SCRAPER_HEADLESS=1         # server par 1, local par 0 (visible)
UI_PASSWORD=...            # web UI login
PROXY_URL=                 # residential proxy (khali = direct)
CHROME_PATH=/usr/bin/google-chrome
```

---

## 8. Kaam kaise karta hai (flow)

```
brands file
   │
   ├─ coupon sites: brand → store page match (slug + shop<brand>)
   │                 → httpx → RENDERER (direct nav + challenge wait)
   │                 → mine_codes (HTML attrs / JSON / text)  = type=coupon
   │                 → mine_deals (title + link)              = type=deal
   │
   └─ brand ki site: shopify products.json / JSON-LD  → sale (max %) = type=sale
                     /policies/shipping-policy       → type=shipping
                     signup text                     → type=newsletter
   │
   ▼
offers.csv  (+ results.csv, offers_summary.csv)
```

---

## 9. Common problems

| Problem | Hal |
|---|---|
| `pip` not recognized | `python -m pip ...` |
| Codes kam aa rahe | Cloudflare block. `.env` `PROXY_URL` (residential) ya index local se banao |
| Brand index mein nahi mila | `python sitemap_crawler.py` dobara chalao |
| 0 brand-site offers | Site Shopify nahi / block. `--sale-limit` badhao ya site check karo |
| Unicode/emoji error (Windows) | `$env:PYTHONIOENCODING="utf-8"` set karo |

---

## 10. Server (Contabo) par

Poori detail `DEPLOY.md` mein. Rutine update:
```bash
cd /var/www/couponScrapper
git pull
source venv/bin/activate && pip install -r requirements.txt
pm2 restart couponscraper
```
