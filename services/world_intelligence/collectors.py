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
from datetime import datetime
from html.parser import HTMLParser
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import Protocol
from urllib.parse import urlparse

from services.world_intelligence.telegram_intelligence import IntelligenceRegistry, redact

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
    details: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"source": self.source, "category": self.category,
                "items": len(self.items), "configured": self.configured,
                "ok": self.ok, "error": self.error, **self.details}


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
class _TelegramText(HTMLParser):
    """Parse only the message text; media-only messages never borrow a neighbour."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts = []
        self.date = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif "tgme_widget_message_text" in (attrs.get("class") or "").split():
                self.depth = 1
        if tag == "time" and not self.date:
            self.date = attrs.get("datetime") or ""
        if tag == "br" and self.depth:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag == "div" and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


class Telegram:
    """
    Public-channel read-only Telegram intelligence.

    Requires no secrets. Fetches https://t.me/s/<username> HTML for channels
    the registry both stores AND re-validates (fail-closed allow-list). Filters
    noise; retains short, privacy-redacted excerpts with permalink, date,
    topics and channel metadata. Per-channel cursors make a replay of the same
    window yield nothing new, so this collects bounded signals, not archives.

    Does not read private messages, join channels, post, handle credentials,
    or poll a bot inbox.
    """
    source, category = "telegram", "communities"

    NOISE_PATTERNS = [
        re.compile(r"^\s*(hi|hello|hey|gm|gn|thanks?|thx|welcome|joined|left)\s*[.!]?\s*$", re.I),
        re.compile(r"(?i)(promo|promotion|discount|sale|buy now|limited offer)"),
        re.compile(r"(?i)(subscribe|follow|join|channel link|t\.me/)"),
        re.compile(r"(?i)^(👋|🤝|🎉|🔥|⚡|💡|🚀|📢)\s*$"),
    ]
    MIN_TEXT_LEN = 30
    MAX_EXCERPT = 300
    REQUEST_TIMEOUT = 15
    MAX_MESSAGES_PER_CHANNEL = 25
    MAX_TOTAL_RESPONSE_BYTES = 512 * 1024

    def __init__(self, registry: IntelligenceRegistry | None = None):
        self.registry = registry

    def _clean_html(self, html: str) -> str:
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"&nbsp;", " ", text, flags=re.I)
        text = re.sub(r"&[a-z]+;", " ", text, flags=re.I)
        return re.sub(r"\s+", " ", text).strip()

    def _is_noise(self, text: str) -> bool:
        if len(text) < self.MIN_TEXT_LEN:
            return True
        for pat in self.NOISE_PATTERNS:
            if pat.search(text):
                return True
        return False

    def _parse_messages(self, html: str, channel: dict, *, is_live_data: bool = False,
                        since_post_id: int = 0) -> list[Item]:
        items = []
        markers = list(re.finditer(r'data-post="([^"<>]+)"', html))
        for index, marker in enumerate(markers):
            post_id = marker.group(1)
            if not re.fullmatch(re.escape(channel["username"]) + r"/\d+", post_id, re.I):
                continue
            number = int(post_id.rsplit("/", 1)[-1])
            # Cursor: a replayed window re-parses the same posts and keeps none.
            if number <= since_post_id:
                continue
            end = markers[index + 1].start() if index + 1 < len(markers) else len(html)
            parser = _TelegramText()
            parser.feed(html[marker.end():end])
            text = " ".join(" ".join(parser.parts).split())
            if self._is_noise(text):
                continue
            try:
                published_at = datetime.fromisoformat(parser.date.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                published_at = 0.0
            # Redact before truncation so a cut-off identifier cannot survive.
            excerpt = redact(text)[:self.MAX_EXCERPT]
            if not excerpt:
                continue
            items.append(Item(
                source=self.source, category=channel["category"], external_id=post_id.lower(),
                title=excerpt[:160], text=excerpt,
                url=f"https://t.me/{channel['username']}/{number}",
                author=channel.get("publisher", channel["username"]), published_at=published_at,
                raw={"channel_id": channel["id"], "channel": channel["username"].lower(),
                     "topics": channel.get("topics", []), "publisher": channel.get("publisher", ""),
                     "post_id": number, "is_live_data": is_live_data},
            ))
        return items[-self.MAX_MESSAGES_PER_CHANNEL:]

    def _read_preview(self, channel: dict) -> str:
        """Fetch the public preview HTML. Only https://t.me/s/<username> is ever addressed."""
        req = urllib.request.Request(channel["url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=self.REQUEST_TIMEOUT) as response:
            raw = response.read(self.MAX_TOTAL_RESPONSE_BYTES + 1)
            if len(raw) > self.MAX_TOTAL_RESPONSE_BYTES:
                raise ValueError("public preview exceeds byte budget")
            return raw.decode("utf-8", "replace")

    def _fetch_channel(self, channel: dict, since_post_id: int = 0) -> list[Item]:
        html = self._read_preview(channel)
        if 'data-post="' not in html:
            raise ValueError("public preview unavailable or has no accessible posts")
        return self._parse_messages(html, channel, is_live_data=True,
                                    since_post_id=since_post_id)

    def collect(self, limit: int = 50, registry: IntelligenceRegistry | None = None) -> Result:
        registry = registry if registry is not None else self.registry
        if registry is None:
            return Result(self.source, self.category, [], False, False,
                          "No channel registry supplied; selective ingestion is fail-closed. "
                          "See docs/phase11/TELEGRAM.md.")
        enabled, quarantined = registry.enabled_channels()
        polling = registry.bot_polling_decision()
        details = {"mode": "public-preview", "bot_polling": polling}
        if quarantined:
            details["quarantined"] = quarantined
        if not enabled:
            reason = ("No channel passed the fail-closed allow-list"
                      if quarantined else
                      "No enabled public channels; see docs/phase11/TELEGRAM.md")
            return Result(self.source, self.category, [], False, False,
                          f"{reason}. Bot/private-message polling is off: {polling['reason']}",
                          details)

        items, errors, channel_reports = [], [], []
        for channel in enabled:
            if registry.is_cooling_down(channel["id"]):
                channel_reports.append({"channel": channel["username"], "ok": False, "items": 0,
                                        "skipped": "cooldown after repeated errors"})
                continue
            cursor = registry.cursor(channel["id"])
            try:
                batch = self._fetch_channel(channel, cursor["last_post_id"])
            except Exception as exc:
                # Never let an exception message (which may quote a URL, a
                # response body, or a header) reach state: only its class.
                kind = type(exc).__name__
                registry.record_failure(channel["id"], kind)
                errors.append(f"{channel['username']}: {kind}")
                channel_reports.append({"channel": channel["username"], "ok": False,
                                        "items": 0, "error": f"{channel['username']}: {kind}"})
                continue
            highest = max((it.raw.get("post_id", 0) for it in batch), default=0)
            if highest > cursor["last_post_id"]:
                registry.advance_cursor(channel["id"], highest)
            items.extend(batch)
            channel_reports.append({"channel": channel["username"], "ok": True,
                                    "items": len(batch), "cursor": max(highest, cursor["last_post_id"])})
        items.sort(key=lambda row: row.published_at, reverse=True)
        details["channels"] = channel_reports
        return Result(self.source, self.category, items[:limit], True, not errors,
                      "; ".join(errors), details)


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


def collect_all(only: list[str] | None = None, tg_registry: IntelligenceRegistry | None = None) -> tuple[list[Item], list[dict]]:
    items: list[Item] = []
    reports: list[dict] = []
    for name, col in registry().items():
        if only and name not in only:
            continue
        # Pass registry to Telegram collector if present
        if name == "telegram" and tg_registry is not None:
            res = col.collect(registry=tg_registry)
        else:
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
