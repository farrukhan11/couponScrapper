from google import genai
import config

client = genai.Client(api_key=config.AI_API_KEY)
models = client.models.list()
for m in models:
    if "flash" in m.name:
        print(m.name)
