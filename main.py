import os
import smtplib
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
HOURS_BACK = 30
JST = timezone(timedelta(hours=9))


def fetch_articles():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    articles = []
    for name, url in FEEDS.items():
        try:
            feed = feedparser.parse(url)
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
            print(f"[OK] {name}: {count}件")
        except Exception as e:
            print(f"[WARN] {name}: {e}")
    return articles


def summarize(articles):
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-2.0-flash")

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

（1件ごとに空行を1つ入れる）

# 選定ルール
- プライバシー規制、Cookie、CTV、リテールメディア、AI活用、M&A、大手プラットフォーム動向を優先
- 単なる製品宣伝、人事異動、イベント告知は除外
- 専門用語は原語のまま（SSP, DSP, PMP など）
- マークダウン記法（**や##）は使わない

# 記事一覧
{body}
"""
    response = model.generate_content(prompt)
    return response.text


def send_mail(text):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]
    mail_to = os.environ["MAIL_TO"]

    today = datetime.now(JST).strftime("%Y/%m/%d")
    header = f"アドテクニュース {today}\n\n"
    footer = "\n\n---\nこのメールは自動配信されています。\n"

    msg = MIMEText(header + text + footer, "plain", "utf-8")
    msg["Subject"] = Header(f"📰 アドテクニュース {today}", "utf-8")
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
        send_mail(summarize(arts))
