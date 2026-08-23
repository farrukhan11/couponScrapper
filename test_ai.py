"""
AI KEY TEST — scraper chalane se PEHLE ye chalayen
Usage: python test_ai.py
"""
import config

def main():
    api_key = getattr(config, "AI_API_KEY", "")
    if not api_key or "PASTE" in api_key.upper():
        print("❌ Pehle config.py mein apni Gemini API key paste karo!")
        return

    print("🔑 Key mili, ab test shuru...\n")

    # Library load karo (nayi ya purani)
    mode = None
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        mode = "new"
    except ImportError:
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            mode = "old"
        except ImportError:
            print("❌ Library nahi ha! Pehle chalao: pip install google-generativeai")
            return

    # Models try karo — jo bhi chale wo bata denge
    for m in ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.6-flash"]:
        try:
            if mode == "new":
                txt = client.models.generate_content(
                    model=m, contents="Reply with exactly: OK").text
            else:
                txt = genai.GenerativeModel(m).generate_content(
                    "Reply with exactly: OK").text
            print(f"✅ SAB KUCH SAHI HA! Chalne wala model: {m}")
            print(f"   AI Reply: {txt}")
            print(f"\n👉 config.py mein ye set karo:")
            print(f'   AI_PROVIDER = "gemini"')
            print(f'   AI_MODEL = "{m}"')
            print("\n🚀 Ab be-fikar ho kar python scraper.py chalao!")
            return
        except Exception as e:
            print(f"⚠️ {m} fail: {str(e)[:120]}")

    print("\n❌ Koi model nahi chala — API key galat ha ya internet issue ha")

if __name__ == "__main__":
    main()