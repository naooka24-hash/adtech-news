import os
import requests

targets = [
    ("Cerebras", "https://api.cerebras.ai/v1/models", "CEREBRAS_API_KEY"),
    ("Groq", "https://api.groq.com/openai/v1/models", "GROQ_API_KEY"),
]

for name, url, env in targets:
    key = os.environ.get(env, "")
    print("")
    print("========== " + name + " ==========")
    if not key:
        print("キー未設定")
        continue
    try:
        res = requests.get(
            url,
            headers={"Authorization": "Bearer " + key},
            timeout=30,
        )
        print("Status: " + str(res.status_code))
        if res.status_code == 200:
            models = res.json().get("data", [])
            print("モデル数: " + str(len(models)))
            for m in sorted(models, key=lambda x: str(x.get("id", ""))):
                print("  " + str(m.get("id", "")))
        else:
            print(res.text[:400])
    except Exception as e:
        print("エラー: " + type(e).__name__ + " " + str(e)[:200])
