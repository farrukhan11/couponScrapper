"""
BROWSER ENGINE — shared stealth launch helper.

Features (Stage 1 stealth):
  - Engine: patchright (undetected) -> fallback playwright
  - Real Chrome (CHROME_PATH / CHROME_CHANNEL) ya bundled Chromium
  - Persistent profile (cf_clearance cookies bache) with graceful fallback
  - Media block (images/fonts/media) — bandwidth bachao
  - Proxy (PROXY_URL) — Stage 2 ready

Usage:
    from browser_engine import browser_context
    with browser_context(headless=True) as (ctx, browser, engine):
        page = ctx.new_page()
        ...
        page.close()
"""
import contextlib
import os

import config

VERSION = "stage1"


def launch_args():
    return ["--disable-blink-features=AutomationControlled",
            "--no-sandbox", "--disable-dev-shm-usage"]


def _launch_kwargs():
    """executable_path / channel / proxy — jo set ho."""
    kw = {}
    chrome = getattr(config, "CHROME_PATH", "") or ""
    if chrome and os.path.exists(chrome):
        kw["executable_path"] = chrome
    elif getattr(config, "CHROME_CHANNEL", ""):
        kw["channel"] = config.CHROME_CHANNEL
    proxy = getattr(config, "PROXY_URL", "") or ""
    if proxy:
        kw["proxy"] = {"server": proxy}
    return kw


def _engine_order():
    pref = getattr(config, "STEALTH_ENGINE", "auto")
    if pref == "patchright":
        return ["patchright"]
    if pref == "playwright":
        return ["playwright"]
    return ["patchright", "playwright"]


def _import_sync(engine):
    mod = __import__(f"{engine}.sync_api", fromlist=["sync_playwright"])
    return mod.sync_playwright


def _block_media(ctx):
    if not getattr(config, "BLOCK_MEDIA", True):
        return

    def _route(route):
        try:
            if route.request.resource_type in ("image", "media", "font"):
                route.abort()
            else:
                route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    try:
        ctx.route("**/*", _route)
    except Exception:
        pass


def _open(p, headless, viewport):
    """Persistent context -> fallback normal context. Return (ctx, browser)."""
    vp = viewport or {"width": 1280, "height": 800}
    kwargs = _launch_kwargs()
    use_persist = getattr(config, "USE_PERSISTENT_PROFILE", True)
    profile = getattr(config, "CHROME_PROFILE_DIR", "chrome_profile") or ""
    if use_persist and profile:
        profile_path = profile if os.path.isabs(profile) else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), profile)
        os.makedirs(profile_path, exist_ok=True)
        try:
            ctx = p.chromium.launch_persistent_context(
                profile_path, headless=headless, args=launch_args(),
                viewport=vp, **kwargs)
            _block_media(ctx)
            return ctx, None
        except Exception:
            pass
    browser = p.chromium.launch(headless=headless, args=launch_args(), **kwargs)
    ctx = browser.new_context(viewport=vp)
    _block_media(ctx)
    return ctx, browser


def _close(ctx, browser):
    try:
        if browser:
            browser.close()
        else:
            ctx.close()
    except Exception:
        pass


@contextlib.contextmanager
def browser_context(headless=None, viewport=None):
    """Yield (ctx, browser, engine_name). Engine/launch failure par agla engine."""
    if headless is None:
        headless = getattr(config, "HEADLESS", False)
    errors = []
    for engine in _engine_order():
        try:
            sw = _import_sync(engine)
        except Exception as e:
            errors.append(f"{engine}: import ({str(e)[:40]})")
            continue
        try:
            p_cm = sw()
            p = p_cm.__enter__()
        except Exception as e:
            errors.append(f"{engine}: start ({str(e)[:40]})")
            continue
        try:
            ctx, browser = _open(p, headless, viewport)
        except Exception as e:
            errors.append(f"{engine}: launch ({str(e)[:60]})")
            try:
                p_cm.__exit__(None, None, None)
            except Exception:
                pass
            continue
        try:
            yield ctx, browser, engine
        finally:
            try:
                _close(ctx, browser)
            finally:
                try:
                    p_cm.__exit__(None, None, None)
                except Exception:
                    pass
        return
    raise RuntimeError("browser launch fail: " + " | ".join(errors))
