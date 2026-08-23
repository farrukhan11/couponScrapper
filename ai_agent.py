"""
AI AGENT — har page visit par AI sochti ha, decide karti ha, agay barhti ha.
Providers: groq | gemini | openai | ollama
"""
import re
import json
import time
import random
import config

DECIDE_PROMPT = """You are an autonomous coupon-hunting agent controlling a real browser.
Target brand: {brand}
Current URL: {url}
Page title: {title}

PAGE TEXT (truncated):
\"\"\"
{text}
\"\"\"

CLICKABLE ELEMENTS (numbered):
{buttons}

Find real coupon/promo/discount codes for the brand.
Return STRICT JSON only — ONE of these:

A) Real code(s) already visible in the text:
{{"action": "codes", "codes": ["SAVE20"], "reason": "..."}}

B) A numbered element likely reveals/copies/unmasks a code — click it:
{{"action": "click", "n": 7, "reason": "..."}}

C) Nothing useful on this page:
{{"action": "skip", "reason": "..."}}

Rules:
- Real codes are short tokens usable at checkout (SAVE20, WELCOME10, EXTRA150).
- Product names, categories, nav links, article words are NOT codes.
- Prefer "click" when codes look masked/hidden behind a button."""

EXTRACT_PROMPT = """You clicked "{btn}" on {url}. State after click:

CLIPBOARD CONTENT: {clip}
MODAL/POPUP TEXT: {modal}
NEW TEXT APPEARED: {diff}

Extract ONLY real coupon/promo codes now available.
Return STRICT JSON: {{"codes": ["CODE1"]}} or {{"codes": []}}"""

MODAL_SELECTORS = [
    "[role='dialog']", "[class*='modal']", "[class*='Modal']",
    "[class*='popup']", "[class*='Popup']", "[class*='overlay']",
    "[class*='coupon']", "[class*='reveal']", "[id*='coupon']",
]


def _call_llm(prompt):
    provider = config.AI_PROVIDER.lower()
    if provider == "openai":
        from openai import OpenAI
        r = OpenAI(api_key=config.AI_API_KEY).chat.completions.create(
            model=config.AI_MODEL or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}], temperature=0)
        return r.choices[0].message.content
    if provider == "groq":
        from groq import Groq
        r = Groq(api_key=config.AI_API_KEY).chat.completions.create(
            model=config.AI_MODEL or "llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}], temperature=0)
        return r.choices[0].message.content
    if provider == "gemini":
        try:
            from google import genai
            return genai.Client(api_key=config.AI_API_KEY).models.generate_content(
                model=config.AI_MODEL or "gemini-2.0-flash", contents=prompt).text
        except ImportError:
            import google.generativeai as genai
            genai.configure(api_key=config.AI_API_KEY)
            return genai.GenerativeModel(
                config.AI_MODEL or "gemini-1.5-flash").generate_content(prompt).text
    if provider == "ollama":
        import ollama
        return ollama.chat(model=config.AI_MODEL or "llama3.1",
                           messages=[{"role": "user", "content": prompt}])["message"]["content"]
    raise ValueError(f"Unknown provider: {provider}")


def _parse_json(raw):
    if not raw:
        return None
    m = re.search(r'\{.*\}', raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _valid(c):
    return isinstance(c, str) and 4 <= len(c.strip()) <= 20 \
        and re.match(r'^[A-Za-z0-9\-_]+$', c.strip())


def read_clipboard(page):
    try:
        txt = page.evaluate("() => navigator.clipboard.readText()")
        return (txt or "").strip()
    except Exception:
        return ""


def collect_modal_text(page):
    parts = []
    for sel in MODAL_SELECTORS:
        try:
            for el in page.query_selector_all(sel):
                try:
                    if el.is_visible():
                        parts.append(el.inner_text())
                except Exception:
                    continue
        except Exception:
            pass
    return " | ".join(parts)[:2000]


def get_page_state(page, max_buttons=40):
    state = {"url": page.url, "title": "", "text": "", "buttons": []}
    els = []
    try:
        state["title"] = page.title()
    except Exception:
        pass
    try:
        state["text"] = page.inner_text("body")[:6000]
    except Exception:
        pass
    try:
        raw = page.query_selector_all("button, a, [role='button']")
    except Exception:
        raw = []
    n = 0
    for el in raw:
        if n >= max_buttons:
            break
        try:
            t = (el.inner_text() or "").strip()
            if t and 3 <= len(t) <= 40 and el.is_visible():
                state["buttons"].append({"n": n, "text": t})
                els.append(el)
                n += 1
        except Exception:
            continue
    return state, els


def ai_decide(state, brand):
    btn_lines = "\n".join(f"[{b['n']}] {b['text']}" for b in state["buttons"]) or "(none)"
    prompt = DECIDE_PROMPT.format(brand=brand, url=state["url"], title=state["title"],
                                  text=state["text"], buttons=btn_lines)
    return _parse_json(_call_llm(prompt))


def ai_extract_after_click(page, btn_text, before_text):
    clip = read_clipboard(page)
    modal = collect_modal_text(page)
    after = ""
    try:
        after = page.inner_text("body")
    except Exception:
        pass
    diff = " ".join(list(set(after.split()) - set(before_text.split()))[:400])
    prompt = EXTRACT_PROMPT.format(btn=btn_text, url=page.url,
                                   clip=clip or "(empty)",
                                   modal=modal or "(none)",
                                   diff=diff or "(none)")
    try:
        data = _parse_json(_call_llm(prompt)) or {}
    except Exception as e:
        print(f"    ⚠️ AI extract error: {e}")
        return []
    return [{"code": c.strip(), "method": "ai"}
            for c in data.get("codes", []) if _valid(c)]


def ai_agent_visit(page, brand):
    """AI agent: sochti ha → decide karti ha → act karti ha → agay barhti ha."""
    found = []
    steps = getattr(config, "AI_MAX_STEPS", 3)
    for step in range(steps):
        state, els = get_page_state(page)
        try:
            decision = ai_decide(state, brand) or {}
        except Exception as e:
            print(f"    ⚠️ AI decide error: {e}")
            break
        action = str(decision.get("action", "skip")).lower()
        print(f"       🤖 AI step {step+1}: {action} — {str(decision.get('reason',''))[:70]}")

        # A) Codes seedha mil gaye
        if action == "codes":
            for c in decision.get("codes", []):
                if _valid(c):
                    found.append({"code": c.strip(), "method": "ai"})
            break

        # B) AI ne kaha: ye button click karo
        if action == "click":
            n = decision.get("n")
            if not isinstance(n, int) or n < 0 or n >= len(els):
                break
            btn_text = state["buttons"][n]["text"]
            before = state["text"]
            pages_before = len(page.context.pages)
            try:
                els[n].scroll_into_view_if_needed()
                els[n].click(timeout=5000)
                time.sleep(random.uniform(1.5, 2.5))
            except Exception:
                print("       ⚠️ click fail — next step")
                continue
            # Naya tab khula?
            if len(page.context.pages) > pages_before:
                np = page.context.pages[-1]
                try:
                    np.wait_for_load_state("domcontentloaded", timeout=8000)
                    found.extend(ai_extract_after_click(np, btn_text, ""))
                except Exception:
                    pass
                try:
                    np.close()
                except Exception:
                    pass
            found.extend(ai_extract_after_click(page, btn_text, before))
            continue  # agay step: AI naya state dekhegi

        # C) Skip
        break
    return found