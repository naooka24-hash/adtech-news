import os
import time
import smtplib
import urllib.request
import feedparser
import google.generativeai as genai
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta, timezone

FEEDS = {
    "AdExchanger": "https://www.adexchanger.com/feed/",
    "Digiday": "https://digiday.com/feed/",
    "Search Engine Land": "https://searchengineland.com/feed",
    "MarTech": "https://martech.org/feed/",
    "The Drum": "https://www.thedrum.com/rss.xml",
    "ExchangeWire JP": "https://www.exchangewire.jp/feed/",
    "MarkeZine": "https://markezine.jp/rss/new/20/index.xml",
}

MAX_PER_FEED = 6
HOURS_BACK = 48
JST = timezone(timedelta(hours=9))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def fetch_feed(url):
    """User-Agentを付けてRSSを取得"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=30) as res:
        return feedparser.parse(res.read())


def fetch_articles():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    articles = []
    for name, url in FEEDS.items():
        try:
            feed = fetch_feed(url)
            total = len(feed.entries)
            count = 0
            for entry in feed.entries:
                if count >= MAX_PER_FEED:
                    break
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                articles.append({
                    "source": name,
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:600],
                })
                count += 1
            print(f"[OK] {name}: 採用{count}件 / フィード内{total}件")
        except Exception as e:
            print(f"[WARN] {name}: {type(e).__name__} {e}")
    return articles


def summarize(articles):
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

    body = "\n\n".join(
        f"[{a['source']}] {a['title']}\nURL: {a['link']}\n{a['summary']}"
        for a in articles
    )

    prompt = f"""以下はアドテク業界の最新記事一覧です。
広告事業に関わるビジネスパーソン向けに、重要度の高いものを最大8件選び、日本語で要約してください。

# 出力形式（プレーンテキスト。装飾記号は使わない）
━━━━━━━━━━━━━━━━━━━━
1. 日本語の見出し
━━━━━━━━━━━━━━━━━━━━
【概要】
要点を2〜3行で説明

【注目ポイント】
なぜ重要かを一言

【出典】媒体名
URL

# 選定ルール
- プライバシー規制、Cookie、CTV、リテールメディア、AI活用、M&A、大手プラットフォーム動向を優先
- 単なる製品宣伝、人事異動、イベント告知は除外
- 専門用語は原語のまま（SSP, DSP, PMP など）
- マークダウン記法（**や##）は使わない

# 記事一覧
{body}
"""

    models_to_try = ["gemini-2.0-flash", "gemini-flash-latest", "gemini-2.5-flash"]
    last_error = None

    for model_name in models_to_try:
        for attempt in range(3):
            try:
                model = genai.GenerativeModel(model_name)
                res = model.generate_content(prompt)
                print(f"[OK] 要約成功: {model_name}")
                return res.text
            except Exception as e:
                last_error = e
                msg = str(e)
                print(f"[WARN] {model_name} 試行{attempt+1}: {type(e).__name__}")
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    if "limit: 0" in msg:
                        break  # 枠自体がない → 次のモデルへ
                    time.sleep(65)
                    continue
                break  # その他のエラーは次のモデルへ

    raise RuntimeError(f"全モデルで失敗: {last_error}")


def build_fallback(articles):
    """要約失敗時: 記事リストだけ送る"""
    lines = ["※AI要約に失敗したため、記事一覧のみ送信します。\n"]
    for i, a in enumerate(articles, 1):
        lines.append(f"{i}. [{a['source']}] {a['title']}\n   {a['link']}\n")
    return "\n".join(lines)


def send_mail(text):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]
    mail_to = os.environ["MAIL_TO"]

    today = datetime.now(JST).strftime("%Y/%m/%d")
    footer = "\n\n---\nこのメールは自動配信されています。\n"

    msg = MIMEText(text + footer, "plain", "utf-8")
    msg["Subject"] = Header(f"アドテクニュース {today}", "utf-8")
    msg["From"] = formataddr((str(Header("AdTech News", "utf-8")), gmail_user))
    msg["To"] = mail_to

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(gmail_user, gmail_pass)
        server.send_message(msg)

    print(f"[OK] メール送信完了: {mail_to}")


if __name__ == "__main__":
    arts = fetch_articles()
    print(f"合計取得記事数: {len(arts)}")

    if not arts:
        send_mail("本日は対象期間内の新着記事がありませんでした。")
    else:
        try:
            content = summarize(arts)
        except Exception as e:
            print(f"[ERROR] 要約失敗: {e}")
            content = build_fallback(arts)
        send_mail(content)
