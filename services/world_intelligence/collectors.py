#!/usr/bin/env python3
"""
WORLD INTELLIGENCE COLLECTORS — one adapter per source category.

Honesty rules enforced here:
  - A collector that cannot reach its source returns items=[] and records the
    error. It never substitutes cached or invented content.
  - `configured` reports whether credentials/config exist at all, separately
    from whether the last fetch succeeded. A source needing a token that has no
    token is reported as unconfigured, not as broken or as empty.
  - Every item carries its source, url, and fetch timestamp.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import Protocol

UA = "NEXARA-WorldIntelligence/10.0 (+https://github.com/Ajeetesh-Ranjan)"
TIMEOUT = int(os.environ.get("NEXARA_FETCH_TIMEOUT", "20"))


@dataclass
class Item:
    source: str
    category: str
    external_id: str
    title: str
    text: str
    url: str
    author: str = ""
    score: int = 0
    comments: int = 0
    published_at: float = 0.0
    fetched_at: float = field(default_factory=time.time)
    raw: dict = field(default_factory=dict)

    def key(self) -> str:
        return f"{self.source}:{self.external_id}"


@dataclass
class Result:
    source: str
    category: str
    items: list[Item]
    configured: bool
    ok: bool
    error: str = ""

    def summary(self) -> dict:
        return {"source": self.source, "category": self.category,
                "items": len(self.items), "configured": self.configured,
                "ok": self.ok, "error": self.error}


def _get(url: str, timeout: int = TIMEOUT, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_json(url: str, timeout: int = TIMEOUT, headers: dict | None = None):
    return json.loads(_get(url, timeout, headers).decode("utf-8", "replace"))


# ------------------------------------------------------------ Hacker News ---
class HackerNews:
    source, category = "hackernews", "technology"

    def collect(self, limit: int = 40) -> Result:
        try:
            ids = _get_json("https://hacker-news.firebaseio.com/v0/topstories.json")[:limit]
            items = []
            for sid in ids:
                try:
                    s = _get_json(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=10)
                except (urllib.error.URLError, TimeoutError, OSError):
                    continue
                if not s or not s.get("title"):
                    continue
                items.append(Item(
                    source=self.source, category=self.category, external_id=str(sid),
                    title=s["title"], text=s.get("text", "") or s["title"],
                    url=s.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                    author=s.get("by", ""), score=int(s.get("score", 0)),
                    comments=int(s.get("descendants", 0)),
                    published_at=float(s.get("time", 0)),
                ))
            return Result(self.source, self.category, items, True, True)
        except Exception as e:
            return Result(self.source, self.category, [], True, False, str(e))


# ------------------------------------------------------------------ arXiv ---
class Arxiv:
    source, category = "arxiv", "research"
    NS = {"a": "http://www.w3.org/2005/Atom"}

    def collect(self, query: str = "cat:cs.AI", limit: int = 30) -> Result:
        try:
            q = urllib.parse.urlencode({
                "search_query": query, "start": 0, "max_results": limit,
                "sortBy": "submittedDate", "sortOrder": "descending",
            })
            xml = _get(f"https://export.arxiv.org/api/query?{q}", timeout=30)
            root = ET.fromstring(xml)
            items = []
            for e in root.findall("a:entry", self.NS):
                eid = (e.findtext("a:id", "", self.NS) or "").rsplit("/", 1)[-1]
                title = " ".join((e.findtext("a:title", "", self.NS) or "").split())
                summary = " ".join((e.findtext("a:summary", "", self.NS) or "").split())
                published = e.findtext("a:published", "", self.NS) or ""
                ts = 0.0
                if published:
                    try:
                        ts = time.mktime(time.strptime(published, "%Y-%m-%dT%H:%M:%SZ"))
                    except ValueError:
                        pass
                authors = [a.findtext("a:name", "", self.NS) for a in e.findall("a:author", self.NS)]
                items.append(Item(
                    source=self.source, category=self.category, external_id=eid,
                    title=title, text=f"{title}. {summary}",
                    url=e.findtext("a:id", "", self.NS) or "",
                    author=", ".join(a for a in authors[:3] if a),
                    published_at=ts,
                ))
            return Result(self.source, self.category, items, True, True)
        except Exception as e:
            return Result(self.source, self.category, [], True, False, str(e))


# ----------------------------------------------------------------- GitHub ---
class GitHub:
    source, category = "github", "open-source"

    def __init__(self):
        self.token = os.environ.get("GITHUB_TOKEN", "").strip()

    def collect(self, days: int = 7, limit: int = 30) -> Result:
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
            q = urllib.parse.urlencode({
                "q": f"created:>{since} stars:>50 topic:ai",
                "sort": "stars", "order": "desc", "per_page": limit,
            })
            data = _get_json(f"https://api.github.com/search/repositories?{q}", headers=headers)
            items = []
            for r in data.get("items", []):
                items.append(Item(
                    source=self.source, category=self.category,
                    external_id=r["full_name"],
                    title=r["full_name"],
                    text=f"{r['full_name']}. {r.get('description') or ''} "
                         f"Topics: {', '.join(r.get('topics', []))}. "
                         f"Language: {r.get('language') or 'unknown'}.",
                    url=r["html_url"], author=r["owner"]["login"],
                    score=int(r.get("stargazers_count", 0)),
                    comments=int(r.get("open_issues_count", 0)),
                    raw={"language": r.get("language"), "topics": r.get("topics", [])},
                ))
            return Result(self.source, self.category, items, True, True)
        except urllib.error.HTTPError as e:
            note = " (unauthenticated rate limit — set GITHUB_TOKEN)" if e.code == 403 and not self.token else ""
            return Result(self.source, self.category, [], True, False, f"HTTP {e.code}{note}")
        except Exception as e:
            return Result(self.source, self.category, [], True, False, str(e))


# ------------------------------------------------------------------- RSS ----
class RSS:
    """Generic RSS/Atom adapter — powers business + AI-news categories."""

    def __init__(self, source: str, category: str, feeds: list[str]):
        self.source, self.category, self.feeds = source, category, feeds

    def collect(self, limit: int = 25) -> Result:
        items, errors = [], []
        for feed in self.feeds:
            try:
                raw = _get(feed, timeout=20)
                root = ET.fromstring(raw)
                entries = root.findall(".//item") or root.findall(
                    ".//{http://www.w3.org/2005/Atom}entry")
                for node in entries[:limit]:
                    def t(*names, _n=node):
                        for n in names:
                            v = _n.findtext(n)
                            if v:
                                return " ".join(v.split())
                        return ""
                    title = t("title", "{http://www.w3.org/2005/Atom}title")
                    if not title:
                        continue
                    link = t("link", "guid") or ""
                    if not link:
                        le = node.find("{http://www.w3.org/2005/Atom}link")
                        link = le.get("href", "") if le is not None else ""
                    desc = t("description", "{http://www.w3.org/2005/Atom}summary")
                    items.append(Item(
                        source=self.source, category=self.category,
                        external_id=link or title,
                        title=title, text=f"{title}. {desc}"[:4000], url=link,
                    ))
            except Exception as e:
                errors.append(f"{urllib.parse.urlparse(feed).netloc}: {e}")
        ok = bool(items)
        return Result(self.source, self.category, items, True, ok, "; ".join(errors)[:300])


# -------------------------------------------------------------- Telegram ----
class Telegram:
    """
    Telegram channel intelligence.

    Requires TELEGRAM_BOT_TOKEN and the bot to be a member of the channels in
    TELEGRAM_CHANNELS. Without a token this reports configured=False — the
    architecture exists and is wired, but no data is invented to fill the gap.
    """
    source, category = "telegram", "communities"

    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.channels = [c.strip() for c in
                         os.environ.get("TELEGRAM_CHANNELS", "").split(",") if c.strip()]

    def collect(self, limit: int = 50) -> Result:
        if not self.token:
            return Result(self.source, self.category, [], False, False,
                          "TELEGRAM_BOT_TOKEN not set — see docs/WORLD_INTELLIGENCE.md")
        try:
            base = f"https://api.telegram.org/bot{self.token}"
            me = _get_json(f"{base}/getMe", timeout=15)
            if not me.get("ok"):
                return Result(self.source, self.category, [], True, False, "getMe rejected token")
            updates = _get_json(f"{base}/getUpdates?limit={limit}&allowed_updates="
                                f'["channel_post","message"]', timeout=20)
            items = []
            for u in updates.get("result", []):
                post = u.get("channel_post") or u.get("message")
                if not post:
                    continue
                text = post.get("text") or post.get("caption") or ""
                if not text.strip():
                    continue
                chat = post.get("chat", {})
                cname = chat.get("username") or str(chat.get("id", ""))
                if self.channels and cname not in self.channels:
                    continue
                items.append(Item(
                    source=self.source, category=self.category,
                    external_id=f"{chat.get('id')}:{post.get('message_id')}",
                    title=text.strip().split("\n")[0][:160],
                    text=text, url=f"https://t.me/{cname}/{post.get('message_id')}",
                    author=chat.get("title", cname),
                    published_at=float(post.get("date", 0)),
                ))
            return Result(self.source, self.category, items, True, True)
        except Exception as e:
            return Result(self.source, self.category, [], True, False, str(e))


# ------------------------------------------------------------- registry -----
AI_FEEDS = [
    "https://hnrss.org/newest?q=AI+OR+LLM+OR+agent&points=30",
    "https://export.arxiv.org/rss/cs.LG",
]
BUSINESS_FEEDS = [
    "https://feeds.a.dj.com/rss/RSSWSJD.xml",
    "https://techcrunch.com/feed/",
]
DEV_COMMUNITY_FEEDS = [
    "https://lobste.rs/rss",
    "https://dev.to/feed",
]


class Collector(Protocol):
    source: str
    category: str

    def collect(self, *args, **kwargs) -> Result: ...


def registry() -> dict[str, Collector]:
    return {
        "hackernews": HackerNews(),
        "arxiv": Arxiv(),
        "github": GitHub(),
        "ai-news": RSS("ai-news", "ai", AI_FEEDS),
        "business": RSS("business", "business", BUSINESS_FEEDS),
        "dev-communities": RSS("dev-communities", "communities", DEV_COMMUNITY_FEEDS),
        "telegram": Telegram(),
    }


def collect_all(only: list[str] | None = None) -> tuple[list[Item], list[dict]]:
    items: list[Item] = []
    reports: list[dict] = []
    for name, col in registry().items():
        if only and name not in only:
            continue
        res = col.collect()
        items.extend(res.items)
        reports.append(res.summary())
    return items, reports


if __name__ == "__main__":
    items, reports = collect_all()
    for r in reports:
        state = "ok" if r["ok"] else ("UNCONFIGURED" if not r["configured"] else "FAIL")
        print(f"  {r['source']:16} {r['category']:12} {r['items']:4} items  {state}"
              + (f"  {r['error'][:70]}" if r["error"] else ""))
    print(f"\ntotal items: {len(items)}")
