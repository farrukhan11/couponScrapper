# Coupon Code Research Workflow

## User input required

For each new request, collect:

- Brand/store name
- Search region/country
- Optional exact keyword
- Optional date or language preference

Example: `Brand: Revomadic | Region: Google US | Keyword: coupon code`

## 1. Open the regional Google search

Use the regional Google homepage/search settings before searching.

For Google US, use:

`https://www.google.com/?gl=us&hl=en&pws=0&gws_rd=cr`

Adjust `gl` for the requested country and `hl` for the requested language. Keep `pws=0` when a less-personalized result set is requested.

Run the exact query:

`<brand> coupon code`

Do not silently replace the brand name or query spelling.

## 2. Capture Google result URLs

Record the visible result order and classify every result as either `sponsored` or `organic`.

Capture:

- Result type
- Rank within that type
- Result title
- Google result URL
- Exact landing URL after opening/clicking the result

For the standard task, collect:

- All visible sponsored results
- The top 10 visible organic results

Never substitute a plain domain for a result URL. Open the result and record the final landing URL after redirects.

## 3. Visit every captured site

Open each sponsored and organic landing URL individually.

On every page, inspect:

- Visible coupon cards
- `Get Code`, `Reveal Code`, `Show Code`, `Copy`, `Use Code`, or similar buttons
- Discount percentage or fixed amount
- Expiry date
- Eligibility restrictions
- Verification/success status
- Automatic deals that do not require a code

## 4. Reveal and scrape codes

For every coupon card:

1. Click the reveal/get/copy button.
2. Read the visible modal, clipboard value, URL parameters, or post-click page state.
3. Inspect rendered DOM data attributes such as `data-code`, `data-coupon-code`, and `data-promo-code`.
4. Inspect page-embedded data only when it is part of the rendered page and accessible through normal page inspection.
5. Record every unique code found on the page, not only the top or best code.

A code is considered found when it is:

- Visible on the page
- Revealed after a button click
- Returned in the clipboard after a copy action
- Present in the page's accessible data attributes or rendered page payload

Do not invent or infer a code from a percentage-only offer.

## 5. Record the coupon data

For each code, record:

- Source type: sponsored or organic
- Source rank
- Source title
- Source URL
- Coupon code
- Discount percentage or fixed amount
- Offer type: code, automatic offer, deal, or free shipping
- Status: revealed, verified, community reported, listed, hidden, or not exposed
- Expiry date when available
- Restrictions/notes

If a page has an offer but no code, record the offer with a blank code and mark it `automatic offer` or `no code exposed`.

If a button redirects to the merchant and the code is not accessible, record the visible offer and mark the code as `not exposed`. Do not create a fake code.

## 6. Deduplicate without losing provenance

Keep the same code as separate rows when it appears on different source URLs. This preserves source provenance.

Within the same source URL, remove exact duplicate rows caused by repeated DOM elements or repeated card markup.

Normalize only obvious formatting differences:

- Trim whitespace
- Preserve capitalization unless the source clearly normalizes it
- Preserve fixed amounts such as `$20 off`
- Keep codes like `impact5` exactly as displayed

## 7. Create the output workbook

Create an Excel workbook with two sheets:

### Sheet 1: `Google US URLs`

Use a regional name when needed, such as `Google UK URLs`.

Columns:

`type, rank, title, url`

This sheet contains the sponsored and organic Google result URLs and final landing URLs.

### Sheet 2: `Codes by Site`

Columns:

`source_type, source_rank, source_title, source_url, code, discount_or_offer, status, notes`

This sheet contains every code and offer found across every result page.

Use a clean table, frozen header row, wrapped text, readable column widths, and source URLs as plain text links.

## 8. Quality checks before delivery

Confirm:

- Every captured Google result has a row in Sheet 1.
- Every source URL was visited or has a clear access limitation.
- Every visible/revealed code is recorded in Sheet 2.
- Multiple codes from the same site are all included.
- No code was inferred from a discount percentage alone.
- Plain domains were not substituted for final landing URLs.
- Sponsored and organic classifications are preserved.
- Duplicate DOM entries were removed within each source.
- Codes marked verified/listed are not presented as guaranteed valid at checkout.
- The workbook opens successfully and both sheets are present.

## 9. Delivery format

Deliver:

- The final `.xlsx` workbook
- A short summary of how many sponsored results, organic results, source pages, codes, and automatic offers were recorded
- Any pages where codes were hidden, blocked, expired, or not exposed

## Important operating rule

The next request may provide only a brand and region. Follow this document in order. If the user reports a change in the required result count, region, query, output columns, or verification standard, apply that change to the current run and update this document only if the user explicitly asks for a workflow change.
