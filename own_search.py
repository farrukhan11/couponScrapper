"""
OWN SEARCH — hamara apna "Firecrawl search" (zero third-party API).

Google + Bing hamare apne Playwright browser se:
  - Headless chromium pehle try hota hai
  - CAPTCHA aaye to automatically HEADFUL real Chrome khulta hai
    (persistent profile chrome_profile_search/ — cookies yaad rehte hain,
     manually solve karo, pipeline khud aage barh jayegi)
  - Pacing built-in (6-10s random) — bot-detection se bachne ke liye
  - Google fail -> Bing (u=a1 base64 decode)

Usage (router se):
    searcher = OwnSearch()
    urls = await searcher.web_search('site:savoo.co.uk "boohoo"')
    await searcher.close()
"""
import asyncio
import base64
import os
import random
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

PROFILE_DIR = os.path.join(os.getcwd(), "chrome_profile_search")
GOOGLE_URL = "https://www.google.com/search?q={q}&num={num}&hl=en&gl=gb"
BING_URL = "https://www.bing.com/search?q={q}&count={num}&setlang=en-gb&cc=gb"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

GOOGLE_EXTRACT_JS = """() => {
    const out = [];
    const els = document.querySelectorAll('#search a[href], #rso a[href], div.g a[href], a:has(h3)');
    els.forEach(a => {
        let h = a.getAttribute('href');
        if (!h) return;
        if (h.startsWith('/url?')) {
            try {
                const p = new URLSearchParams(h.split('?')[1]);
                h = p.get('q') || p.get('url') || h;
            } catch (e) {}
        }
        if (h && h.startsWith('http') && !h.includes('google.') && !h.includes('gstatic')) out.push(h);
    });
    return [...new Set(out)];
}"""


def bing_decode(html):
    """Bing ke /ck/a redirect links ko base64 se real URLs me kholo."""
    urls = []
    for block in re.findall(r'<li class="b_algo".*?</li>', html, re.S):
        m = re.search(r"u=a1([A-Za-z0-9\-_]{20,})", block)
        if not m:
            m2 = re.search(r'href="(https?://[^"]+)"', block)
            if m2 and "bing.com" not in m2.group(1):
                urls.append(m2.group(1))
            continue
        try:
            dec = base64.urlsafe_b64decode(m.group(1) + "==" * 3).decode("utf-8", "ignore")
            if dec.startswith("http") and dec not in urls:
                urls.append(dec)
        except Exception:
            continue
    return urls


class OwnSearch:
    def __init__(self, headless=False, max_queries=60, delay=(6.0, 10.0)):
        # headless=False by default — Google headless ko turant detect kar
        # garbage/cloaked SERP deta hai. Real Chrome headful = reliable.
        self.headless = headless
        self.max_queries = max_queries
        self.delay = delay
        self.queries = 0
        self.disabled = False
        self._pw = None
        self._ctx = None
        self._lock = asyncio.Lock()

    # ---------------- browser lifecycle ----------------
    async def _ensure(self):
        if self._ctx is not None:
            return
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self._ctx = await self._launch()

    async def _launch(self):
        """Real Chrome headful + persistent profile (CAPTCHA cookies yaad rehte hain)."""
        kwargs = dict(
            user_data_dir=PROFILE_DIR,
            headless=False,
            locale="en-GB",
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        try:
            import config
            chrome_path = getattr(config, "CHROME_PATH", "")
            if chrome_path and os.path.exists(chrome_path):
                kwargs["executable_path"] = chrome_path
            else:
                print("   ⚠️ Real Chrome nahi mila — bundled chromium headful use ho raha hai")
        except Exception:
            pass
        return await self._pw.chromium.launch_persistent_context(**kwargs)

    async def _close_ctx(self):
        try:
            if self._ctx:
                await self._ctx.close()
        except Exception:
            pass
        self._ctx = None

    async def close(self):
        await self._close_ctx()
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        self._pw = None

    # ---------------- captcha ----------------
    @staticmethod
    def _is_captcha(url, html):
        low = (html or "")[:4000].lower()
        return ("/sorry/" in (url or "").lower() or "unusual traffic" in low
                or "recaptcha" in low or "/captcha/" in low)

    async def _wait_manual_solve(self, page):
        print("   🔐 Google CAPTCHA — Chrome window me solve karo (240s tak wait)…")
        deadline = 240
        while deadline > 0:
            await asyncio.sleep(3)
            deadline -= 3
            try:
                url, html = page.url, await page.content()
            except Exception:
                return False
            if not self._is_captcha(url, html):
                print("   ✅ CAPTCHA solve — continue")
                return True
        return False

    # ---------------- main API ----------------
    async def web_search(self, query, num=8):
        """Return list of result URLs (google pehle, bing fallback)."""
        async with self._lock:
            if self.disabled or self.queries >= self.max_queries:
                if not self.disabled and self.queries >= self.max_queries:
                    self.disabled = True
                    print("   ⚠️ Own-search query limit — is run ke liye disable")
                return []
            await self._ensure()
            await asyncio.sleep(random.uniform(*self.delay))
            self.queries += 1

            try:
                page = await self._ctx.new_page()
            except Exception:
                await self._close_ctx()   # browser band ho gaya tha — relaunch
                await self._ensure()
                try:
                    page = await self._ctx.new_page()
                except Exception:
                    return []
            try:
                # ---------- Google (real Chrome headful) ----------
                try:
                    await page.goto(GOOGLE_URL.format(q=_q(query), num=num),
                                    wait_until="domcontentloaded", timeout=25000)
                    await page.wait_for_timeout(1800)
                    html = await page.content()
                    if self._is_captcha(page.url, html):
                        await self._wait_manual_solve(page)
                        html = await page.content()
                    urls = await page.evaluate(GOOGLE_EXTRACT_JS)
                    if urls:
                        return urls
                except Exception:
                    pass

                # ---------- Bing fallback ----------
                try:
                    await page.goto(BING_URL.format(q=_q(query), num=num + 7),
                                    wait_until="domcontentloaded", timeout=25000)
                    await page.wait_for_timeout(2000)
                    html = await page.content()
                    return bing_decode(html)
                except Exception:
                    return []
            finally:
                try:
                    await page.close()
                except Exception:
                    pass


def _q(s):
    from urllib.parse import quote_plus
    return quote_plus(s)
