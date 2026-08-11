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
    "AdExchanger": "https://www.adexchanger.com/feed/",
    "Digiday": "https://digiday.com/feed/",
    "AdMonsters": "https://www.admonsters.com/feed/",
    "ExchangeWire": "https://www.exchangewire.com/feed/",
    "MarTech": "https://martech.org/feed/",
    "Marketing Dive": "https://www.marketingdive.com/feeds/news/",
    "Mobile Dev Memo": "https://mobiledevmemo.com/feed/",
    "The Drum": "https://www.thedrum.com/rss.xml",
    "Marketing Brew": "https://www.marketingbrew.com/feed",
    "MediaPost Online": "https://feeds.mediapost.com/online-media-daily",
    "MediaPost RTD": "https://feeds.mediapost.com/real-time-daily",
    "Search Engine Land": "https://searchengineland.com/feed",
    "Search Engine Journal": "https://www.searchenginejournal.com/feed/",
    "Search Engine Roundtable": "https://www.seroundtable.com/index.rdf",
    "StreamTV Insider": "https://www.streamtvinsider.com/rss/xml",
    "TechCrunch": "https://techcrunch.com/feed/",
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "ExchangeWire JP": "https://www.exchangewire.jp/feed/",
    "MarkeZine": "https://markezine.jp/rss/new/20/index.xml",
    "Web担当者Forum": "https://webtan.impress.co.jp/rss.xml",
    "AdverTimes": "https://www.advertimes.com/feed/",
    "DIGIDAY JP": "https://digiday.jp/feed/",
    "Media Innovation": "https://media-innovation.jp/feed/",
    "ITmedia マーケティング": "https://rss.itmedia.co.jp/rss/2.0/marketing.xml",
    "unyoo.jp": "https://unyoo.jp/feed/",
}

MAX_PER_FEED = 4
HOURS_BACK = 30
MAX_ARTICLES_IN_MAIL = 8
MAX_TOTAL_ARTICLES = 55
MAX_FEEDBACK_ROWS = 200
HISTORY_FILE = "sent_history.json"
HISTORY_DAYS = 14
EXTENDED_HOURS = 96
MIN_ARTICLES = 3
JST = timezone(timedelta(hours=9))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

EXCLUDE_KEYWORDS = [
    "hires", "promotes", "appoints", "joins ", "names ", "steps down",
    "departs", "new ceo", "new cmo", "leadership change", "obituary",
    "award", "webinar", "podcast", "sponsored", "register now",
    "join us", "upcoming event", "conference recap", "deals of the week",
    "opinion:", "op-ed", "guest post", "q&a with", "photos:",
    "watch:", "listen:", "on the move",
    "セミナー", "ウェビナー", "イベント開催", "登壇", "人事", "役員異動",
    "参加無料", "申込受付", "開催のお知らせ", "無料ウェビナー",
    "【pr】", "募集", "アワード", "表彰",
]


def clean_text(text):
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
    try:
        p = urllib.parse.urlparse(url)
        return (p.netloc + p.path).rstrip("/").lower()
    except Exception:
        return url.lower()


def load_members():
    def load_history():
    """配信済み履歴を読み込み、古いものを削除"""
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("[INFO] 履歴ファイルなし。新規作成します")
        return {}
    except Exception as e:
        print("[WARN] 履歴読込失敗: " + str(e))
        return {}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    cleaned = {}
    for person, records in data.items():
        if not isinstance(records, dict):
            continue
        kept = {}
        for url_key, date_str in records.items():
            if str(date_str) >= cutoff:
                kept[url_key] = date_str
        cleaned[person] = kept
        print("[OK] 履歴 " + person + ": " + str(len(kept)) + "件")

    return cleaned


def save_history(history):
    """履歴をファイルに保存"""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=1, sort_keys=True)
        total = sum(len(v) for v in history.values())
        print("[OK] 履歴保存: 全" + str(total) + "件")
        return True
    except Exception as e:
        print("[ERROR] 履歴保存失敗: " + str(e))
        return False


def filter_unsent(articles, sent_map):
    """未配信の記事のみ抽出"""
    if not sent_map:
        return articles
    result = []
    for a in articles:
        key = normalize_url(a["link"])
        if key not in sent_map:
            result.append(a)
    removed = len(articles) - len(result)
    if removed > 0:
        print("[INFO] 配信済み " + str(removed) + "件を除外 -> 残り" + str(len(result)) + "件")
    return result


def record_sent(history, person, articles, items):
    """配信した記事を履歴に追加"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    if person not in history:
        history[person] = {}
    count = 0
    for item in items:
        idx = item.get("id")
        if not isinstance(idx, int) or idx < 0 or idx >= len(articles):
            continue
        key = normalize_url(articles[idx]["link"])
        history[person][key] = today
        count += 1
    return count
    
    try:
        with open("members.json", encoding="utf-8") as f:
            data = json.load(f)
        members = data.get("members", [])
        valid = [m for m in members if m.get("name") and m.get("email")]
        print("[OK] メンバー読込: " + str(len(valid)) + "名")
        return valid
    except FileNotFoundError:
        print("[WARN] members.json なし。単一配信モードで実行")
        to = os.environ.get("MAIL_TO", "")
        if to:
            return [{"name": "", "email": to, "focus": ""}]
        return []
    except Exception as e:
        print("[ERROR] members.json 読込失敗: " + str(e))
        return []


def fetch_feed(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=25) as res:
        return feedparser.parse(res.read())


def fetch_articles(hours_back=None):
    if hours_back is None:
        hours_back = HOURS_BACK
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
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

                nurl = normalize_url(link)
                if nurl in seen_urls:
                    continue

                tkey = re.sub(r"[^\w]", "", title.lower())[:40]
                if tkey and tkey in seen_titles:
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
            print("[OK] " + name + ": 採用" + str(count) + " / 全" + str(total))
            ok_count += 1
        except Exception as e:
            print("[NG] " + name + ": " + type(e).__name__)

    print("[INFO] フィード成功 " + str(ok_count) + "/" + str(len(FEEDS)))
    return articles


def cap_articles(articles, limit=MAX_TOTAL_ARTICLES):
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
    print("[INFO] " + str(len(articles)) + "件 -> " + str(len(result)) + "件に絞込")
    return result

def load_all_feedback():
    url = os.environ.get("FEEDBACK_CSV_URL")
    result = {}
    if not url:
        print("[INFO] FEEDBACK_CSV_URL 未設定")
        return result
    try:
        res = requests.get(url, timeout=30)
        res.raise_for_status()
        res.encoding = "utf-8"
        rows = list(csv.reader(io.StringIO(res.text)))
        if len(rows) < 2:
            return result

        for row in rows[1:][-MAX_FEEDBACK_ROWS:]:
            if len(row) < 4:
                continue
            person = row[1].strip()
            title = clean_text(row[2])[:120]
            rating = row[3].strip().lower()
            reason = row[4].strip() if len(row) > 4 else ""

            if not title:
                continue

            if person not in result:
                result[person] = {"good": [], "bad": []}

            if "good" in rating:
                result[person]["good"].append(title)
            elif "bad" in rating:
                if reason:
                    result[person]["bad"].append(title + "（理由: " + reason + "）")
                else:
                    result[person]["bad"].append(title)

        for k, v in result.items():
            print("[OK] FB " + k + ": GOOD " + str(len(v["good"]))
                  + " / BAD " + str(len(v["bad"])))
        return result
    except Exception as e:
        print("[WARN] FB読込失敗: " + type(e).__name__ + " " + str(e))
        return result


def build_team_feedback(all_fb, exclude_name):
    good = []
    bad = []
    for person, fb in all_fb.items():
        if person == exclude_name:
            continue
        for g in fb.get("good", []):
            good.append(g + "［" + person + "］")
        for b in fb.get("bad", []):
            bad.append(b + "［" + person + "］")
    return {"good": good, "bad": bad}


def build_preference_block(member, personal_fb, team_fb):
    parts = []

    focus = member.get("focus", "").strip()
    if focus:
        parts.append("")
        parts.append("# この読者の関心領域（基本方針）")
        parts.append(focus)
        parts.append("")

    p_good = personal_fb.get("good", [])
    p_bad = personal_fb.get("bad", [])

    if p_good or p_bad:
        parts.append("# 本人の評価履歴（最優先で反映すること）")
        parts.append("")
        if p_good:
            parts.append("## 本人が高く評価した記事")
            for g in p_good[-15:]:
                parts.append("- " + g)
            parts.append("")
        if p_bad:
            parts.append("## 本人が不要と評価した記事")
            for b in p_bad[-15:]:
                parts.append("- " + b)
            parts.append("")

    t_good = team_fb.get("good", [])
    t_bad = team_fb.get("bad", [])

    if t_good or t_bad:
        parts.append("# チーム全体の傾向（参考情報）")
        parts.append("同じチームの同僚が評価した記事です。")
        parts.append("本人の評価履歴と矛盾する場合は、必ず本人の評価を優先してください。")
        parts.append("")
        if t_good:
            parts.append("## チームで評価が高かった記事")
            for g in t_good[-12:]:
                parts.append("- " + g)
            parts.append("")
        if t_bad:
            parts.append("## チームで不要とされた記事")
            for b in t_bad[-12:]:
                parts.append("- " + b)
            parts.append("")

    if p_good or p_bad or t_good or t_bad:
        parts.append("# 評価の活用方針")
        parts.append("上記の評価履歴から読者の関心領域を推論してください。")
        parts.append("不要と評価された記事に類似する内容は選定から除外してください。")
        parts.append("本人の評価が少ない場合は、チーム全体の傾向を補助的に参考にしてください。")
        parts.append("")

    return "\n".join(parts)


def call_groq(prompt, max_tokens=5000):
    api_key = os.environ["GROQ_API_KEY"]
    last_error = None

    for model in GROQ_MODELS:
        for attempt in range(4):
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "あなたは日本の広告業界に精通したアナリストです。必ず日本語で、指定されたJSON形式のみを出力します。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                }
                res = requests.post(
                    GROQ_URL,
                    headers={
                        "Authorization": "Bearer " + api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=180,
                )

                if res.status_code == 429:
                    wait = 30
                    try:
                        info = res.json()
                        msg = str(info.get("error", {}).get("message", ""))
                        m = re.search(r"try again in ([\d.]+)s", msg)
                        if m:
                            wait = int(float(m.group(1))) + 5
                    except Exception:
                        pass
                    wait = min(wait, 90)
                    print("[WARN] " + model + " 429。" + str(wait) + "秒待機")
                    time.sleep(wait)
                    continue

                if res.status_code == 413:
                    print("[WARN] " + model + " 入力過大。次モデルへ")
                    break

                res.raise_for_status()
                content = res.json()["choices"][0]["message"]["content"]
                print("[OK] LLM応答取得: " + model)
                return content

            except requests.exceptions.Timeout:
                last_error = "Timeout"
                print("[WARN] " + model + " タイムアウト 試行" + str(attempt + 1))
                time.sleep(10)
            except Exception as e:
                last_error = e
                print("[WARN] " + model + " 試行" + str(attempt + 1)
                      + ": " + type(e).__name__ + " " + str(e)[:150])
                time.sleep(8)

    raise RuntimeError("全モデル失敗: " + str(last_error))


def parse_json_safely(raw):
    """LLMの出力からJSONを抽出"""
    if not raw:
        raise ValueError("空の応答")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError("JSON解析失敗: " + raw[:200])


def select_and_summarize(articles, preference):
    blocks = []
    for i, a in enumerate(articles):
        blocks.append(
            "ID:" + str(i) + "\n媒体:" + a["source"] + "\n原題:" + a["title"]
            + "\n概要:" + a["summary"][:300]
        )
    indexed = "\n\n".join(blocks)

    prompt = (
        "以下はアドテク・デジタルマーケティング業界の最新記事一覧です。\n"
        "日本の広告事業従事者にとって重要度が高いものを最大"
        + str(MAX_ARTICLES_IN_MAIL) + "件選び、日本語で要約してください。\n"
        + preference
        + """
# 出力形式
以下のJSON形式のみを出力してください。前置きや説明文は不要です。

{
  "articles": [
    {
      "id": 記事のID番号（整数）,
      "headline": "日本語の見出し。30〜45文字。具体的な企業名や数字を含める",
      "summary": "概要を3〜4文で。何が起きたか、背景、影響を具体的に記述する",
      "insight": "日本の広告関係者にとっての示唆を1〜2文。実務への影響を具体的に"
    }
  ]
}

# 選定基準（優先度順）
1. プライバシー規制、Cookie、アイデンティティ技術の動向
2. Google、Meta、Amazon、TikTok など大手プラットフォームの広告仕様変更
3. CTV、リテールメディアの市場動向
4. アドテク企業のM&A、資金調達、業績
5. 広告計測、アトリビューション技術の進展
6. 生成AIの広告領域への実装事例

# 除外するもの
- セミナー、ウェビナー告知、イベント案内
- 人事異動、組織改編
- 単なる製品リリース告知
- 個別ブランドのキャンペーン事例
- 広告業界と関連の薄い一般テックニュース

# 記述ルール
- summary は「〜について報じられた」のような内容の薄い表現を禁止
- insight は「AIの活用による効率化」のような一般論を禁止
- 専門用語は原語のまま（SSP, DSP, PMP, CDP, CTV など）
- 同一の出来事を複数媒体が報じている場合は1件にまとめる

# 記事一覧
"""
        + indexed
    )

    raw = call_groq(prompt)
    data = parse_json_safely(raw)

    items = data.get("articles", [])
    if not isinstance(items, list):
        raise ValueError("articles が配列ではありません")

    valid = []
    for it in items:
        if not isinstance(it, dict):
            continue
        idx = it.get("id")
        if isinstance(idx, str) and idx.isdigit():
            idx = int(idx)
            it["id"] = idx
        if isinstance(idx, int) and 0 <= idx < len(articles):
            valid.append(it)

    return valid


def form_urls(title, person_name):
    base = os.environ.get("FORM_BASE_URL", "")
    e_name = os.environ.get("FORM_ENTRY_NAME", "")
    e_title = os.environ.get("FORM_ENTRY_TITLE", "")
    e_rating = os.environ.get("FORM_ENTRY_RATING", "")

    if not base or not e_title or not e_rating:
        return None, None

    q_title = urllib.parse.quote(title[:150], safe="")
    sep = "&" if "?" in base else "?"

    name_part = ""
    if e_name and person_name:
        name_part = "&" + e_name + "=" + urllib.parse.quote(person_name, safe="")

    good = base + sep + e_title + "=" + q_title + "&" + e_rating + "=good" + name_part
    bad = base + sep + e_title + "=" + q_title + "&" + e_rating + "=bad" + name_part
    return good, bad

def build_html(items, articles, member, note=""):
    today = datetime.now(JST).strftime("%Y年%m月%d日")
    person = member.get("name", "")

    css_card = "background:#ffffff;border:1px solid #e3e8ef;border-radius:10px;padding:22px;margin-bottom:18px;"
    css_btn_good = "display:inline-block;padding:9px 22px;background:#0a7d3f;color:#ffffff;text-decoration:none;border-radius:6px;font-size:13px;font-weight:600;margin-right:8px;"
    css_btn_bad = "display:inline-block;padding:9px 22px;background:#ffffff;color:#5a6472;text-decoration:none;border-radius:6px;font-size:13px;font-weight:600;border:1px solid #c9d1dc;"
    css_link = "display:inline-block;color:#1558d6;text-decoration:none;font-size:13px;font-weight:600;"

    p = []
    p.append('<!DOCTYPE html><html><head><meta charset="utf-8">')
    p.append('<meta name="viewport" content="width=device-width,initial-scale=1"></head>')
    p.append('<body style="margin:0;padding:0;background:#f0f3f8;font-family:sans-serif;">')
    p.append('<div style="max-width:660px;margin:0 auto;padding:24px 16px;">')

    p.append('<div style="background:#12263f;border-radius:10px;padding:26px;margin-bottom:22px;">')
    p.append('<div style="color:#ffffff;font-size:21px;font-weight:700;">AdTech Daily Digest</div>')
    sub = today + ' ／ 厳選 ' + str(len(items)) + '件'
    if person:
        sub = html.escape(person) + ' さん向け ／ ' + sub
    p.append('<div style="color:#9fb3cc;font-size:13px;margin-top:7px;">' + sub + '</div>')
    p.append('</div>')

    valid = 0
    for item in items:
        idx = item.get("id")
        if not isinstance(idx, int) or idx < 0 or idx >= len(articles):
            continue
        valid += 1
        src = articles[idx]

        headline = html.escape(str(item.get("headline") or src["title"]))
        summary = html.escape(str(item.get("summary") or ""))
        insight = html.escape(str(item.get("insight") or ""))
        link = html.escape(src["link"], quote=True)
        source = html.escape(src["source"])
        orig = html.escape(src["title"][:110])

        good_url, bad_url = form_urls(src["title"], person)

        p.append('<div style="' + css_card + '">')

        p.append('<div style="margin-bottom:11px;">')
        p.append('<span style="display:inline-block;background:#eef3fb;color:#3d5a80;'
                 'font-size:11px;font-weight:700;padding:4px 11px;border-radius:4px;">'
                 + source + '</span>')
        p.append('<span style="color:#a8b3c1;font-size:12px;margin-left:9px;">#'
                 + str(valid) + '</span>')
        p.append('</div>')

        p.append('<div style="font-size:17px;font-weight:700;color:#12263f;'
                 'line-height:1.55;margin-bottom:13px;">' + headline + '</div>')

        p.append('<div style="font-size:14px;color:#3c4757;line-height:1.85;'
                 'margin-bottom:14px;">' + summary + '</div>')

        if insight:
            p.append('<div style="background:#f7f9fc;border-left:3px solid #4a7fd4;'
                     'padding:12px 15px;margin-bottom:15px;border-radius:0 5px 5px 0;">')
            p.append('<div style="font-size:11px;color:#4a7fd4;font-weight:700;'
                     'margin-bottom:5px;">POINT</div>')
            p.append('<div style="font-size:13px;color:#3c4757;line-height:1.75;">'
                     + insight + '</div>')
            p.append('</div>')

        p.append('<div style="font-size:11px;color:#9aa5b4;margin-bottom:9px;'
                 'line-height:1.5;">原題: ' + orig + '</div>')
        p.append('<div style="margin-bottom:16px;"><a href="' + link + '" style="'
                 + css_link + '">元記事を読む &rarr;</a></div>')

        if good_url and bad_url:
            p.append('<div style="border-top:1px solid #eef1f6;padding-top:15px;">')
            p.append('<div style="font-size:11px;color:#9aa5b4;margin-bottom:9px;">'
                     'この記事の評価</div>')
            p.append('<a href="' + html.escape(good_url, quote=True) + '" style="'
                     + css_btn_good + '">&#128077; 参考になった</a>')
            p.append('<a href="' + html.escape(bad_url, quote=True) + '" style="'
                     + css_btn_bad + '">&#128078; 不要</a>')
            p.append('</div>')

        p.append('</div>')

    p.append('<div style="text-align:center;padding:22px 12px;color:#94a1b2;'
             'font-size:11px;line-height:1.8;">')
    p.append('評価いただいた内容は翌日以降のあなた向け記事選定に反映されます。<br>')
    p.append('このメールは GitHub Actions により自動配信されています。')
    p.append('</div>')
    p.append('</div></body></html>')
    return "".join(p)


def build_text(items, articles, member, note=""):
    lines = []
    person = member.get("name", "")
    head = "AdTech Daily Digest " + datetime.now(JST).strftime("%Y/%m/%d")
    if person:
        head = person + " さん向け " + head
    lines.append(head)
    lines.append("")

    if note:
        lines.append("※ " + note)
        lines.append("")

    n = 0
    for item in items:
        idx = item.get("id")
        if not isinstance(idx, int) or idx < 0 or idx >= len(articles):
            continue
        n += 1
        src = articles[idx]
        lines.append("[" + str(n) + "] " + str(item.get("headline") or src["title"]))
        lines.append("媒体: " + src["source"])
        lines.append(str(item.get("summary") or ""))
        if item.get("insight"):
            lines.append("POINT: " + str(item["insight"]))
        lines.append(src["link"])
        lines.append("")

    lines.append("※HTMLメール対応の環境でご覧いただくと評価ボタンが表示されます。")
    return "\n".join(lines)


def build_fallback_html(articles):
    p = []
    p.append('<html><body style="font-family:sans-serif;padding:20px;background:#f5f7fa;">')
    p.append('<h2 style="color:#12263f;">AdTech Daily Digest</h2>')
    p.append('<p style="color:#666;">AI要約に失敗したため記事一覧のみお送りします。</p>')
    p.append('<ul style="line-height:2;">')
    for a in articles[:20]:
        p.append('<li><span style="color:#888;font-size:12px;">['
                 + html.escape(a["source"]) + ']</span> <a href="'
                 + html.escape(a["link"], quote=True)
                 + '" style="color:#1558d6;">' + html.escape(a["title"]) + '</a></li>')
    p.append('</ul></body></html>')
    return "".join(p)


def build_fallback_text(articles):
    lines = ["AI要約に失敗したため記事一覧のみお送りします。", ""]
    for i, a in enumerate(articles[:20], 1):
        lines.append(str(i) + ". [" + a["source"] + "] " + a["title"])
        lines.append("   " + a["link"])
        lines.append("")
    return "\n".join(lines)

def build_empty_html(member, reason=""):
    today = datetime.now(JST).strftime("%Y.%m.%d")
    weekday = ["月", "火", "水", "木", "金", "土", "日"][datetime.now(JST).weekday()]
    person = member.get("name", "")

    p = []
    p.append('<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">')
    p.append('<meta name="viewport" content="width=device-width,initial-scale=1"></head>')
    p.append('<body style="margin:0;padding:0;background-color:#eef1f6;">')
    p.append('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
             'style="background-color:#eef1f6;padding:32px 12px;">')
    p.append('<tr><td align="center">')
    p.append('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
             'style="max-width:620px;font-family:-apple-system,BlinkMacSystemFont,'
             '\'Segoe UI\',\'Hiragino Sans\',sans-serif;">')

    p.append('<tr><td style="background:linear-gradient(135deg,#1a2f4b 0%,#2d5a8c 100%);'
             'border-radius:16px;padding:36px 32px;">')
    p.append('<div style="color:#7dd3fc;font-size:11px;font-weight:700;'
             'letter-spacing:2.5px;text-transform:uppercase;margin-bottom:10px;">'
             'AdTech Intelligence</div>')
    p.append('<div style="color:#ffffff;font-size:27px;font-weight:800;'
             'letter-spacing:-0.5px;">Daily Digest</div>')
    p.append('<div style="height:1px;background:rgba(255,255,255,0.15);'
             'margin:18px 0 14px 0;"></div>')
    line = today + ' (' + weekday + ')'
    if person:
        line = html.escape(person) + '　|　' + line
    p.append('<div style="color:#a8c5e0;font-size:12px;">' + line + '</div>')
    p.append('</td></tr>')

    p.append('<tr><td style="height:20px;"></td></tr>')
    if note:
        p.append('<tr><td style="background-color:#fef9e7;border-radius:10px;'
                 'padding:14px 20px;margin-bottom:16px;">')
        p.append('<div style="font-size:12px;color:#92702a;line-height:1.7;">'
                 '&#9432; ' + html.escape(note) + '</div>')
        p.append('</td></tr>')
        p.append('<tr><td style="height:16px;"></td></tr>')
    p.append('<tr><td style="background-color:#ffffff;border-radius:14px;'
             'padding:44px 32px;text-align:center;">')
    p.append('<div style="font-size:38px;margin-bottom:18px;">&#127749;</div>')
    p.append('<div style="font-size:18px;font-weight:700;color:#101f38;'
             'margin-bottom:12px;">本日の新着記事はありません</div>')
    p.append('<div style="font-size:13px;color:#6b7a90;line-height:1.9;">')
    if reason:
        p.append(html.escape(reason) + '<br>')
    p.append('明日また最新情報をお届けします。')
    p.append('</div>')
    p.append('</td></tr>')

    p.append('<tr><td style="padding:26px 28px 12px 28px;text-align:center;">')
    p.append('<div style="height:1px;background-color:#dde3ec;margin-bottom:18px;"></div>')
    p.append('<div style="color:#8b98ab;font-size:11px;line-height:1.9;">')
    p.append('<span style="color:#a8b3c4;">Automated by GitHub Actions</span>')
    p.append('</div>')
    p.append('</td></tr>')

    p.append('</table></td></tr></table></body></html>')
    return "".join(p)


def build_empty_text(member, reason=""):
    today = datetime.now(JST).strftime("%Y/%m/%d")
    person = member.get("name", "")
    lines = []
    head = "AdTech Daily Digest " + today
    if person:
        head = person + " さん向け " + head
    lines.append(head)
    lines.append("")
    lines.append("本日の新着記事はありません。")
    if reason:
        lines.append(reason)
    lines.append("")
    lines.append("明日また最新情報をお届けします。")
    return "\n".join(lines)


def send_mail(to_email, html_body, text_body, person=""):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]

    today = datetime.now(JST).strftime("%m/%d")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header("AdTech Daily Digest " + today, "utf-8")
    msg["From"] = formataddr((str(Header("AdTech Digest", "utf-8")), gmail_user))
    msg["To"] = to_email

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(gmail_user, gmail_pass)
        server.send_message(msg)

    label = person if person else to_email
    print("[OK] 送信完了: " + label)


def main():
    members = load_members()
    if not members:
        print("[ERROR] 配信先が設定されていません")
        return

    history = load_history()

    arts_normal = fetch_articles(HOURS_BACK)
    print("通常範囲(" + str(HOURS_BACK) + "h)の取得: " + str(len(arts_normal)) + "件")

    arts_extended = None

    all_fb = load_all_feedback()

    total_good = 0
    total_bad = 0
    for v in all_fb.values():
        total_good += len(v.get("good", []))
        total_bad += len(v.get("bad", []))
    print("[INFO] チーム全体の評価: GOOD " + str(total_good)
          + " / BAD " + str(total_bad))

    success = 0
    empty_sent = 0
    history_changed = False

    for i, member in enumerate(members):
        name = member.get("name", "")
        label = name if name else member["email"]
        print("")
        print("===== 処理中: " + label + " =====")

        sent_map = history.get(name, {})
        arts = filter_unsent(arts_normal, sent_map)
        note = ""

        # 段階1: 通常範囲で不足なら範囲拡大
        if len(arts) < MIN_ARTICLES:
            print("[INFO] 未配信" + str(len(arts)) + "件。範囲を"
                  + str(EXTENDED_HOURS) + "hに拡大して再取得します")

            if arts_extended is None:
                arts_extended = fetch_articles(EXTENDED_HOURS)
                print("[INFO] 拡大範囲の取得: " + str(len(arts_extended)) + "件")

            arts = filter_unsent(arts_extended, sent_map)
            if arts:
                note = "直近" + str(EXTENDED_HOURS) + "時間まで範囲を広げて収集しました。"

        # 段階2: それでも0件なら空メール
        if not arts:
            print("[INFO] " + label + ": 未配信記事なし。空メールを送信します")
            try:
                send_mail(
                    member["email"],
                    build_empty_html(member, "収集対象の媒体に未読の新着記事がありませんでした。"),
                    build_empty_text(member, "収集対象の媒体に未読の新着記事がありませんでした。"),
                    name,
                )
                empty_sent += 1
            except Exception as e:
                print("[ERROR] 送信失敗: " + str(e))
            if i < len(members) - 1:
                time.sleep(5)
            continue

        arts = cap_articles(arts)

        personal_fb = all_fb.get(name, {"good": [], "bad": []})
        team_fb = build_team_feedback(all_fb, name)

        print("[INFO] 本人 GOOD " + str(len(personal_fb.get("good", [])))
              + " / BAD " + str(len(personal_fb.get("bad", [])))
              + "　チーム GOOD " + str(len(team_fb["good"]))
              + " / BAD " + str(len(team_fb["bad"])))

        pref = build_preference_block(member, personal_fb, team_fb)

        items = []
        try:
            items = select_and_summarize(arts, pref)
            print("[OK] 選定記事数: " + str(len(items)))

            if not items:
                raise RuntimeError("選定結果が空です")

            html_body = build_html(items, arts, member, note)
            text_body = build_text(items, arts, member, note)

        except Exception as e:
            print("[ERROR] 要約失敗: " + str(e))
            html_body = build_fallback_html(arts)
            text_body = build_fallback_text(arts)
            items = []

        try:
            send_mail(member["email"], html_body, text_body, name)
            success += 1
            if items:
                n = record_sent(history, name, arts, items)
                history_changed = True
                print("[OK] 履歴に" + str(n) + "件を記録")
        except Exception as e:
            print("[ERROR] 送信失敗 " + member["email"] + ": " + str(e))

        if i < len(members) - 1:
            print("[INFO] 次のメンバーまで35秒待機")
            time.sleep(35)

    if history_changed:
        save_history(history)

    print("")
    print("[DONE] 配信 " + str(success) + "件 / 空通知 " + str(empty_sent)
          + "件 / 全" + str(len(members)) + "名")


if __name__ == "__main__":
    main()
