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
    "Marketing Dive": "https://www.marketingdive.com/feeds/news/",
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
    "ITmedia マーケティング": "https://rss.itmedia.co.jp/rss/2.0/marketing.xml",
    "Marketing Land": "https://martech.org/feed/",
    "Adweek": "https://www.adweek.com/feed/",
    "Campaign Asia": "https://www.campaignasia.com/rss/",
    "Netインフォメーション": "https://internet.watch.impress.co.jp/data/rss/1.0/iw/feed.rdf",
}

MAX_PER_FEED = 4
HOURS_BACK = 30
MAX_ARTICLES_IN_MAIL = 8
MAX_TOTAL_ARTICLES = 50
MAX_FEEDBACK_ROWS = 200
HISTORY_FILE = "sent_history.json"
HISTORY_DAYS = 14
EXTENDED_HOURS = 96
MIN_ARTICLES = 3
SUMMARIZE_COUNT = 14
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
        total = 0
        for v in history.values():
            total += len(v)
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


def load_members():
    try:
        with open("members.json", encoding="utf-8") as f:
            data = json.load(f)
        members = data.get("members", [])
        valid = []
        for m in members:
            if m.get("name") and m.get("email"):
                valid.append(m)
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
                    "summary": clean_text(entry.get("summary", ""))[:250],
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

    known_names = set()
    try:
        with open("members.json", encoding="utf-8") as f:
            for m in json.load(f).get("members", []):
                if m.get("name"):
                    known_names.add(m["name"].strip())
    except Exception:
        pass

    try:
        res = requests.get(url, timeout=30)
        res.raise_for_status()
        res.encoding = "utf-8"
        rows = list(csv.reader(io.StringIO(res.text)))
        if len(rows) < 2:
            print("[INFO] フィードバック未登録")
            return result

        header = [h.strip() for h in rows[0]]
        print("[INFO] CSV列: " + str(header))

        col_name = -1
        col_title = -1
        col_rating = -1
        col_reason = -1

        for i, h in enumerate(header):
            low = h.lower()
            if col_name < 0 and ("氏名" in h or "名前" in h or "name" in low):
                col_name = i
            elif col_title < 0 and ("タイトル" in h or "記事" in h or "title" in low):
                col_title = i
            elif col_rating < 0 and ("評価" in h or "rating" in low):
                col_rating = i
            elif col_reason < 0 and ("理由" in h or "reason" in low):
                col_reason = i

        if col_rating < 0:
            for i, row in enumerate(rows[1:6]):
                for j, cell in enumerate(row):
                    if cell.strip().lower() in ("good", "bad"):
                        col_rating = j
                        break
                if col_rating >= 0:
                    break

        if col_name < 0 and known_names:
            for row in rows[1:6]:
                for j, cell in enumerate(row):
                    if cell.strip() in known_names:
                        col_name = j
                        break
                if col_name >= 0:
                    break

        print("[INFO] 列判定: 氏名=" + str(col_name) + " タイトル=" + str(col_title)
              + " 評価=" + str(col_rating) + " 理由=" + str(col_reason))

        if col_rating < 0 or col_title < 0:
            print("[WARN] 必要な列を特定できません")
            return result

        for row in rows[1:][-MAX_FEEDBACK_ROWS:]:
            if len(row) <= max(col_title, col_rating):
                continue

            person = ""
            if col_name >= 0 and len(row) > col_name:
                person = row[col_name].strip()
            if not person:
                person = "共通"

            title = clean_text(row[col_title])[:120]
            rating = row[col_rating].strip().lower()
            reason = ""
            if col_reason >= 0 and len(row) > col_reason:
                reason = row[col_reason].strip()

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
                    wait = max(wait, 40)
                    wait = min(wait, 120)
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


TAG_LIST = [
    "privacy",
    "platform",
    "ctv",
    "retail",
    "ma",
    "measurement",
    "ai",
    "agency",
    "japan",
    "search",
]


def summarize_common(articles):
    """全員共通の要約を1回だけ生成"""
    blocks = []
    for i, a in enumerate(articles):
        blocks.append(
            "ID:" + str(i) + " [" + a["source"] + "] " + a["title"]
            + "\n" + a["summary"][:200]
        )
    indexed = "\n\n".join(blocks)

    prompt = (
        "以下はアドテク・デジタルマーケティング業界の最新記事一覧です。\n"
        "日本の広告事業従事者にとって重要度が高いものを"
        + str(SUMMARIZE_COUNT) + "件選び、日本語で要約してください。\n"
        + """
# 出力形式
以下のJSON形式のみを出力してください。前置きや説明は不要です。

{
  "articles": [
    {
      "id": 記事のID番号（整数）,
      "headline": "日本語の見出し。30〜45文字",
      "summary": "概要を3〜4文で具体的に記述",
      "insight": "日本の広告関係者への示唆を1〜2文",
      "tags": ["該当するタグを1〜3個"],
      "importance": 重要度を1〜10の整数で
    }
  ]
}

# 使用可能なタグ
privacy     : プライバシー規制、Cookie、同意管理、アイデンティティ
platform    : Google/Meta/Amazon/TikTok等の広告仕様変更
ctv         : CTV、ストリーミング、動画広告
retail      : リテールメディア、コマース広告
ma          : M&A、資金調達、業績、倒産
measurement : 計測、アトリビューション、ブランドセーフティ、アドフラウド
ai          : 生成AIの広告領域への実装
agency      : 代理店動向、業界構造の変化
japan       : 日本国内市場の動向
search      : 検索広告、SEO、SGE

# 選定基準
上記タグに該当する記事を優先。
セミナー告知、人事異動、個別ブランドのキャンペーン事例は除外。

# 記述ルール
- summary は「〜について報じられた」等の空虚な表現を禁止
- insight は「効率化が期待される」等の一般論を禁止
- 専門用語は原語のまま（SSP, DSP, PMP, CDP, CTV）

# 記事一覧
"""
        + indexed
    )

    raw = call_groq(prompt, max_tokens=6000)
    data = parse_json_safely(raw)
    items = data.get("articles", [])

    valid = []
    for it in items:
        if not isinstance(it, dict):
            continue
        idx = it.get("id")
        if isinstance(idx, str) and idx.isdigit():
            idx = int(idx)
            it["id"] = idx
        if not isinstance(idx, int) or idx < 0 or idx >= len(articles):
            continue

        tags = it.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        it["tags"] = [str(t).lower().strip() for t in tags if t]

        imp = it.get("importance", 5)
        try:
            it["importance"] = int(imp)
        except Exception:
            it["importance"] = 5

        valid.append(it)

    print("[OK] 共通要約: " + str(len(valid)) + "件")
    return valid


def extract_tag_prefs(articles, common_items, fb):
    """評価履歴からタグごとのスコアを算出"""
    title_to_tags = {}
    for it in common_items:
        idx = it.get("id")
        if isinstance(idx, int) and 0 <= idx < len(articles):
            key = articles[idx]["title"].lower()[:60]
            title_to_tags[key] = it.get("tags", [])

    scores = {}
    for t in TAG_LIST:
        scores[t] = 0.0

    for title in fb.get("good", []):
        key = title.lower()[:60]
        for k, tags in title_to_tags.items():
            if key[:30] in k or k[:30] in key:
                for t in tags:
                    if t in scores:
                        scores[t] += 1.0

    for title in fb.get("bad", []):
        key = title.lower()[:60]
        for k, tags in title_to_tags.items():
            if key[:30] in k or k[:30] in key:
                for t in tags:
                    if t in scores:
                        scores[t] -= 1.2

    return scores


def personalize(articles, common_items, member, personal_fb, team_fb, limit):
    """個人向けに記事を選択・並べ替え"""
    p_scores = extract_tag_prefs(articles, common_items, personal_fb)
    t_scores = extract_tag_prefs(articles, common_items, team_fb)

    focus = member.get("focus", "").lower()
    focus_tags = []
    focus_map = {
        "privacy": ["プライバシー", "規制", "cookie", "クッキー"],
        "platform": ["プラットフォーム", "google", "meta", "amazon"],
        "ctv": ["ctv", "動画", "ストリーミング", "テレビ"],
        "retail": ["リテール", "コマース", "ec"],
        "ma": ["m&a", "買収", "資金調達", "業績"],
        "measurement": ["計測", "アトリビューション", "測定"],
        "ai": ["ai", "生成ai"],
        "agency": ["代理店", "エージェンシー"],
        "japan": ["国内", "日本"],
        "search": ["検索", "seo", "リスティング"],
    }
    for tag, words in focus_map.items():
        for w in words:
            if w in focus:
                focus_tags.append(tag)
                break

    scored = []
    for it in common_items:
        base = float(it.get("importance", 5))
        tags = it.get("tags", [])

        personal = 0.0
        team = 0.0
        for t in tags:
            personal += p_scores.get(t, 0.0)
            team += t_scores.get(t, 0.0)

        focus_bonus = 0.0
        for t in tags:
            if t in focus_tags:
                focus_bonus += 2.5

        total = base + personal * 2.0 + team * 0.6 + focus_bonus
        scored.append((total, it))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [it for _, it in scored[:limit]]


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

    arts_all = fetch_articles(HOURS_BACK)
    print("通常範囲(" + str(HOURS_BACK) + "h)の取得: " + str(len(arts_all)) + "件")

    if len(arts_all) < MIN_ARTICLES:
        print("[INFO] 記事が少ないため範囲を拡大")
        arts_all = fetch_articles(EXTENDED_HOURS)
        print("拡大範囲の取得: " + str(len(arts_all)) + "件")

    if not arts_all:
        for m in members:
            try:
                send_mail(
                    m["email"],
                    build_empty_html(m, "収集対象の媒体に新着記事がありませんでした。"),
                    build_empty_text(m, "収集対象の媒体に新着記事がありませんでした。"),
                    m.get("name", ""),
                )
            except Exception as e:
                print("[ERROR] 送信失敗: " + str(e))
        return

    arts = cap_articles(arts_all)

    all_fb = load_all_feedback()
    total_good = 0
    total_bad = 0
    for v in all_fb.values():
        total_good += len(v.get("good", []))
        total_bad += len(v.get("bad", []))
    print("[INFO] チーム全体の評価: GOOD " + str(total_good)
          + " / BAD " + str(total_bad))

    print("")
    print("===== 共通要約を生成 =====")
    common_items = []
    try:
        common_items = summarize_common(arts)
    except Exception as e:
        print("[ERROR] 共通要約に失敗: " + str(e))

    success = 0
    empty_sent = 0
    history_changed = False

    for i, member in enumerate(members):
        name = member.get("name", "")
        label = name if name else member["email"]
        print("")
        print("===== 処理中: " + label + " =====")

        sent_map = history.get(name, {})
        items = []

        if common_items:
            unsent_items = []
            for it in common_items:
                idx = it.get("id")
                if not isinstance(idx, int) or idx < 0 or idx >= len(arts):
                    continue
                key = normalize_url(arts[idx]["link"])
                if key not in sent_map:
                    unsent_items.append(it)

            print("[INFO] 未配信候補: " + str(len(unsent_items)) + "件")

            if not unsent_items:
                print("[INFO] 未配信記事なし。空メールを送信")
                try:
                    send_mail(
                        member["email"],
                        build_empty_html(member, "未読の新着記事がありませんでした。"),
                        build_empty_text(member, "未読の新着記事がありませんでした。"),
                        name,
                    )
                    empty_sent += 1
                except Exception as e:
                    print("[ERROR] 送信失敗: " + str(e))
                continue

            personal_fb = all_fb.get(name, {"good": [], "bad": []})
            team_fb = build_team_feedback(all_fb, name)
            print("[INFO] 本人 GOOD " + str(len(personal_fb.get("good", [])))
                  + " / BAD " + str(len(personal_fb.get("bad", [])))
                  + "　チーム GOOD " + str(len(team_fb["good"]))
                  + " / BAD " + str(len(team_fb["bad"])))

            items = personalize(arts, unsent_items, member,
                                personal_fb, team_fb, MAX_ARTICLES_IN_MAIL)
            print("[OK] 選定: " + str(len(items)) + "件")

            html_body = build_html(items, arts, member)
            text_body = build_text(items, arts, member)
        else:
            print("[WARN] 共通要約なし。フォールバック送信")
            html_body = build_fallback_html(arts)
            text_body = build_fallback_text(arts)

        try:
            send_mail(member["email"], html_body, text_body, name)
            success += 1
            if items:
                n = record_sent(history, name, arts, items)
                history_changed = True
                print("[OK] 履歴に" + str(n) + "件を記録")
        except Exception as e:
            print("[ERROR] 送信失敗 " + member["email"] + ": " + str(e))

        time.sleep(3)

    if history_changed:
        save_history(history)

    print("")
    print("[DONE] 配信 " + str(success) + "件 / 空通知 " + str(empty_sent)
          + "件 / 全" + str(len(members)) + "名")


if __name__ == "__main__":
    main()
