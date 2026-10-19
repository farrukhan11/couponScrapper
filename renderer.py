"""
RENDERER — apna "Firecrawl-like" fetch tool (no proxy).

Real Chrome (patchright) → DIRECT navigation → Cloudflare/challenge wait →
optional reveal-button clicks → rendered DOM (+ optional markdown).

Features:
  - Direct page navigation (origin + in-page fetch ka bug khatam)
  - Cloudflare / "Just a moment" challenge wait
  - SPA detect (Nuxt/Next/React/Angular) → extra wait
  - Reveal buttons auto-click (REVEAL_JS)
  - Persistent profile (cf_clearance cookies bache)
  - Media block, retries, disk cache

Usage:
    import renderer
    res = renderer.render("https://simplycodes.com/store/shopathluna.com")
    res["html"], res["title"]
    batch = renderer.render_many([url1, url2], reveal=True)
"""
import hashlib
import html as html_mod
import json
import os
import re
import time

import config
from browser_engine import browser_context

CACHE_DIR = os.environ.get("RENDER_CACHE_DIR", ".render_cache")
CACHE_TTL = int(os.environ.get("RENDER_CACHE_TTL", "3600"))
DEFAULT_TIMEOUT = int(os.environ.get("RENDER_TIMEOUT", "45000"))
DEFAULT_WAIT = int(os.environ.get("RENDER_WAIT", "1800"))
CHALLENGE_WAIT = int(os.environ.get("RENDER_CHALLENGE_WAIT", "25000"))
REVEAL_WAIT = int(os.environ.get("RENDER_REVEAL_WAIT", "2500"))

CHALLENGE_RE = re.compile(
    r"(just a moment|checking your browser|attention required|cf-challenge"
    r"|verify you are human|enable javascript and cookies|access denied"
    r"|you have been blocked|incapsula)", re.I)
SPA_RE = re.compile(
    r"(__NUXT__|__NEXT_DATA__|id=[\"']app[\"']|id=[\"']__next[\"']"
    r"|importmap|data-reactroot|ng-version|data-v-app)", re.I)

REVEAL_JS = r"""
(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    // Navigation guard: reveal clicks se store par redirect na ho
    document.addEventListener('click', e => {
        const a = e.target.closest && e.target.closest('a');
        if (a) { try { e.preventDefault(); } catch (_) {} }
    }, true);
    try { window.open = () => null; } catch (_) {}
    const dismissWords = ["accept all","accept","allow all","got it","no thanks",
                          "dismiss","i agree","close"];
    document.querySelectorAll("button").forEach(b => {
        const t = (b.innerText || "").trim().toLowerCase();
        if (t && dismissWords.includes(t) && b.offsetParent) { try { b.click(); } catch(e){} }
    });
    await sleep(500);
    for (let i = 0; i < 3; i++) { window.scrollBy(0, 1200); await sleep(250); }
    window.scrollTo(0, 0); await sleep(250);
    const isReveal = t => {
        t = (t||"").trim().toLowerCase();
        return t && t.length <= 40 && ["get code","reveal code","view code",
            "copy code","show code","see code","reveal","show coupon","get coupon",
            "show discount","get discount","show voucher","get voucher","unmask",
            "unlock"].some(w => t.includes(w));
    };
    const sel = "button, a, [role='button']";
    for (let round = 0; round < 2; round++) {
        const more = [...document.querySelectorAll(sel)].find(b => {
            const t = (b.innerText||"").trim().toLowerCase();
            return t && t.length <= 30 && (/show\s+\d+\s+more/.test(t)
                || t === "show more" || t.includes("more coupons"));
        });
        if (!more) break;
        try { more.scrollIntoView({block:"center"}); more.click(); await sleep(1200); } catch(e){ break; }
    }
    const allPats = ["show all","reveal all","view all codes","show all codes",
                     "see all codes","all codes"];
    const ra = [...document.querySelectorAll(sel)].find(b => {
        const t = (b.innerText||"").trim().toLowerCase();
        return t && t.length <= 40 && allPats.some(p => t.includes(p));
    });
    if (ra) { try { ra.scrollIntoView({block:"center"}); ra.click(); await sleep(1800); } catch(e){} }
    let clicked = 0;
    for (const b of [...document.querySelectorAll(sel)]) {
        if (clicked >= 12) break;
        const t = (b.innerText||"").trim();
        if (!isReveal(t)) continue;
        try { b.scrollIntoView({block:"center"}); b.click(); clicked++;
              await sleep(900); } catch(e){ continue; }
    }
    return {clicked};
})()
"""


# ============================================================
# CACHE
# ============================================================
def _cache_key(url, reveal, wait_ms, js):
    raw = f"{url}|{int(reveal)}|{wait_ms}|{hashlib.sha1((js or '').encode()).hexdigest()}"
    return hashlib.sha1(raw.encode()).hexdigest()


def _cache_get(url, reveal, wait_ms, js):
    path = os.path.join(CACHE_DIR, _cache_key(url, reveal, wait_ms, js) + ".json")
    try:
        if time.time() - os.path.getmtime(path) > CACHE_TTL:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_set(url, reveal, wait_ms, js, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = os.path.join(CACHE_DIR, _cache_key(url, reveal, wait_ms, js) + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


# ============================================================
# HELPERS
# ============================================================
def is_challenge(html):
    return bool(CHALLENGE_RE.search((html or "")[:6000]))


def is_spa(html):
    return bool(SPA_RE.search((html or "")[:200000]))


def _to_markdown(html):
    if not html:
        return ""
    t = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<style\b.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>",
               lambda m: "\n" + "#" * int(m.group(1)) + " " + m.group(2) + "\n",
               t, flags=re.S | re.I)
    t = re.sub(r"<li[^>]*>", "\n- ", t, flags=re.I)
    t = re.sub(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
               lambda m: f"[{m.group(2)}]({m.group(1)})", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_mod.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


def _origin(url):
    m = re.match(r"^(https?://[^/]+)", url or "")
    return (m.group(1) + "/") if m else ""


def _render_one(ctx, url, engine, reveal, js, wait_for, wait_ms, timeout):
    page = ctx.new_page()
    status = None
    try:
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            status = resp.status if resp else None
        except Exception:
            status = status or 0

        # ---- warm-up: Cloudflare/403 par pehle origin visit (cf_clearance set) ----
        try:
            html0 = page.content()
        except Exception:
            html0 = ""
        origin = _origin(url)
        if (status in (403, 503) or is_challenge(html0)) and origin and \
                origin.rstrip("/") != (url or "").rstrip("/"):
            try:
                page.goto(origin, wait_until="domcontentloaded", timeout=timeout)
                page.wait_for_timeout(2500)
                resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                status = resp.status if resp else status
            except Exception:
                pass

        # ---- challenge wait ----
        waited = 0
        html = ""
        while waited < CHALLENGE_WAIT:
            try:
                html = page.content()
            except Exception:
                html = ""
            if html and not is_challenge(html):
                break
            page.wait_for_timeout(1500)
            waited += 1500

        # ---- optional selector wait ----
        if wait_for:
            try:
                page.wait_for_selector(wait_for, timeout=min(10000, timeout))
            except Exception:
                pass

        page.wait_for_timeout(wait_ms)

        # ---- SPA: thoda extra wait ----
        try:
            html = page.content()
        except Exception:
            html = ""
        if is_spa(html):
            page.wait_for_timeout(1500)
            try:
                html = page.content()
            except Exception:
                pass

        # ---- reveal clicks (navigation-safe) ----
        navigated = False
        if reveal:
            try:
                pre_html = html
                page.evaluate(REVEAL_JS)
                page.wait_for_timeout(REVEAL_WAIT)
                if _origin(page.url) != _origin(url):
                    navigated = True       # store par redirect ho gaya — pre-reveal HTML rakho
                    html = pre_html
                else:
                    html = page.content()
            except Exception:
                pass

        # ---- custom js ----
        if js:
            try:
                page.evaluate(js)
                page.wait_for_timeout(800)
                html = page.content()
            except Exception:
                pass

        try:
            title = page.title()
        except Exception:
            title = ""
        final_url = page.url
    finally:
        try:
            page.close()
        except Exception:
            pass

    return {
        "url": url, "final_url": final_url, "status": status, "title": title,
        "html": html, "engine": engine, "challenge": is_challenge(html),
        "spa": is_spa(html), "navigated": navigated, "elapsed": 0,
    }


# ============================================================
# PUBLIC
# ============================================================
def render(url, reveal=False, js=None, wait_for=None, wait_ms=None,
           timeout=None, return_markdown=False, use_cache=True):
    res = render_many([url], reveal=reveal, js=js, wait_for=wait_for,
                      wait_ms=wait_ms, timeout=timeout,
                      return_markdown=return_markdown, use_cache=use_cache)
    return res.get(url, {"url": url, "status": 0, "html": "", "title": "",
                         "engine": "", "error": "render failed"})


def render_many(urls, reveal=False, js=None, wait_for=None, wait_ms=None,
                timeout=None, return_markdown=False, use_cache=True):
    urls = list(dict.fromkeys(u for u in urls if u))
    wait_ms = DEFAULT_WAIT if wait_ms is None else wait_ms
    timeout = DEFAULT_TIMEOUT if timeout is None else timeout
    out = {}
    todo = []
    for u in urls:
        if use_cache:
            c = _cache_get(u, reveal, wait_ms, js)
            if c:
                out[u] = c
                continue
        todo.append(u)
    if not todo:
        return out

    try:
        with browser_context(headless=getattr(config, "HEADLESS", False)) as (ctx, browser, engine):
            for u in todo:
                t0 = time.time()
                try:
                    r = _render_one(ctx, u, engine, reveal, js, wait_for, wait_ms, timeout)
                except Exception as e:
                    r = {"url": u, "status": 0, "html": "", "title": "",
                         "engine": engine, "error": str(e)[:150]}
                r["elapsed"] = round(time.time() - t0, 2)
                if return_markdown and r.get("html"):
                    r["markdown"] = _to_markdown(r["html"])
                if use_cache and r.get("html") and not r.get("challenge"):
                    _cache_set(u, reveal, wait_ms, js, r)
                out[u] = r
    except Exception as e:
        for u in todo:
            out.setdefault(u, {"url": u, "status": 0, "html": "", "title": "",
                              "engine": "", "error": str(e)[:150]})
    return out


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    urls = sys.argv[1:] or []
    if not urls:
        print("Usage: python renderer.py <url> [url2 ...]")
        sys.exit(1)
    results = render_many(urls, reveal=True, return_markdown=False)
    for u, r in results.items():
        print(f"{r.get('status')} len={len(r.get('html') or ''):>8} "
              f"challenge={r.get('challenge')} spa={r.get('spa')} "
              f"engine={r.get('engine')} {u}")
        print(f"   title: {r.get('title','')[:100]}")
