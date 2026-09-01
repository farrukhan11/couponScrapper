# CouponScrapper

A browser-based coupon discovery and extraction tool written in Python with Playwright.

The current production flow is configured for a **UK coupon batch**, runs with a visible Chrome browser, searches Google for coupon-related pages, visits selected coupon/deal sites, extracts visible or hidden coupon codes, and writes the results to CSV.

> **Important:** this project discovers and extracts coupon codes from public search results and coupon/deal pages. It does **not** currently verify every code at the merchant checkout. A code appearing in `results_uk.csv` means the scraper found it on a source page; it does not guarantee the code is still valid, applicable to every product, or accepted at checkout.

---

## Table of contents

- [What the project does](#what-the-project-does)
- [Current mode](#current-mode)
- [How the scraper works](#how-the-scraper-works)
- [Project structure](#project-structure)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Input brands](#input-brands)
- [Running the scraper](#running-the-scraper)
- [Search behaviour](#search-behaviour)
- [Google result link extraction](#google-result-link-extraction)
- [Coupon extraction layers](#coupon-extraction-layers)
- [CAPTCHA handling](#captcha-handling)
- [Output files](#output-files)
- [Duplicate handling and resume behaviour](#duplicate-handling-and-resume-behaviour)
- [Current UK target list](#current-uk-target-list)
- [Troubleshooting](#troubleshooting)
- [Changing the region](#changing-the-region)
- [Using Bing instead of Google](#using-bing-instead-of-google)
- [AI-related files](#ai-related-files)
- [Current limitations](#current-limitations)
- [Operational notes](#operational-notes)
- [Development notes](#development-notes)

---

## What the project does

For every store/domain in `brands.txt`, the scraper:

1. Loads the store name/domain.
2. Runs coupon-focused web searches.
3. Collects external result URLs from Google or Bing.
4. Prioritises coupon/deal/voucher-related result pages.
5. Visits a limited number of source sites per brand.
6. Mines coupon codes from HTML and page text.
7. Looks for buttons such as **Show Code**, **Get Code**, **Reveal Code**, **Copy Code**, **Show Voucher**, etc.
8. Clicks reveal buttons when required.
9. Reads codes from modals, new content, clipboard text and HTML attributes.
10. Normalises and de-duplicates codes.
11. Saves newly discovered codes to CSV.
12. Persists seen codes so an interrupted run can continue without repeatedly writing the same code.

The scraper uses a real browser rather than only HTTP requests because coupon sites frequently rely on JavaScript, popups, reveal buttons, clipboard actions, redirects and dynamically rendered content.

---

## Current mode

The current `main` branch is configured as follows:

| Setting | Current value |
|---|---|
| Region | UK |
| Google country targeting | `gb` |
| Browser locale | `en-GB` |
| Search engine | Google |
| Search pages per query | 2 |
| Search queries per brand | 3 |
| Maximum source sites per brand | 8 |
| Browser | Real installed Google Chrome |
| Headless mode | Off |
| AI | Off |
| CAPTCHA solving | Manual |
| Output codes file | `results_uk.csv` |
| Output search URLs file | `urls_uk.csv` |
| Seen-code state file | `data/seen_codes_uk.json` |

The scraper intentionally runs with **AI disabled**. The current extraction flow is deterministic and rule-based.

---

## How the scraper works

High-level flow:

```text
brands.txt
   |
   v
Load brand/domain
   |
   v
Build UK Google searches
   |
   +--> <brand> coupon code
   +--> <brand> discount code
   +--> <brand> voucher code
   |
   v
Collect Google result links
   |
   v
Normalise Google redirect URLs
   |
   v
Filter + prioritise coupon/deal pages
   |
   v
Visit up to MAX_SITES_PER_BRAND
   |
   +--> HTML attribute / JSON mining
   +--> visible text mining
   +--> reveal-button detection
   +--> clipboard extraction
   +--> modal / popup extraction
   +--> newly revealed text extraction
   |
   v
Validate + normalise code
   |
   v
De-duplicate
   |
   +--> results_uk.csv
   +--> data/seen_codes_uk.json
```

---

## Project structure

```text
couponScrapper/
|
|-- scraper.py              # Main non-AI coupon scraper
|-- config.py               # Runtime configuration
|-- brands.txt              # Store/domain input list
|-- README.md               # Project documentation
|-- .gitignore
|
|-- ai_agent.py             # Legacy/optional AI agent implementation
|-- test_ai.py              # Older AI testing utility
|-- test_url.py             # URL/testing utility from earlier development
|
|-- results.csv             # Historical output from older runs
|-- deals.csv               # Historical output from older runs
|-- urls.csv                # Historical URL output
|
|-- retailmenot_modal.png   # Historical debugging screenshot
|-- retailmenot_tab_0.png   # Historical debugging screenshot
|
|-- data/                   # Runtime state, created locally
|   `-- seen_codes_uk.json
|
|-- chrome_profile/         # Persistent Chrome profile, created locally
|-- __pycache__/            # Python cache
`-- scratch/                # Development scratch directory
```

### Runtime files that are intentionally local

The `.gitignore` excludes:

- virtual environments
- `__pycache__`
- `chrome_profile/`
- `data/`
- most CSV files
- JSON files

This means files such as `results_uk.csv`, `urls_uk.csv` and `data/seen_codes_uk.json` are normally generated and kept on the machine running the scraper rather than committed to Git.

---

## Requirements

Recommended environment:

- Windows 10 or Windows 11
- Python 3.10+
- Google Chrome installed
- Internet connection
- Playwright Python package

The current default Chrome executable path is:

```text
C:\Program Files\Google\Chrome\Application\chrome.exe
```

If Chrome is installed elsewhere, update `CHROME_PATH` in `config.py`.

---

## Installation

Clone the repository:

```powershell
git clone https://github.com/farrukhan11/couponScrapper.git
cd couponScrapper
```

If the repository already exists locally:

```powershell
git pull origin main
```

Create a virtual environment if desired:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Install Playwright:

```powershell
pip install playwright
```

The project is configured to launch your installed Chrome directly. If you later switch to Playwright-managed Chromium, install it with:

```powershell
playwright install chromium
```

Before the first run, syntax-check the scraper:

```powershell
python -m py_compile scraper.py
```

No output means Python successfully compiled the file.

---

## Configuration

All main runtime settings are in `config.py`.

### Search engine

```python
SEARCH_ENGINE = "google"
```

Supported values in the current code:

```text
google
bing
```

### Region

```python
REGIONS = ["uk"]
```

For the UK flow, the code maps `uk` to Google's country code `gb`.

### Search depth

```python
SEARCH_PAGES = 2
```

Each search query checks two result pages.

There are currently three queries per brand, so one brand can produce up to six search result page loads before coupon source pages are visited.

### Maximum coupon source sites

```python
MAX_SITES_PER_BRAND = 8
```

After collecting and prioritising results, the scraper visits at most eight source URLs per brand.

### Human-like delays

```python
MIN_DELAY = 8
MAX_DELAY = 15
```

These delays are primarily used between search result pages to reduce request frequency and make browser activity less aggressive.

Additional shorter random delays are also used during page loading, scrolling and reveal-button interaction.

### CAPTCHA settings

```python
CAPTCHA_TIMEOUT = 300
CAPTCHA_CHECK = 2
```

If a supported CAPTCHA/unusual-traffic page is detected, the scraper waits up to 300 seconds and checks every two seconds to see whether it has been manually resolved.

### Chrome settings

```python
USE_REAL_CHROME = True
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
```

A persistent Chrome profile is created in:

```text
chrome_profile/
```

This lets browser state persist between runs and is useful for cookies, consent choices and manually solved challenges.

### Browser mode

```python
HEADLESS = False
SLOW_MO = 300
```

`HEADLESS = False` is intentional so you can see what the browser is doing and manually handle challenges when required.

### UK output files

```python
URLS_CSV = "urls_uk.csv"
OUTPUT_CSV = "results_uk.csv"
DEALS_CSV = "deals_uk.csv"
SEEN_CODES_FILE = "data/seen_codes_uk.json"
```

`DEALS_CSV` remains in configuration for compatibility with earlier versions. The current main scraper focuses on coupon-code extraction rather than deal-link output.

### AI settings

```python
USE_AI = False
```

The current main scraper does not depend on an AI API key.

---

## Input brands

Edit `brands.txt` to control which stores are processed.

Use one store or domain per line:

```text
example.com
anotherstore.co.uk
shop.example.net
```

Blank lines are ignored.

The current input contains 21 UK-targeted stores.

---

## Running the scraper

Update local code first:

```powershell
git pull origin main
```

Optional syntax check:

```powershell
python -m py_compile scraper.py
```

Run:

```powershell
python scraper.py
```

Expected startup output is similar to:

```text
=======================================================
COUPON CODE SCRAPER - UK
=======================================================
21 brands loaded
GOOGLE | ['uk'] | 21 brands | AI OFF | en-GB
```

For each brand, the console prints:

- current brand number
- search query
- number of result links found per Google page
- number of source sites selected
- each source URL being visited
- each newly discovered coupon code
- number of new codes found for that brand

Stop the scraper with:

```text
Ctrl + C
```

Because seen-code state is written during the run, already-saved codes can be retained across interrupted runs.

---

## Search behaviour

For every brand, the scraper currently searches:

```text
<brand> coupon code
<brand> discount code
<brand> voucher code
```

Example for `commomy.com`:

```text
commomy.com coupon code
commomy.com discount code
commomy.com voucher code
```

For Google UK, a URL is generated using:

```text
gl=gb
hl=en
```

The browser itself uses:

```text
en-GB
```

This aligns the explicit Google country target and browser locale with the UK batch.

---

## Google result link extraction

Google changes its result-page DOM frequently, so the scraper does not depend on only one CSS shape.

The current extractor checks multiple result-like selectors, including:

```text
a:has(h3)
a:has([role='heading'])
div.MjjYud a[href]
div.tF2Cxc a[href]
#search a[href]
```

It also normalises Google wrapper URLs such as:

```text
/url?q=https://example.com/...
/url?url=https://example.com/...
```

and converts them back to the actual external destination URL before filtering.

Google-owned links and internal search-navigation links are rejected.

This multi-selector approach exists because visible Google search results may still produce `0 links` if code assumes every result title uses one fixed DOM structure.

---

## Coupon extraction layers

The current non-AI scraper uses several extraction methods.

### 1. HTML attribute extraction

It searches rendered HTML for attributes commonly used by coupon sites:

```text
data-code
data-clipboard-text
data-coupon-code
data-promo-code
data-voucher-code
```

A result extracted this way is recorded with a method such as:

```text
html_attr
```

### 2. HTML/JSON-style embedded values

The scraper also searches HTML for keys such as:

```text
code
couponCode
promoCode
voucherCode
```

These may be present in inline state, rendered JSON or page markup.

The recorded method is typically:

```text
html_json
```

### 3. Visible text extraction

Visible page text is scanned for token-shaped coupon candidates.

The normal visible-text path is intentionally stricter than reveal/modal extraction to avoid treating ordinary words as coupon codes.

### 4. Reveal-button interaction

The scraper inspects clickable elements including:

```text
button
a
[role='button']
```

It looks for labels containing phrases such as:

```text
show code
get code
reveal code
view code
copy code
see code
show coupon
get coupon
show discount
get discount
show voucher
get voucher
click to reveal
tap to reveal
unmask
unlock
```

A maximum number of reveal interactions is enforced per source page to avoid uncontrolled clicking.

### 5. Clipboard extraction

Some coupon websites copy the code directly to the clipboard after a click.

The scraper grants clipboard permissions for the active site origin and attempts to read the clipboard after reveal interactions.

Codes found this way use the method:

```text
clipboard
```

### 6. Modal/popup extraction

The scraper checks visible elements matching modal-like selectors such as:

```text
[role='dialog']
[class*='modal']
[class*='popup']
[class*='overlay']
[class*='coupon']
[class*='reveal']
[id*='coupon']
```

Coupon-like values found after a reveal interaction can be recorded from the popup/modal state.

### 7. Newly revealed text

The scraper compares body text before and after a click.

New tokens appearing after the reveal action are scanned separately. This is useful when a site replaces button text or reveals a code inline instead of using a modal.

### 8. New tabs

Coupon pages sometimes open the merchant website in a new tab while leaving the revealed coupon on the original source page.

The scraper detects newly opened tabs, briefly inspects relevant HTML where possible, closes the new tab, and continues processing the coupon source page.

---

## Coupon validation rules

The scraper performs format-level validation to reduce false positives.

General constraints include:

- length between 4 and 20 characters
- letters, digits, `_` and `-` only
- no spaces
- no `*` masked values
- excludes a list of common non-code words such as `coupon`, `sale`, `checkout`, `offer`, `price`, etc.

The code supports legitimate uppercase alphabetic coupon codes from stronger contexts such as HTML attributes, clipboard results and reveal/modal content.

Example potentially valid codes:

```text
WELCOME
WELCOME10
SAVE20
EXTRA-15
VIP_25
```

This validation is heuristic. It is designed to reduce obvious noise, not to prove checkout validity.

---

## CAPTCHA handling

The scraper checks for common challenge indicators such as:

```text
/sorry/
captcha
unusual traffic
verify you are human
are you a robot
before you continue
complete the security check
```

If detected during Google searching, the browser stays open and the console asks for manual resolution.

Because the current run uses visible Chrome, manually complete the challenge in the browser. Once the challenge disappears, the scraper continues automatically.

The current project does not contain an automatic CAPTCHA-solving service.

---

## Output files

### `results_uk.csv`

Main coupon output.

Columns:

| Column | Meaning |
|---|---|
| `brand` | Input store/domain |
| `code` | Extracted coupon code |
| `source_url` | Page where the code was found |
| `region` | Region used for the run, currently `uk` |
| `method` | Extraction method |
| `found_at` | Local timestamp when the code was saved |

Example:

```csv
brand,code,source_url,region,method,found_at
commomy.com,WELCOME10,https://example-coupon-site.com/commomy,uk,html_attr,2026-09-01 16:30:00
```

### `urls_uk.csv`

Stores raw external URLs collected from search results.

Columns:

```text
brand
region
search_page
url
```

This file is useful for debugging search coverage independently of coupon extraction.

### `data/seen_codes_uk.json`

Persistent per-brand code history used for de-duplication.

Conceptual structure:

```json
{
  "commomy.com": [
    "WELCOME10",
    "SAVE20"
  ],
  "anotherstore.co.uk": [
    "NEW15"
  ]
}
```

If a code is already present for the same brand, it is not written again to `results_uk.csv`.

### Historical CSV files

The repository also contains older files such as:

```text
results.csv
deals.csv
urls.csv
```

These are from earlier development/runs and are not the current UK output targets.

---

## Duplicate handling and resume behaviour

Deduplication is scoped by brand.

For example, if `SAVE10` was already found for `commomy.com`, it will not be appended again for that brand on a later run.

The same text code can still be legitimate for a different brand.

Seen codes are persisted to:

```text
data/seen_codes_uk.json
```

If you intentionally want a completely fresh run, remove the local state file and optionally remove the previous UK CSV outputs before running again:

```powershell
Remove-Item .\data\seen_codes_uk.json -ErrorAction SilentlyContinue
Remove-Item .\results_uk.csv -ErrorAction SilentlyContinue
Remove-Item .\urls_uk.csv -ErrorAction SilentlyContinue
```

Only do this when you intentionally want to reset deduplication/history.

---

## Current UK target list

`brands.txt` currently contains:

| # | Store/domain |
|---:|---|
| 1 | `commomy.com` |
| 2 | `lgxnds.com` |
| 3 | `divamelody.com` |
| 4 | `stuartwiltshireglass.co.uk` |
| 5 | `echoradios.com` |
| 6 | `x-bows.com` |
| 7 | `betterthan.shop` |
| 8 | `kailashenergy.com` |
| 9 | `charabancaroma.store` |
| 10 | `icarsoft-us.com` |
| 11 | `uthena.com` |
| 12 | `emporizen.com` |
| 13 | `blackandgray.co` |
| 14 | `ruedigerhats.com` |
| 15 | `ivorynn.com` |
| 16 | `shyrebikes.co.uk` |
| 17 | `whitetigerqigong.com` |
| 18 | `aura-displays.com` |
| 19 | `welluraglobal.com` |
| 20 | `jewelclues.com` |
| 21 | `vibroacousticsolutions.com` |

The region is determined by scraper configuration, not by the domain suffix alone. A `.com` target can still be searched using UK-targeted Google results.

---

## Troubleshooting

### Google shows results but console says `0 links`

First make sure local code is current:

```powershell
git pull origin main
```

Then rerun:

```powershell
python scraper.py
```

The current main branch includes a multi-selector Google result parser and Google redirect URL normalisation.

If results are visibly present but the parser still reports zero:

1. Do not immediately close the browser.
2. Note the exact query shown in the address bar.
3. Check whether Google is displaying a normal results page, consent screen, challenge page, or experimental layout.
4. Capture the console output and browser screenshot for DOM-specific debugging.

### Chrome executable not found

Error may indicate the configured executable path does not exist.

Check:

```powershell
Test-Path "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

If it returns `False`, locate Chrome and update:

```python
CHROME_PATH = r"YOUR\ACTUAL\CHROME\PATH\chrome.exe"
```

### Chrome profile already in use

The scraper uses:

```text
chrome_profile/
```

Do not run two scraper processes using the same persistent profile at the same time.

If a previous process crashed, make sure the previous Chrome instance launched by the scraper is closed before rerunning.

### CAPTCHA/unusual traffic appears

This is expected occasionally during repeated searches.

Complete the challenge manually in the visible browser and wait for the script to continue.

Do not increase request frequency to work around a challenge; that can make blocking worse.

### No `results_uk.csv` exists

The file is created only after at least one result is saved.

If no codes have been found yet, the file may not exist.

Check `urls_uk.csv` first to confirm search results are being collected.

### Codes are found but some look wrong

Coupon extraction is heuristic. Review:

- `code`
- `source_url`
- `method`

The source URL is deliberately stored so questionable entries can be audited manually.

### A real code with letters only is missing

The scraper is intentionally conservative with ordinary visible page text. Alphabetic-only codes are accepted more readily from stronger contexts such as HTML attributes, clipboard content or reveal/modal text.

If a specific site exposes legitimate codes in a different pattern, add a targeted extraction rule rather than globally weakening validation.

### Script appears slow

The scraper intentionally waits between searches and interactions.

With the current defaults, each brand may execute:

- 3 queries
- 2 Google pages per query
- up to 8 source-site visits

For 21 brands this can take significant time, especially when sites are slow or CAPTCHA/manual interaction occurs.

Reducing delays makes the run faster but increases the chance of throttling and blocking.

---

## Changing the region

The current production configuration is UK-specific.

To adapt another region, update `REGIONS` and review the country/locale mapping in `scraper.py`.

Do not assume a two-letter internal label is always identical to the code expected by a search engine.

For example, the project deliberately maps:

```text
uk -> gb
```

for Google/Bing country targeting.

For a serious multi-region version, move country code and browser locale mappings into an explicit configuration dictionary rather than adding one-off conditionals.

---

## Using Bing instead of Google

Change:

```python
SEARCH_ENGINE = "google"
```

to:

```python
SEARCH_ENGINE = "bing"
```

The current code contains a Bing result selector based on:

```text
li.b_algo h2 a
```

Google and Bing have different markup, ranking and anti-automation behaviour, so switching engines can materially change coverage and result quality.

---

## AI-related files

The repository contains historical AI-related code:

```text
ai_agent.py
test_ai.py
```

`config.py` also still contains legacy AI provider/model fields.

However, the current UK scraper is intentionally configured with:

```python
USE_AI = False
```

and the current `scraper.py` is designed to run without an AI API call.

Therefore:

- no Gemini key is required for the current UK run
- no OpenAI key is required
- no Groq key is required
- no Ollama model is required

The old AI files are retained as historical/optional development code and should not be confused with the current production extraction path.

---

## Current limitations

The project currently has several important limitations.

### 1. No checkout verification

The scraper does not automatically:

- visit the merchant store
- select a product
- add it to cart
- locate the merchant coupon input
- apply every discovered code
- verify discount amount

That would be a separate browser-automation stage with merchant-specific complexity.

### 2. Coupon sites change frequently

Third-party coupon pages can change:

- HTML structure
- button labels
- popup behaviour
- anti-bot systems
- JavaScript rendering
- redirects
- authentication requirements

No static scraper can guarantee indefinite compatibility without maintenance.

### 3. Search engine markup can change

Google frequently changes result DOM structure. The current code already uses several selectors and redirect normalisation, but additional maintenance may still be required over time.

### 4. False positives are possible

Format validation reduces noise but cannot prove a token is a real working coupon.

Always use `source_url` for manual audit when accuracy matters.

### 5. False negatives are possible

Some real codes can be hidden inside:

- shadow DOM
- iframes
- images
- canvas content
- obfuscated scripts
- authenticated content
- site-specific interactions not covered by generic reveal rules

### 6. No automatic proxy/geolocation network layer

The project currently uses search-engine country parameters and browser locale. It does not automatically route traffic through a UK residential IP.

Search localisation and true network geolocation are related but not identical concepts.

### 7. No concurrency

The current flow processes brands and pages sequentially. This is slower but simpler and less aggressive than parallel browser scraping.

---

## Operational notes

### Keep Chrome visible during manual runs

Current configuration:

```python
HEADLESS = False
```

This is useful because you can immediately see:

- search results
- cookie banners
- CAPTCHA pages
- reveal popups
- unexpected redirects

### Do not interact with the automated tab unnecessarily

Manual interaction can alter page state while Playwright is working. Only intervene when required, for example to solve a CAPTCHA.

### Search delays are intentional

The default delay range is not a performance bug. It is a deliberate operational trade-off between throughput and the risk of search-engine throttling.

### Review output after each batch

Recommended review order:

1. `urls_uk.csv` — confirms search discovery.
2. `results_uk.csv` — confirms extracted codes.
3. `source_url` — audit suspicious codes.
4. `method` — understand how each code was obtained.

### Keep historical and current outputs separate

The current UK filenames were intentionally separated from older generic files so previous US/experimental data does not get mixed with the UK batch.

---

## Development notes

### Main entry point

```text
scraper.py
```

### Configuration

```text
config.py
```

### Input

```text
brands.txt
```

### Recommended pre-run checks

```powershell
git pull origin main
python -m py_compile scraper.py
python scraper.py
```

### Recommended code-change workflow

Before changing extraction behaviour:

1. Reproduce the problem with one brand.
2. Observe the rendered page in visible Chrome.
3. Identify whether the failure is in search discovery or coupon extraction.
4. Prefer a targeted selector/normalisation rule over a broad noisy rule.
5. Keep source URL and extraction method in output for auditability.
6. Syntax-check before a full 21-brand run.

### Search discovery vs coupon extraction

Treat these as two separate subsystems when debugging:

**Search discovery problem:**

```text
Google visibly shows results
but console shows 0 links
```

Focus on `extract_search_links()` and URL normalisation.

**Coupon extraction problem:**

```text
search links are found
source pages are visited
but no codes are produced
```

Focus on HTML mining, text rules, reveal buttons, clipboard and modal handling.

Keeping these failure modes separate makes debugging much faster.

---

## Responsible use

Use the scraper only where you are authorised to access and process the relevant public pages, and respect applicable website terms, robots policies, rate limits and laws.

Avoid excessive request rates or actions that could disrupt third-party services.

---

## Quick start

For the current Windows/UK setup:

```powershell
git pull origin main
pip install playwright
python -m py_compile scraper.py
python scraper.py
```

Main output:

```text
results_uk.csv
```

Search discovery/debug output:

```text
urls_uk.csv
```

Deduplication state:

```text
data/seen_codes_uk.json
```

Current mode:

```text
UK / Google / en-GB / AI OFF / visible Chrome
```
