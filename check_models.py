import os
import requests

api_key = os.environ.get("GROQ_API_KEY", "")
if not api_key:
    print("GROQ_API_KEY が未設定です")
    raise SystemExit(1)

res = requests.get(
    "https://api.groq.com/openai/v1/models",
    headers={"Authorization": "Bearer " + api_key},
    timeout=30,
)

print("Status: " + str(res.status_code))
print("")

if res.status_code == 200:
    data = res.json()
    models = data.get("data", [])
    print("=== 利用可能なモデル (" + str(len(models)) + "件) ===")
    for m in sorted(models, key=lambda x: x.get("id", "")):
        mid = m.get("id", "")
        ctx = m.get("context_window", "?")
        owner = m.get("owned_by", "")
        print(mid)
        print("    context: " + str(ctx) + " / owner: " + str(owner))
else:
    print(res.text[:800])
