"""Tech news digest -> Tech Pulse web app (GitHub Pages) + push notification.

Fetches RSS/Atom feeds, drops anything already sent, picks a balanced set of
fresh stories and adds them to docs/news.json, which the web app in docs/
displays. A second mode sends a push notification to the owner's phone.

  python news.py            fetch, pick, update docs/news.json and state/sent.json
  python news.py --dry-run  print the digest, write nothing
  python news.py --notify   send a push notification for the latest digest

The first two modes use the standard library only. --notify needs the
pywebpush package (installed by the workflow) and these env vars:
  VAPID_PRIVATE_KEY   private half of the key pair whose public half is in docs/index.html
  PUSH_SUBSCRIPTION   the JSON text the app shows after notifications are turned on
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# ----------------------------------------------------------------- settings
MAX_ITEMS = 12           # stories per run
ITEMS_PER_MESSAGE = 4    # stories per block in the dry-run printout
MAX_PER_SOURCE = 2       # keeps one site from dominating a digest
MAX_AGE_HOURS = 36       # ignore anything older than this
SUMMARY_CHARS = 230
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "sent.json")
STATE_KEEP_DAYS = 30
NEWS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "news.json")
FEED_KEEP = 160          # stories kept in the app's feed
PUSH_TTL = 3 * 3600      # seconds a notification may wait if the phone is offline
IST = timezone(timedelta(hours=5, minutes=30))

AI, DE, DS, BI, TECH, STARTUP, GADGET, SEC = (
    "AI", "Data Engineering", "Data Science", "BI & Analytics",
    "Tech", "Startups", "Gadgets", "Security",
)
EMOJI = {AI: "🤖", DE: "🛠️", DS: "📊", BI: "📈", TECH: "💻", STARTUP: "🚀", GADGET: "📱",
         SEC: "🔒"}
CATEGORY_ORDER = [AI, DE, DS, BI, TECH, STARTUP, GADGET, SEC]

# (source name, default category, url). Checked 21 Nov 2026: every feed except
# VentureBeat (which blocks some IPs) returned items. A failing feed is skipped.
FEEDS = [
    # --- AI and machine learning
    ("TechCrunch AI",   AI, "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("VentureBeat AI",  AI, "https://venturebeat.com/category/ai/feed/"),
    ("MIT Tech Review", AI, "https://www.technologyreview.com/feed/"),
    ("MIT News AI",     AI, "https://news.mit.edu/rss/topic/artificial-intelligence2"),
    ("Google AI",       AI, "https://blog.google/technology/ai/rss/"),
    ("OpenAI",          AI, "https://openai.com/news/rss.xml"),
    ("Hugging Face",    AI, "https://huggingface.co/blog/feed.xml"),
    ("NVIDIA",          AI, "https://blogs.nvidia.com/feed/"),
    ("Ars AI",          AI, "https://arstechnica.com/ai/feed/"),
    ("Simon Willison",  AI, "https://simonwillison.net/atom/everything/"),
    ("Berkeley AI",     AI, "https://bair.berkeley.edu/blog/feed.xml"),
    ("Import AI",       AI, "https://importai.substack.com/feed"),
    ("Ahead of AI",     AI, "https://magazine.sebastianraschka.com/feed"),
    ("MarkTechPost",    AI, "https://www.marktechpost.com/feed/"),
    ("Analytics India", AI, "https://analyticsindiamag.com/feed/"),
    # --- Data engineering and platforms
    ("Databricks",              DE, "https://www.databricks.com/feed"),
    ("Microsoft Fabric",        DE, "https://blog.fabric.microsoft.com/en-us/blog/feed/"),
    ("AWS Big Data",            DE, "https://aws.amazon.com/blogs/big-data/feed/"),
    ("Google Cloud Data",       DE, "https://cloudblog.withgoogle.com/products/data-analytics/rss/"),
    ("Confluent",               DE, "https://www.confluent.io/rss.xml"),
    ("dbt Labs",                DE, "https://www.getdbt.com/blog/rss.xml"),
    ("Dagster",                 DE, "https://dagster.io/rss.xml"),
    ("Astronomer",              DE, "https://www.astronomer.io/blog/rss.xml"),
    ("DuckDB",                  DE, "https://duckdb.org/feed.xml"),
    ("ClickHouse",              DE, "https://clickhouse.com/rss.xml"),
    ("Monte Carlo",             DE, "https://www.montecarlodata.com/feed/"),
    ("Fivetran",                DE, "https://www.fivetran.com/blog/rss.xml"),
    ("Data Engineering Weekly", DE, "https://www.dataengineeringweekly.com/feed"),
    ("Seattle Data Guy",        DE, "https://seattledataguy.substack.com/feed"),
    # --- Data science
    ("KDnuggets",            DS, "https://www.kdnuggets.com/feed"),
    ("Towards Data Science", DS, "https://towardsdatascience.com/feed"),
    ("R-bloggers",           DS, "https://www.r-bloggers.com/feed/"),
    ("Netflix Tech",         DS, "https://netflixtechblog.com/feed"),
    # --- BI and analytics
    ("Power BI Blog", BI, "https://powerbi.microsoft.com/en-us/blog/feed/"),
    ("Tableau",       BI, "https://www.tableau.com/blog/feed"),
    ("SQLBI",         BI, "https://www.sqlbi.com/feed/"),
    ("Chris Webb BI", BI, "https://blog.crossjoin.co.uk/feed/"),
    ("Metabase",      BI, "https://www.metabase.com/feed.xml"),
    # --- General tech news, including the major newsrooms
    ("Techmeme",             TECH, "https://www.techmeme.com/feed.xml"),
    ("The Verge",            TECH, "https://www.theverge.com/rss/index.xml"),
    ("Ars Technica",         TECH, "https://feeds.arstechnica.com/arstechnica/index"),
    ("Hacker News",          TECH, "https://hnrss.org/frontpage?points=200"),
    ("BBC Technology",       TECH, "http://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("Guardian Tech",        TECH, "https://www.theguardian.com/uk/technology/rss"),
    ("New York Times Tech",  TECH, "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
    ("Financial Times Tech", TECH, "https://www.ft.com/technology?format=rss"),
    ("Bloomberg Tech",       TECH, "https://feeds.bloomberg.com/technology/news.rss"),
    ("CNBC Tech",            TECH, "https://www.cnbc.com/id/19854910/device/rss/rss.html"),
    ("Wired",                TECH, "https://www.wired.com/feed/rss"),
    ("The Register",         TECH, "https://www.theregister.com/headlines.atom"),
    ("IEEE Spectrum",        TECH, "https://spectrum.ieee.org/feeds/feed.rss"),
    ("ZDNET",                TECH, "https://www.zdnet.com/news/rss.xml"),
    ("CNET",                 TECH, "https://www.cnet.com/rss/news/"),
    ("TechRadar",            TECH, "https://www.techradar.com/rss"),
    ("Gizmodo",              TECH, "https://gizmodo.com/rss"),
    ("Slashdot",             TECH, "https://rss.slashdot.org/Slashdot/slashdotMain"),
    ("Lobsters",             TECH, "https://lobste.rs/rss"),
    ("The Next Web",         TECH, "https://thenextweb.com/feed"),
    ("Hacker Noon",          TECH, "https://hackernoon.com/feed"),
    ("InfoQ",                TECH, "https://feed.infoq.com/"),
    ("GitHub Blog",          TECH, "https://github.blog/feed/"),
    ("Stack Overflow Blog",  TECH, "https://stackoverflow.blog/feed/"),
    ("Cloudflare",           TECH, "https://blog.cloudflare.com/rss/"),
    ("AWS News",             TECH, "https://aws.amazon.com/blogs/aws/feed/"),
    ("Apple Newsroom",       TECH, "https://www.apple.com/newsroom/rss-feed.rss"),
    ("MIT News Computing",   TECH, "https://news.mit.edu/rss/topic/computers"),
    ("Platformer",           TECH, "https://www.platformer.news/rss/"),
    ("Rest of World",        TECH, "https://restofworld.org/feed/latest/"),
    ("ET Tech",              TECH, "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms"),
    ("Livemint Tech",        TECH, "https://www.livemint.com/rss/technology"),
    ("The Hindu Tech",       TECH, "https://www.thehindu.com/sci-tech/technology/feeder/default.rss"),
    # --- Startups and funding
    ("TechCrunch",      STARTUP, "https://techcrunch.com/feed/"),
    ("Crunchbase News", STARTUP, "https://news.crunchbase.com/feed/"),
    ("Sifted",          STARTUP, "https://sifted.eu/feed"),
    ("YourStory",       STARTUP, "https://yourstory.com/feed"),
    ("Inc42",           STARTUP, "https://inc42.com/feed/"),
    ("Entrackr",        STARTUP, "https://entrackr.com/rss"),
    # --- Gadgets and hardware
    ("Engadget",          GADGET, "https://www.engadget.com/rss.xml"),
    ("Android Authority", GADGET, "https://www.androidauthority.com/feed/"),
    ("9to5Google",        GADGET, "https://9to5google.com/feed/"),
    ("9to5Mac",           GADGET, "https://9to5mac.com/feed/"),
    ("Tom's Hardware",    GADGET, "https://www.tomshardware.com/feeds/all"),
    ("GSMArena",          GADGET, "https://www.gsmarena.com/rss-news-reviews.php3"),
    ("Digital Trends",    GADGET, "https://www.digitaltrends.com/feed/"),
    # --- Security
    ("The Hacker News",      SEC, "https://feeds.feedburner.com/TheHackersNews"),
    ("BleepingComputer",     SEC, "https://www.bleepingcomputer.com/feed/"),
    ("Krebs on Security",    SEC, "https://krebsonsecurity.com/feed/"),
    ("Schneier on Security", SEC, "https://www.schneier.com/feed/atom/"),
    ("Project Zero",         SEC, "https://googleprojectzero.blogspot.com/feeds/posts/default"),
]

# Stories from general feeds are re-filed when the title matches a topic.
TOPIC_RULES = [
    (DE, re.compile(r"\b(databricks|snowflake|apache spark|pyspark|dbt|airflow|kafka|lakehouse|"
                    r"data pipelines?|etl|elt|data warehouse|iceberg|delta lake|microsoft fabric|"
                    r"bigquery|redshift|duckdb|data engineer\w*)\b", re.I)),
    (BI, re.compile(r"\b(power ?bi|tableau|looker|qlik|business intelligence|dashboards?|"
                    r"data analysts?|dax)\b", re.I)),
    (DS, re.compile(r"\b(data scien\w+|pandas|scikit|statistics|jupyter|kaggle)\b", re.I)),
    (SEC, re.compile(r"\b(ransomware|malware|phishing|zero[- ]day|data breach(?:es)?|"
                     r"breached|vulnerabilit\w+|cve-\d|exploit\w*|cyber\w*|hackers?|"
                     r"hacked|spyware|botnet|ddos|infostealer|patch tuesday)\b", re.I)),
    (AI, re.compile(r"\b(ai|a\.i\.|llms?|gpt\S*|openai|anthropic|claude|gemini|copilot|"
                    r"machine learning|deep learning|neural|agentic|chatbots?|generative)\b", re.I)),
]
GENERAL = {TECH, STARTUP, GADGET}

# Headlines that are not news: shopping deals, sports streaming guides, puzzle answers.
# Gadget and consumer sites publish many of these, especially around Black Friday.
NOISE = re.compile(r"\b(?:how|where) to watch\b|\blive ?stream|\bkick-?off\b|\bwordle\b|"
                   r"\bquordle\b|\bcrossword\b|\b(?:connections|strands)\b.{0,24}\bhints?\b|"
                   r"\bhints? (?:and|&) answers?\b|\btoday'?s (?:hints?|answers?)\b|"
                   r"\bhoroscope\b|\bcoupons?\b|\bpromo code\b|\brecord[- ]low\b|"
                   r"\blowest price\b|\bbest price\b|\bprice drop\b|\bdeal of the day\b", re.I)
# A shopping event only counts as noise next to a shopping word, so that, say,
# "Scaling Black Friday pipelines with Airflow" still gets through.
EVENT = re.compile(r"\b(?:black friday|cyber monday|prime day)\b", re.I)
SHOP = re.compile(r"\bdeals?\b|\bsale\b|\bdiscounts?\b|\bsave\b|\$\d|\d{1,3}% off|"
                  r"\bgift guide\b|\bshop\b|\bbuy\b|\bprices?\b|\boffers?\b", re.I)
PRICED = re.compile(r"\$\d|\b\d{1,3}% off\b", re.I)
BARGAIN = re.compile(r"\b(?:deals?|save|sale|discounts?|drops? to|now just|cheapest|"
                     r"bargain|under \$|off)\b", re.I)
# ... unless the money is the story: funding, valuations, revenue.
MONEY_NEWS = re.compile(r"\b(?:raise[sd]?|raising|funding|valuation|rounds?|acquir\w+|"
                        r"acquisitions?|millions?|billions?|ipo|revenue|series [a-f]|"
                        r"invest\w+|fined?|lawsuits?|contracts?)\b", re.I)


def is_noise(title):
    if NOISE.search(title):
        return True
    if EVENT.search(title) and SHOP.search(title):
        return True
    return bool(PRICED.search(title) and BARGAIN.search(title) and not MONEY_NEWS.search(title))

UA = "Mozilla/5.0 (compatible; tech-news-digest/1.0)"


# ------------------------------------------------------------------ fetching
def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def child_text(el, *names):
    for c in el:
        if local(c.tag) in names and (c.text or "").strip():
            return c.text.strip()
    return ""


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(re.sub(r"\.\d+", "", s.strip()).replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def clean_url(u):
    p = urllib.parse.urlsplit(u.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q), ""))


def clean_text(s):
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(html.unescape(s))
    s = re.sub(r"The post .{0,200}? appeared first on .*$", "", s, flags=re.S)
    s = re.sub(r"\b(Continue reading|Read more|Read the full story)\b.*$", "", s, flags=re.S | re.I)
    s = s.replace("*", "").replace("_", " ").replace("~", "").replace("`", "")
    return re.sub(r"\s+", " ", s).strip(" .…[]") if s else ""


def shorten(s, limit=SUMMARY_CHARS):
    if len(s) <= limit:
        return s + ("." if s and s[-1] not in ".!?" else "")
    cut = s[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end > limit * 0.5:
        return cut[:end + 1]
    return cut[:cut.rfind(" ")].rstrip(",;:-") + "…"


def parse_feed(source, category, raw):
    items = []
    root = ET.fromstring(raw)
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        title = clean_text(child_text(el, "title"))
        link = child_text(el, "link")
        if not link:
            for c in el:
                if local(c.tag) == "link" and c.get("href") and c.get("rel", "alternate") == "alternate":
                    link = c.get("href")
                    break
        when = parse_date(child_text(el, "pubdate", "published", "updated", "date"))
        if not (title and link and when):
            continue
        if is_noise(title):
            continue
        raw_desc = child_text(el, "description", "summary") or child_text(el, "encoded", "content")
        if source == "Hacker News":
            pts = re.search(r"Points:\s*(\d+)", raw_desc)
            com = re.search(r"# Comments:\s*(\d+)", raw_desc)
            summary = "Trending on Hacker News"
            if pts:
                summary += " — {} points".format(pts.group(1))
            if com:
                summary += ", {} comments".format(com.group(1))
            summary += "."
        else:
            summary = clean_text(raw_desc)
            summary = "" if summary.lower().startswith(title.lower()[:60]) and len(summary) < len(title) + 20 else shorten(summary) if summary else ""
        cat = category
        if category in GENERAL:
            for topic, rx in TOPIC_RULES:
                if rx.search(title):
                    cat = topic
                    break
        items.append({"title": title, "url": clean_url(link), "summary": summary,
                      "source": source, "category": cat, "when": when})
    return items


def fetch_all():
    def one(feed):
        source, category, url = feed
        try:
            got = parse_feed(source, category, http_get(url))
            print("  ok   {:<24} {:>3} items".format(source, len(got)))
            return got
        except Exception as e:
            print("  FAIL {:<24} {}".format(source, e))
            return []
    with ThreadPoolExecutor(max_workers=16) as pool:
        return [i for got in pool.map(one, FEEDS) for i in got]


# --------------------------------------------------------------------- state
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)["sent"]
    except (OSError, ValueError, KeyError):
        return []


def save_state(sent):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STATE_KEEP_DAYS)).isoformat()
    sent = [s for s in sent if s["ts"] >= cutoff]
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"sent": sent}, f, ensure_ascii=False, indent=0)


def title_key(t):
    return frozenset(w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 3)


def similar(a, b):
    """Same story from another site: mostly the same words, or 3+ shared key words."""
    if not (a and b):
        return False
    return len(a & b) >= 3 or len(a & b) / float(len(a | b)) >= 0.6


# ----------------------------------------------------------------- selection
def select(items, sent):
    now = datetime.now(timezone.utc)
    seen_urls = {s["u"] for s in sent}
    day_ago = (now - timedelta(hours=24)).isoformat()
    seen_titles = [title_key(s["t"]) for s in sent if s["ts"] >= day_ago]
    fresh = [i for i in items
             if i["url"] not in seen_urls
             and timedelta(0) <= now - i["when"] <= timedelta(hours=MAX_AGE_HOURS)]
    fresh.sort(key=lambda i: i["when"], reverse=True)

    buckets = {c: [i for i in fresh if i["category"] == c] for c in CATEGORY_ORDER}
    picked, per_source = [], {}
    while len(picked) < MAX_ITEMS and any(buckets.values()):
        for cat in CATEGORY_ORDER:
            while buckets[cat]:
                i = buckets[cat].pop(0)
                key = title_key(i["title"])
                if per_source.get(i["source"], 0) >= MAX_PER_SOURCE:
                    continue
                if i["url"] in seen_urls or any(similar(key, k) for k in seen_titles):
                    continue
                picked.append(i)
                seen_urls.add(i["url"])
                seen_titles.append(key)
                per_source[i["source"]] = per_source.get(i["source"], 0) + 1
                break
            if len(picked) >= MAX_ITEMS:
                break
    picked.sort(key=lambda i: CATEGORY_ORDER.index(i["category"]))
    return picked


# ---------------------------------------------------------------- formatting
def ago(when):
    mins = int((datetime.now(timezone.utc) - when).total_seconds() // 60)
    if mins < 60:
        return "{}m ago".format(max(mins, 1))
    if mins < 60 * 24:
        return "{}h ago".format(mins // 60)
    return "{}d ago".format(mins // (60 * 24))


def build_messages(picked):
    stamp = datetime.now(IST).strftime("%a %d %b, %I:%M %p").replace(" 0", " ")
    chunks = [picked[i:i + ITEMS_PER_MESSAGE] for i in range(0, len(picked), ITEMS_PER_MESSAGE)]
    messages = []
    for n, chunk in enumerate(chunks, 1):
        part = " ({}/{})".format(n, len(chunks)) if len(chunks) > 1 else ""
        lines = ["🗞️ *TECH PULSE*{}".format(part), "🕒 {} IST".format(stamp), "━━━━━━━━━━━━━━━"]
        last_cat = None
        for i in chunk:
            if i["category"] != last_cat:
                lines += ["", "{} *{}*".format(EMOJI[i["category"]], i["category"].upper())]
                last_cat = i["category"]
            lines += ["", "▪️ *{}*".format(i["title"])]
            if i["summary"]:
                lines.append("_{}_".format(i["summary"]))
            lines.append("📰 {} · {}".format(i["source"], ago(i["when"])))
            lines.append("🔗 {}".format(i["url"]))
        messages.append("\n".join(lines))
    return messages


# ---------------------------------------------------------------- publishing
def load_feed():
    try:
        with open(NEWS_FILE, encoding="utf-8") as f:
            return json.load(f).get("stories") or []
    except (OSError, ValueError, AttributeError):
        return []


def publish(picked, now):
    """Delivery: add this digest to the feed the web app reads (docs/news.json)."""
    batch = now.isoformat(timespec="seconds")
    new = [{"title": i["title"], "url": i["url"], "summary": i["summary"],
            "source": i["source"], "category": i["category"],
            "when": i["when"].isoformat(timespec="seconds"), "batch": batch} for i in picked]
    stories = (new + load_feed())[:FEED_KEEP]
    os.makedirs(os.path.dirname(NEWS_FILE), exist_ok=True)
    with open(NEWS_FILE, "w", encoding="utf-8") as f:
        json.dump({"updated": batch, "stories": stories}, f, ensure_ascii=False, indent=1)


# ------------------------------------------------------------- notification
def pages_url():
    url = os.environ.get("PAGES_URL", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not url and "/" in repo:
        owner, name = repo.split("/", 1)
        url = "https://{}.github.io/{}/".format(owner.lower(), name)
    return url.rstrip("/") + "/" if url else ""


def wait_until_live(updated, tries=24, pause=10):
    """GitHub Pages takes a minute or so to publish a commit. Wait for it, so the
    app already shows the new stories when the notification is tapped."""
    base = pages_url()
    if not base:
        return
    for _ in range(tries):
        try:
            raw = http_get("{}news.json?t={}".format(base, int(time.time())), timeout=15)
            if json.loads(raw.decode("utf-8")).get("updated") == updated:
                print("  site is showing the new digest")
                return
        except Exception:
            pass
        time.sleep(pause)
    print("  site still not updated after {}s - notifying anyway".format(tries * pause))


def report(level, msg):
    """Print the outcome. On GitHub Actions it also shows on the run's summary page."""
    if os.environ.get("GITHUB_ACTIONS"):
        print("::{} title=Push notification::{}".format(level, msg))
    else:
        print(msg)


def notify(test=False):
    key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    sub_raw = os.environ.get("PUSH_SUBSCRIPTION", "").strip()
    missing = [n for n, v in (("VAPID_PRIVATE_KEY", key), ("PUSH_SUBSCRIPTION", sub_raw)) if not v]
    if missing:
        report("warning", "No notification sent: the secret {} is missing or empty. Check the "
               "name is spelled exactly like this under Settings > Secrets and variables > "
               "Actions > Repository secrets.".format(" and ".join(missing)))
        return 1 if test else 0
    try:
        sub = json.loads(sub_raw)
        ok = bool(sub["endpoint"] and sub["keys"]["p256dh"] and sub["keys"]["auth"])
    except (ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        report("error", "PUSH_SUBSCRIPTION is not the full text from the app. Tap the bell, tap "
               "Copy, and paste everything from the first { to the last } into the secret.")
        return 1

    if test:
        payload = json.dumps({"title": "Tech Pulse — test",
                              "body": "Notifications are working on this phone."})
    else:
        try:
            with open(NEWS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        updated = data.get("updated")
        latest = [s for s in data.get("stories") or [] if s.get("batch") == updated]
        if not latest:
            print("No digest to notify about.")
            return 0
        wait_until_live(updated)
        payload = json.dumps({
            "title": "Tech Pulse — {} new {}".format(len(latest), "story" if len(latest) == 1 else "stories"),
            "body": "\n".join("• " + s["title"] for s in latest[:3]),
        })

    from pywebpush import webpush, WebPushException
    host = urllib.parse.urlsplit(sub["endpoint"]).netloc
    # Same fingerprint the app shows as "Device ID", to check the secret is this phone's.
    device = hashlib.sha256(sub["endpoint"].encode("utf-8")).hexdigest()[:8]
    try:
        # Urgency high so a phone that is idle / in Doze still shows it straight away.
        res = webpush(subscription_info=sub, data=payload, vapid_private_key=key, ttl=PUSH_TTL,
                      headers={"Urgency": "high"},
                      vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", "mailto:noreply@example.com")})
    except WebPushException as e:
        code = getattr(e.response, "status_code", None)
        if code in (404, 410):
            report("error", "The phone's push subscription has expired. Open the app, tap the "
                   "bell, copy the text and update the PUSH_SUBSCRIPTION secret.")
        elif code in (400, 401, 403):
            report("error", "Push rejected (HTTP {}): the subscription does not match the "
                   "current key. On the phone clear the site's data in Chrome, reopen the app, "
                   "turn notifications on again and update PUSH_SUBSCRIPTION.".format(code))
        else:
            report("error", "Push failed (HTTP {}) at {}.".format(code, host))
        return 1
    report("notice", "Accepted by {} (HTTP {}) for device ID {}. If nothing shows, compare this "
           "ID with the one in the app's bell panel; if they match, the phone is blocking "
           "it.".format(host, getattr(res, "status_code", "?"), device))
    return 0


def main():
    if "--notify" in sys.argv:
        return notify(test="--test" in sys.argv)
    dry = "--dry-run" in sys.argv

    print("Fetching feeds...")
    items = fetch_all()
    sent = load_state()
    picked = select(items, sent)
    print("{} stories fetched, {} selected".format(len(items), len(picked)))
    if not picked:
        print("Nothing new - feed left unchanged.")
        return 0

    if dry:
        print("\n[dry run - nothing written]\n")
        out = getattr(sys.stdout, "buffer", None)
        for m in build_messages(picked):
            if out:
                out.write((m + "\n\n" + "=" * 40 + "\n\n").encode("utf-8"))
            else:
                print(m)
        return 0

    now = datetime.now(timezone.utc)
    publish(picked, now)
    sent += [{"u": i["url"], "t": i["title"], "ts": now.isoformat()} for i in picked]
    save_state(sent)
    print("Feed updated with {} stories.".format(len(picked)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
