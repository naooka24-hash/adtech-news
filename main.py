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
    # ===== アドテク専門 =====
    "AdExchanger": "https://www.adexchanger.com/feed/",
    "Digiday": "https://digiday.com/feed/",
    "AdMonsters": "https://www.admonsters.com/feed/",
    "ExchangeWire": "https://www.exchangewire.com/feed/",
    "Adweek": "https://www.adweek.com/feed/",
    "MarTech": "https://martech.org/feed/",
    "Marketing Dive": "https://www.marketingdive.com/feeds/news/",
    "Mobile Dev Memo": "https://mobiledevmemo.com/feed/",

    # ===== 業界ニュース全般 =====
    "The Drum": "https://www.thedrum.com/rss.xml",
    "Campaign US": "https://www.campaignlive.com/rss/campaignus",
    "Marketing Brew": "https://www.marketingbrew.com/feed",
    "Ad Age": "https://adage.com/rss/latest-news",
    "MediaPost Online Media": "https://feeds.mediapost.com/online-media-daily",
    "MediaPost RTD": "https://feeds.mediapost.com/real-time-daily",

    # ===== 検索・プラットフォーム =====
    "Search Engine Land": "https://searchengineland.com/feed",
    "Search Engine Journal": "https://www.searchenginejournal.com/feed/",
    "Search Engine Roundtable": "https://www.seroundtable.com/index.rdf",

    # ===== CTV・動画 =====
    "StreamTV Insider": "https://www.streamtvinsider.com/rss/xml",
    "TVREV": "https://www.tvrev.com/news?format=rss",

    # ===== プライバシー =====
    "IAPP": "https://iapp.org/feed/",

    # ===== テック大手 =====
    "TechCrunch": "https://techcrunch.com/feed/",
    "The Verge": "https://www.theverge.com/rss/index.xml",

    # ===== 国内 =====
    "ExchangeWire JP": "https://www.exchangewire.jp/feed/",
    "MarkeZine": "https://markezine.jp/rss/new/20/index.xml",
    "Web担当者Forum": "https://webtan.impress.co.jp/rss.xml",
    "AdverTimes": "https://www.advertimes.com/feed/",
    "DIGIDAY JP": "https://digiday.jp/feed/",
    "Media Innovation": "https://media-innovation.jp/feed/",
    "ITmedia マーケティング": "https://rss.itmedia.co.jp/rss/2.0/marketing.xml",
    "アタラ unyoo.jp": "https://www.atara.co.jp/unyoojp/feed/",

}

MAX_PER_FEED = 4
HOURS_BACK = 30
MAX_ARTICLES_IN_MAIL = 10
MAX_TOTAL_ARTICLES = 90
MAX_FEEDBACK_ROWS = 60
JST = timezone(timedelta(hours=9))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

EXCLUDE_KEYWORDS = [
    "hires", "promotes", "appoints", "joins ", "names ", "steps down",
    "departs", "new ceo", "new cmo", "leadership change",
    "award", "webinar", "podcast", "sponsored",
    "register now", "join us", "upcoming event",
    "opinion:", "op-ed", "guest post", "q&a with",
    "photos:", "watch:", "listen:",
    "セミナー", "ウェビナー", "イベント開催", "登壇", "人事", "役員異動",
]


# ========== RSS取得 ==========

def fetch_feed(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=25) as res:
        return feedparser.parse(res.read())


def is_excluded(title):
    low = title.lower()
    return any(k in low for k in EXCLUDE_KEYWORDS)


def clean_html(text):
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&\w+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_articles():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    articles = []
    seen_titles = set()
    ok_count = 0

    for name, url in FEEDS.items():
        try:
            feed = fetch_feed(url)
            total = len(feed.entries)
            count = 0
            for entry in feed.entries:
                if count >= MAX_PER_FEED:
                    break
                title = entry.get("title", "").strip()
                if not title or is_excluded(title):
                    continue

                # 重複排除（先頭50文字で判定）
                key = title.lower()[:50]
                if key in seen_titles:
                    continue

                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue

                seen_titles.add(key)
                articles.append({
                    "source": name,
                    "title": title,
                    "link": entry.get("link", ""),
                    "summary": clean_html(entry.get("summary", ""))[:400],
                })
                count += 1
            print(f"[OK] {name}: 採用{count} / 全{total}")
            ok_count += 1
        except Exception as e:
            print(f"[NG] {name}: {type(e).__name__}")

    print(f"[INFO] フィード成功: {ok_count}/{len(FEEDS)}")
    return articles


def cap_articles(articles, limit=MAX_TOTAL_ARTICLES):
    """媒体ごとに均等に間引く"""
    if len(articles) <= limit:
        return articles

    by_source = {}
    for a in articles:
        by_source.setdefault(a["source"], []).append(a)

    result = []
    idx = 0
    while len(result) < limit:
        added = False
        for src in by_source:
            if idx < len(by_source[src]) and len(result) < limit:
                result.append(by_source[src][idx])
                added = True
        if not added:
            break
        idx += 1

    print(f"[INFO] {len(articles)}件 → {len(result)}件に絞込")
    return result


# ========== フィードバック ==========

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
            return [], []

        good, bad = [], []
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
                bad.append(title + (f" ／ 理由: {reason}" if reason else ""))

        print(f"[OK] FB: GOOD {len(good)} / BAD {len(bad)}")
        return good, bad
    except Exception as e:
        print(f"[WARN] FB読込失敗: {type(e).__name__}")
        return [], []


def build_preference_block(good, bad):
    if not good and not bad:
        return ""
    parts = ["\n# 読者の過去の評価（最優先で考慮）\n"]
    if good:
        parts.append("## 高評価だった記事")
        parts.extend(f"- {g}" for g in good[-20:])
        parts.append("")
    if bad:
        parts.append("## 低評価だった記事（理由付き）")
        parts.extend(f"- {b}" for b in bad[-20:])
        parts.append("")
    parts.append("上記から関心領域を推論し、低評価に類似する記事は除外すること。\n")
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
                        "max_tokens": 5000,
                    },
                    timeout=150,
                )
                if res.status_code == 429:
                    print(f"[WARN] {model} レート制限。25秒待機")
                    time.sleep(25)
                    continue
                if res.status_code == 413:
                    print(f"[WARN] {model} 入力過大")
                    break
                res.raise_for_status()
                print(f"[OK] 要約成功: {model}")
                return res.json()["choices"][0]["message"]["content"]
            except Exception as e:
                last_error = e
                print(f"[WARN] {model} 試行{attempt+1}: {type(e).__name__}")
                time.sleep(5)
    raise RuntimeError(f"全モデル失敗: {last_error}")


def select_and_summarize(articles, preference):
    indexed = "\n\n".join(
        f"ID:{i}\n媒体:{a['source']}\n原題:{a['title']}\nURL:{a['link']}\n概要:{a['summary']}"
        for i, a in enumerate(articles)
    )

    prompt = f"""以下はアドテク・デジタルマーケティング業界の最新記事一覧です。
日本の広告事業従事者向けに、重要度が高いものを最大{MAX_ARTICLES_IN_MAIL}件選び、日本語で要約してください。
{preference}
# 出力形式（厳守。マークダウン記法は禁止）
選んだ記事ごとに以下を繰り返す。

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


# 制約
- 冒頭の @@@ID:数字@@@ は必須（システムが使用）
- IDは記事一覧の正確な番号を使うこと
- 優先: プライバシー規制、Cookie、CTV、リテールメディア、AI活用、M&A、大手プラットフォーム動向、計測技術
- 除外: 製品宣伝、人事、イベント告知、単発キャンペーン事例
- 専門用語は原語のまま（SSP, DSP, PMP, CDP など）
- 同一の出来事を複数媒体が報じている場合は1件にまとめる
- 装飾記号（*, #）は使わない
- 前置きや説明文は書かず、ブロックのみ出力

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

def build_rating_section(selected):
    base = os.environ.get("FORM_BASE_URL")
    e_title = os.environ.get("FORM_ENTRY_TITLE")
    e_rating = os.environ.get("FORM_ENTRY_RATING")

    if not all([base, e_title, e_rating]):
        return ""

    lines = [
        "\n\n",
        "════════════════════════════════════",
        "  配信精度向上へのご協力のお願い",
        "════════════════════════════════════",
        "",
        "GOOD → クリックして送信を押すだけ（入力不要）",
        "BAD  → クリック後、理由を一言ご記入ください",
        "",
    ]

    for n, a in enumerate(selected, 1):
        title = a["title"][:180]
        q = urllib.parse.quote(title, safe="")
        lines.append(f"{n}. {title}")
        lines.append(f"   [GOOD] {base}&{e_title}={q}&{e_rating}=good")
        lines.append(f"   [BAD ] {base}&{e_title}={q}&{e_rating}=bad")
        lines.append("")

    return "\n".join(lines)


# ========== メール ==========

def build_fallback(articles):
    lines = ["※AI要約に失敗したため記事一覧のみ送信します。\n"]
    for i, a in enumerate(articles[:20], 1):
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
    print(f"取得記事数: {len(arts)}")

    if not arts:
        send_mail("本日は対象期間内の新着記事がありませんでした。")
        raise SystemExit

    arts = cap_articles(arts)
    good, bad = load_feedback()
    pref = build_preference_block(good, bad)

    try:
        raw = select_and_summarize(arts, pref)
        ids = parse_selected_ids(raw, len(arts))
        selected = [arts[i] for i in ids]
        body = re.sub(r"@@@ID:\d+@@@\s*", "", raw)
        print(f"[OK] 選定: {len(selected)}件")

        if not selected:
            print("[WARN] ID抽出失敗")
            selected = arts[:MAX_ARTICLES_IN_MAIL]

        body += build_rating_section(selected)
    except Exception as e:
        print(f"[ERROR] 要約失敗: {e}")
        body = build_fallback(arts)
        body += build_rating_section(arts[:MAX_ARTICLES_IN_MAIL])

    send_mail(body)
