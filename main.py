import os
import re
import csv
import io
import time
import json
import html
import smtplib
import urllib.parse
import urllib.request
import feedparser
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timedelta, timezone

FEEDS = {
    # ===== アドテク専門 =====
    "AdExchanger": "https://www.adexchanger.com/feed/",
    "Digiday": "https://digiday.com/feed/",
    "AdMonsters": "https://www.admonsters.com/feed/",
    "ExchangeWire": "https://www.exchangewire.com/feed/",
    "MarTech": "https://martech.org/feed/",
    "Marketing Dive": "https://www.marketingdive.com/feeds/news/",
    "Mobile Dev Memo": "https://mobiledevmemo.com/feed/",

    # ===== 業界ニュース =====
    "The Drum": "https://www.thedrum.com/rss.xml",
    "Marketing Brew": "https://www.marketingbrew.com/feed",
    "MediaPost Online": "https://feeds.mediapost.com/online-media-daily",
    "MediaPost RTD": "https://feeds.mediapost.com/real-time-daily",

    # ===== 検索 =====
    "Search Engine Land": "https://searchengineland.com/feed",
    "Search Engine Journal": "https://www.searchenginejournal.com/feed/",
    "Search Engine Roundtable": "https://www.seroundtable.com/index.rdf",

    # ===== CTV =====
    "StreamTV Insider": "https://www.streamtvinsider.com/rss/xml",

    # ===== テック =====
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
    "unyoo.jp": "https://unyoo.jp/feed/",
}

MAX_PER_FEED = 5
HOURS_BACK = 30
MAX_ARTICLES_IN_MAIL = 8
MAX_TOTAL_ARTICLES = 80
MAX_FEEDBACK_ROWS = 60
JST = timezone(timedelta(hours=9))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

EXCLUDE_KEYWORDS = [
    # 人事
    "hires", "promotes", "appoints", "joins ", "names ", "steps down",
    "departs", "new ceo", "new cmo", "leadership change", "obituary",
    # イベント・宣伝
    "award", "webinar", "podcast", "sponsored", "register now",
    "join us", "upcoming event", "conference recap", "deals of the week",
    # 形式
    "opinion:", "op-ed", "guest post", "q&a with", "photos:",
    "watch:", "listen:", "on the move",
    # 国内
    "セミナー", "ウェビナー", "イベント開催", "登壇", "人事", "役員異動",
    "参加無料", "申込受付", "開催のお知らせ", "無料ウェビナー",
    "【pr】", "［pr］", "募集", "アワード", "表彰",
]


# ========== ユーティリティ ==========

def clean_text(text):
    """HTMLタグ・エンティティを除去"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_excluded(title):
    low = title.lower()
    return any(k in low for k in EXCLUDE_KEYWORDS)


def normalize_url(url):
    """UTMパラメータ等を除去して比較用に正規化"""
    try:
        p = urllib.parse.urlparse(url)
        return f"{p.netloc}{p.path}".rstrip("/").lower()
    except Exception:
        return url.lower()


# ========== RSS取得 ==========

def fetch_feed(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=25) as res:
        return feedparser.parse(res.read())


def fetch_articles():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    articles = []
    seen_urls = set()
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

                title = clean_text(entry.get("title", ""))
                link = entry.get("link", "").strip()

                if not title or not link:
                    continue
                if is_excluded(title):
                    continue

                # URL重複排除
                nurl = normalize_url(link)
                if nurl in seen_urls:
                    continue
                # タイトル重複排除
                tkey = re.sub(r"[^\w]", "", title.lower())[:40]
                if tkey in seen_titles:
                    continue

                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue

                seen_urls.add(nurl)
                seen_titles.add(tkey)
                articles.append({
                    "source": name,
                    "title": title,
                    "link": link,
                    "summary": clean_text(entry.get("summary", ""))[:400],
                })
                count += 1
            print(f"[OK] {name}: 採用{count} / 全{total}")
            ok_count += 1
        except Exception as e:
            print(f"[NG] {name}: {type(e).__name__}")

    print(f"[INFO] フィード成功 {ok_count}/{len(FEEDS)}")
    return articles


def cap_articles(articles, limit=MAX_TOTAL_ARTICLES):
    if len(articles) <= limit:
        return articles
    by_source = {}
    for a in articles:
        by_source.setdefault(a["source"], []).append(a)
    result, idx = [], 0
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
            title = clean_text(row[1])[:120]
            rating = row[2].strip().lower()
            reason = row[3].strip() if len(row) > 3 else ""
            if not title:
                continue
            if "good" in rating:
                good.append(title)
            elif "bad" in rating:
                bad.append(title + (f"（理由: {reason}）" if reason else ""))
        print(f"[OK] FB: GOOD {len(good)} / BAD {len(bad)}")
        return good, bad
    except Exception as e:
        print(f"[WARN] FB読込失敗: {type(e).__name__}")
        return [], []


def build_preference_block(good, bad):
    if not good and not bad:
        return ""
    parts = ["\n# 読者の過去の評価（最優先で反映）\n"]
    if good:
        parts.append("## 高評価だった記事")
        parts.extend(f"- {g}" for g in good[-20:])
        parts.append("")
    if bad:
        parts.append("## 低評価だった記事")
        parts.extend(f"- {b}" for b in bad[-20:])
        parts.append("")
    parts.append("上記から関心領域を推論し、低評価に類似する記事は選ばないこと。\n")
    return "\n".join(parts)


# ========== 要約（JSON形式で受け取る） ==========

def call_groq(prompt, json_mode=True):
    api_key = os.environ["GROQ_API_KEY"]
    last_error = None
    for model in GROQ_MODELS:
        for attempt in range(3):
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system",
                         "content": "あなたは日本の広告業界に精通したアナリストです。必ず日本語で、指定されたJSON形式のみを出力します。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 5000,
                }
                if json_mode:
                    payload["response_format"] = {"type": "json_object"}

                res = requests.post(
                    GROQ_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=150,
                )
                if res.status_code == 429:
                    print(f"[WARN] {model} レート制限。25秒待機")
                    time.sleep(25)
                    continue
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
        f"ID:{i}\n媒体:{a['source']}\n原題:{a['title']}\n概要:{a['summary']}"
        for i, a in enumerate(articles)
    )

    prompt = f"""以下はアドテク・デジタルマーケティング業界の最新記事一覧です。
日本の広告事業従事者にとって重要度が高いものを最大{MAX_ARTICLES_IN_MAIL}件選び、日本語で要約してください。
{preference}
# 出力形式
以下のJSON形式のみを出力してください。説明文は不要です。

{{
  "articles": [
    {{
      "id": 記事のID番号（整数）,
      "headline": "日本語の見出し（30〜45文字。具体的な企業名・数字を含める）",
      "summary": "概要を3〜4文で。何が起きたか、背景、影響を具体的に記述する。曖昧な表現を避ける",
      "insight": "日本の広告関係者にとっての示唆を1〜2文で。実務への影響を具体的に"
    }}
  ]
}}

# 選定基準（優先度順）
1. プライバシー規制・Cookie・アイデンティティ技術の動向
2. 大手プラットフォーム（Google, Meta, Amazon, TikTok）の広告仕様変更
3. CTV・リテールメディアの市場動向
4. アドテク企業のM&A・資金調達・業績
5. 広告計測・アトリビューション技術の進展
6. 生成AIの広告領域への実装事例

# 除外するもの
- セミナー・ウェビナー告知、イベント案内
- 人事異動、組織改編
- 単なる製品リリース告知
- 広告業界と関連の薄い一般テックニュース

# 記述ルール
- summary は「〜について報じられた」のような内容の薄い表現を禁止。必ず具体的な事実を書く
- insight は「AIの活用による効率化」のような一般論を禁止。実務上何が変わるかを書く
- 専門用語は原語のまま（SSP, DSP, PMP, CDP, CTV など）
- 同一の出来事を複数媒体が報じている場合は1件にまとめる

# 記事一覧
{indexed}
"""
    raw = call_groq(prompt)
    data = json.loads(raw)
    return data.get("articles", [])


# ========== HTMLメール生成 ==========

def form_urls(title):
    base = os.environ.get("FORM_BASE_URL", "")
    e_title = os.environ.get("FORM_ENTRY_TITLE", "")
    e_rating = os.environ.get("FORM_ENTRY_RATING", "")
    if not all([base, e_title, e_rating]):
        return None, None
    q = urllib.parse.quote(title[:150], safe="")
    sep = "&" if "?" in base else "?"
    return (
        f"{base}{sep}{e_title}={q}&{e_rating}=good",
        f"{base}{sep}{e_title}={q}&{e_rating}=bad",
    )


def build_html(items, articles):
    today = datetime.now(JST).strftime("%Y年%m月%d日")

    css_card = (
        "background:#ffffff;border:1px solid #e3e8ef;border-radius:10px;"
        "padding:22px;margin-bottom:18px;"
    )
    css_btn_good = (
        "display:inline-block;padding:9px 22px;background:#0a7d3f;color:#ffffff;"
        "text-decoration:none;border-radius:6px;font-size:13px;font-weight:600;"
        "margin-right:8px;"
    )
    css_btn_bad = (
        "display:inline-block;padding:9px 22px;background:#ffffff;color:#5a6472;"
        "text-decoration:none;border-radius:6px;font-size:13px;font-weight:600;"
        "border:1px solid #c9d1dc;"
    )
    css_link = (
        "display:inline-block;color:#1558d6;text-decoration:none;"
        "font-size:13px;font-weight:600;"
    )

    parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>',
        '<body style="margin:0;padding:0;background:#f0f3f8;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Hiragino Sans\',\'Yu Gothic\',sans-serif;">',
        '<div style="max-width:660px;margin:0 auto;padding:24px 16px;">',

        # ヘッダー
        '<div style="background:#12263f;border-radius:10px;padding:26px;margin-bottom:22px;">',
        '<div style="color:#ffffff;font-size:21px;font-weight:700;letter-spacing:.5px;">'
        'AdTech Daily Digest</div>',
        f'<div style="color:#9fb3cc;font-size:13px;margin-top:7px;">'
        f'{today} ／ 厳選 {len(items)}件</div>',
        '</div>',
    ]

    for n, item in enumerate(items, 1):
        idx = item.get("id")
        if not isinstance(idx, int) or not (0 <= idx < len(articles)):
            continue
        src = articles[idx]

        headline = html.escape(str(item.get("headline", src["title"])))
        summary = html.escape(str(item.get("summary", "")))
        insight = html.escape(str(item.get("insight", "")))
        link = html.escape(src["link"], quote=True)
        source = html.escape(src["source"])
        orig = html.escape(src["title"][:110])

        good_url, bad_url = form_urls(src["title"])

        parts.append(f'<div style="{css_card}">')

        # 媒体バッジ
        parts.append(
            '<div style="margin-bottom:11px;">'
            f'<span style="display:inline-block;background:#eef3fb;color:#3d5a80;'
            f'font-size:11px;font-weight:700;padding:4px 11px;border-radius:4px;">'
            f'{source}</span>'
            f'<span style="color:#a8b3c1;font-size:12px;margin-left:9px;">#{n}</span>'
            '</div>'
        )

        # 見出し
        parts.append(
            f'<div style="font-size:17px;font-weight:700;color:#12263f;'
            f'line-height:1.55;margin-bottom:13px;">{headline}</div>'
        )

        # 概要
        parts.append(
            f'<div style="font-size:14px;color:#3c4757;line-height:1.85;'
            f'margin-bottom:14px;">{summary}</div>'
        )

        # 示唆
        if insight:
            parts.append(
                '<div style="background:#f7f9fc;border-left:3px solid #4a7fd4;'
                'padding:12px 15px;margin-bottom:15px;border-radius:0 5px 5px 0;">'
                '<div style="font-size:11px;color:#4a7fd4;font-weight:700;'
                'margin-bottom:5px;letter-spacing:.6px;">POINT</div>'
                f'<div style="font-size:13px;color:#3c4757;line-height:1.75;">{insight}</div>'
                '</div>'
            )

        # 原題 + 元記事リンク
        parts.append(
            f'<div style="font-size:11px;color:#9aa5b4;margin-bottom:9px;'
            f'line-height:1.5;">原題: {orig}</div>'
            f'<div style="margin-bottom:16px;">'
            f'<a href="{link}" style="{css_link}">元記事を読む →</a></div>'
        )

        # 評価ボタン
        if good_url and bad_url:
            parts.append(
                '<div style="border-top:1px solid #eef1f6;padding-top:15px;">'
                '<div style="font-size:11px;color:#9aa5b4;margin-bottom:9px;">'
                'この記事の評価</div>'
                f'<a href="{good_url}" style="{css_btn_good}">&#128077; 参考になった</a>'
                f'<a href="{bad_url}" style="{css_btn_bad}">&#128078; 不要</a>'
                '</div>'
            )

        parts.append('</div>')

    parts.append(
        '<div style="text-align:center;padding:22px 12px;color:#94a1b2;font-size:11px;'
        'line-height:1.8;">'
        '評価いただいた内容は翌日以降の記事選定に反映されます。<br>'
        'このメールは GitHub Actions により自動配信されています。'
        '</div>'
    )
    parts.append('</div></body></html>')
    return "".join(parts)


def build_text(items, articles):
    lines = [f"AdTech Daily Digest {datetime.now(JST).strftime('%Y/%m/%d')}", ""]
    for n, item in enumerate(items, 1):
        idx = item.get("id")
        if not isinstance(idx, int) or not (0 <= idx < len(articles)):
            continue
        src = articles[idx]
        lines.append(f"[{n}] {item.get('headline', src['title'])}")
        lines.append(f"媒体: {src['source']}")
        lines.append(f"{item.get('summary', '')}")
        if item.get("insight"):
            lines.append(f"POINT: {item['insight']}")
        lines.append(f"{src['link']}")
        lines.append("")
    lines.append("※HTMLメール対応の環境でご覧いただくと評価ボタンが表示されます。")
    return "\n".join(lines)


def build_fallback_html(articles):
    parts = [
        '<html><body style="font-family:sans-serif;padding:20px;background:#f5f7fa;">',
        '<h2 style="color:#12263f;">AdTech Daily Digest</h2>',
        '<p style="color:#666;">AI要約に失敗したため記事一覧のみお送りします。</p>',
        '<ul style="line-height:2;">',
    ]
    for a in articles[:20]:
        parts.append(
            f'<li><span style="color:#888;font-size:12px;">[{html.escape(a["source"])}]</span> '
            f'<a href="{html.escape(a["link"], quote=True)}" style="color:#1558d6;">'
            f'{html.escape(a["title"])}</a></li>'
        )
    parts.append('</ul></body></html>')
    return "".join(parts)


# ========== メール送信 ==========

def send_mail(html_body, text_body):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]
    mail_to = os.environ["MAIL_TO"]

    today = datetime.now(JST).strftime("%m/%d")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(f"AdTech Daily Digest {today}", "utf-8")
    msg["From"] = formataddr((str(Header("AdTech Digest", "utf-8")), gmail_user))
    msg["To"] = mail_to

    msg.attach(MIMEText(text_body, "plain", "
