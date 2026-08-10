import os
import re
import csv
import io
import time
import smtplib
import urllib.parse
import urllib.request
import feedparser
import requests
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
    "Marketing Dive": "https://www.marketingdive.com/feeds/news/",
    "ExchangeWire JP": "https://www.exchangewire.jp/feed/",
    "MarkeZine": "https://markezine.jp/rss/new/20/index.xml",
}

MAX_PER_FEED = 6
HOURS_BACK = 48
MAX_ARTICLES_IN_MAIL = 8
MAX_FEEDBACK_ROWS = 60
JST = timezone(timedelta(hours=9))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

EXCLUDE_KEYWORDS = [
    "hires", "promotes", "appoints", "joins", "names ",
    "award", "webinar", "podcast", "sponsored",
]


# ========== RSS取得 ==========

def fetch_feed(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=30) as res:
        return feedparser.parse(res.read())


def is_excluded(title):
    low = title.lower()
    return any(k in low for k in EXCLUDE_KEYWORDS)


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
                title = entry.get("title", "")
                if not title or is_excluded(title):
                    continue
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                articles.append({
                    "source": name,
                    "title": title,
                    "link": entry.get("link", ""),
                    "summary": re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:500],
                })
                count += 1
            print(f"[OK] {name}: 採用{count}件 / フィード内{total}件")
        except Exception as e:
            print(f"[WARN] {name}: {type(e).__name__} {e}")
    return articles


# ========== フィードバック読み込み ==========

def load_feedback():
    url = os.environ.get("FEEDBACK_CSV_URL")
    if not url:
        print("[INFO] FEEDBACK_CSV_URL 未設定")
        return [], []

    try:
        res = requests.get(url, timeout=30)
        res.raise_for_status()
        res.encoding = "utf-8"
        rows = list(csv.reader(io.StringIO(res.text)))
        if len(rows) < 2:
            print("[INFO] フィードバックなし")
            return [], []

        good, bad = [], []
        # 列構成: [0]タイムスタンプ [1]タイトル [2]評価 [3]理由
        for row in rows[1:][-MAX_FEEDBACK_ROWS:]:
            if len(row) < 3:
                continue
            title = row[1].strip()
            rating = row[2].strip().lower()
            reason = row[3].strip() if len(row) > 3 else ""
            if not title:
                continue
            if "good" in rating:
                good.append(title)
            elif "bad" in rating:
                bad.append(f"{title}" + (f" ／ 理由: {reason}" if reason else ""))

        print(f"[OK] フィードバック: GOOD {len(good)}件 / BAD {len(bad)}件")
        return good, bad
    except Exception as e:
        print(f"[WARN] フィードバック読込失敗: {type(e).__name__} {e}")
        return [], []


def build_preference_block(good, bad):
    if not good and not bad:
        return ""

    parts = ["\n# 読者の過去の評価（最優先で考慮すること）\n"]
    if good:
        parts.append("## 高評価だった記事の例")
        parts.extend(f"- {g}" for g in good[-20:])
        parts.append("")
    if bad:
        parts.append("## 低評価だった記事の例（理由付き）")
        parts.extend(f"- {b}" for b in bad[-20:])
        parts.append("")
    parts.append(
        "上記から読者の関心領域と不要な話題の傾向を推論し、"
        "低評価に類似する記事は選定から除外すること。\n"
    )
    return "\n".join(parts)


# ========== 要約 ==========

def call_groq(prompt):
    api_key = os.environ["GROQ_API_KEY"]
    last_error = None
    for model in GROQ_MODELS:
        for attempt in range(3):
            try:
                res = requests.post(
                    GROQ_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system",
                             "content": "あなたは日本の広告業界に精通したアナリストです。必ず日本語で回答します。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 4000,
                    },
                    timeout=120,
                )
                if res.status_code == 429:
                    print(f"[WARN] {model} レート制限。20秒待機")
                    time.sleep(20)
                    continue
                res.raise_for_status()
                print(f"[OK] 要約成功: {model}")
                return res.json()["choices"][0]["message"]["content"]
            except Exception as e:
                last_error = e
                print(f"[WARN] {model} 試行{attempt+1}: {type(e).__name__}")
                time.sleep(5)
    raise RuntimeError(f"全モデルで失敗: {last_error}")


def select_and_summarize(articles, preference):
    indexed = "\n\n".join(
        f"ID:{i}\n媒体:{a['source']}\n原題:{a['title']}\nURL:{a['link']}\n概要:{a['summary']}"
        for i, a in enumerate(articles)
    )

    prompt = f"""以下はアドテク業界の最新記事一覧です。
広告事業に関わる日本のビジネスパーソン向けに、重要度が高いものを最大{MAX_ARTICLES_IN_MAIL}件選び、日本語で要約してください。
{preference}
# 出力形式（厳守。マークダウン記法は使用禁止）
選んだ記事ごとに、以下のブロックを繰り返してください。

@@@ID:元記事のID番号@@@
━━━━━━━━━━━━━━━━━━━━
[通し番号]. 日本語の見出し
━━━━━━━━━━━━━━━━━━━━
【概要】
要点を2〜3行で説明

【注目ポイント】
なぜ重要かを一言

【出典】媒体名
URL


# 重要な制約
- 冒頭の @@@ID:数字@@@ は必ず出力すること（システムが使用します）
- IDは記事一覧に記載された正確な番号を使うこと
- プライバシー規制、Cookie、CTV、リテールメディア、AI活用、M&A、大手プラットフォーム動向を優先
- 製品宣伝、人事異動、イベント告知は除外
- 専門用語は原語のまま（SSP, DSP, PMP など）
- アスタリスクやシャープなどの装飾記号は使わない
- 説明文や前置きは一切書かず、上記ブロックのみ出力すること

# 記事一覧
{indexed}
"""
    return call_groq(prompt)


def parse_selected_ids(text, total):
    ids = []
    for m in re.finditer(r"@@@ID:(\d+)@@@", text):
        i = int(m.group(1))
        if 0 <= i < total and i not in ids:
            ids.append(i)
    return ids


# ========== 評価リンク ==========

def build_rating_section(selected_articles):
    base = os.environ.get("FORM_BASE_URL")
    e_title = os.environ.get("FORM_ENTRY_TITLE")
    e_rating = os.environ.get("FORM_ENTRY_RATING")

    if not all([base, e_title, e_rating]):
        print("[INFO] フォーム設定が未完了のため評価リンクをスキップ")
        return ""

    lines = [
        "\n\n",
        "════════════════════════════════════",
        "  この配信の精度を上げるためのご協力",
        "════════════════════════════════════",
        "",
        "GOOD → クリックして送信ボタンを押すだけ（入力不要）",
        "BAD  → クリック後、理由を一言だけご記入ください",
        "",
    ]

    for n, a in enumerate(selected_articles, 1):
        title = a["title"][:180]
        q = urllib.parse.quote(title, safe="")
        lines.append(f"{n}. {title}")
        lines.append(f"   [GOOD] {base}&{e_title}={q}&{e_rating}=good")
        lines.append(f"   [BAD ] {base}&{e_title}={q}&{e_rating}=bad")
        lines.append("")

    return "\n".join(lines)


# ========== メール ==========

def build_fallback(articles):
    lines = ["※AI要約に失敗したため、記事一覧のみお送りします。\n"]
    for i, a in enumerate(articles[:15], 1):
        lines.append(f"{i}. [{a['source']}] {a['title']}\n   {a['link']}\n")
    return "\n".join(lines)


def send_mail(text):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]
    mail_to = os.environ["MAIL_TO"]

    today = datetime.now(JST).strftime("%Y/%m/%d")
    footer = "\n\n────────────────\nこのメールは自動配信されています。\n"

    msg = MIMEText(text + footer, "plain", "utf-8")
    msg["Subject"] = Header(f"アドテクニュース {today}", "utf-8")
    msg["From"] = formataddr((str(Header("AdTech News", "utf-8")), gmail_user))
    msg["To"] = mail_to

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(gmail_user, gmail_pass)
        server.send_message(msg)

    print("[OK] メール送信完了")


# ========== メイン ==========

if __name__ == "__main__":
    arts = fetch_articles()
    print(f"合計取得記事数: {len(arts)}")

    if not arts:
        send_mail("本日は対象期間内の新着記事がありませんでした。")
        raise SystemExit

    good, bad = load_feedback()
    pref = build_preference_block(good, bad)

    try:
        raw = select_and_summarize(arts, pref)
        ids = parse_selected_ids(raw, len(arts))
        selected = [arts[i] for i in ids]
        body = re.sub(r"@@@ID:\d+@@@\s*", "", raw)
        print(f"[OK] 選定記事数: {len(selected)}")

        if not selected:
            print("[WARN] ID抽出失敗。上位記事を評価対象にします")
            selected = arts[:MAX_ARTICLES_IN_MAIL]

        body += build_rating_section(selected)
    except Exception as e:
        print(f"[ERROR] 要約失敗: {e}")
        body = build_fallback(arts)
        body += build_rating_section(arts[:MAX_ARTICLES_IN_MAIL])

    send_mail(body)
