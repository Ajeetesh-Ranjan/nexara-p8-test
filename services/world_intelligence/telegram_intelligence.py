"""
Read-only public Telegram intelligence: channel/topic registry, fail-closed
allow-list, replay cursors, and privacy redaction.

Design rules enforced here (Phase 11):
  - Fail closed. A persisted channel row is re-validated on every read; a row
    that no longer passes validation is quarantined, never fetched.
  - Bounded signals, not archives. Excerpts are capped, message counts are
    capped, and cursors mean a replayed window yields nothing new.
  - No credentials. Nothing in this module reads a token from the environment
    or stores one. Dedicated-bot polling is a registry-level policy that fails
    closed and reports why, rather than a secret-consuming code path.
  - No posting, joining, or private-message access. Only the public
    https://t.me/s/<username> preview is ever addressed.
"""
from copy import deepcopy
import re
import time

from services.common.store import StateStore

REGISTRY_FILE = "telegram_registry.json"
CURSOR_FILE = "telegram_cursors.json"

DEFAULT_TOPICS = [
    {"id": "open-source", "name": "Open source", "keywords": ["open source", "github", "release"]},
    {"id": "developer-communities", "name": "Developer communities", "keywords": ["developer", "api", "sdk"]},
    {"id": "ai", "name": "AI", "keywords": ["artificial intelligence", "llm", "ai", "agents"]},
    {"id": "startups", "name": "Startups", "keywords": ["startup", "funding", "founder"]},
    {"id": "business", "name": "Business", "keywords": ["business", "revenue", "acquisition"]},
    {"id": "technology", "name": "Technology", "keywords": ["software", "technology", "update"]},
    {"id": "research", "name": "Research", "keywords": ["research", "paper", "arxiv", "benchmark"]},
]
# Verified by read-only HTTP on 2026-09-10; not a claim of continuing reachability.
DEFAULT_CHANNELS = [
    {"id": "telegram", "username": "telegram", "title": "Telegram News",
     "url": "https://t.me/s/telegram", "category": "technology",
     "topics": ["technology"], "enabled": True, "publisher": "telegram.org",
     "description": "Official Telegram product announcements; not independent of BotNews.",
     "provenance": {"primary_url": "https://telegram.org/faq",
                    "verified_at": "2026-09-10", "verification": "http-200-public-preview"}},
    {"id": "botnews", "username": "botnews", "title": "BotNews",
     "url": "https://t.me/s/botnews", "category": "developer-communities",
     "topics": ["developer-communities", "technology"], "enabled": True,
     "publisher": "telegram.org", "description": "Official Telegram Bot API release announcements.",
     "provenance": {"primary_url": "https://core.telegram.org/bots/api",
                    "verified_at": "2026-09-10", "verification": "http-200-public-preview"}},
    {"id": "pythontelegrambotchannel", "username": "pythontelegrambotchannel",
     "title": "python-telegram-bot", "url": "https://t.me/s/pythontelegrambotchannel",
     "category": "open-source", "topics": ["open-source", "developer-communities"],
     "enabled": True, "publisher": "python-telegram-bot.org",
     "description": "Public release channel linked by the python-telegram-bot project README.",
     "provenance": {"primary_url": "https://raw.githubusercontent.com/python-telegram-bot/python-telegram-bot/master/README.rst",
                    "verified_at": "2026-09-10", "verification": "http-200-public-preview"}},
]

# Dedicated-bot polling is declared here so the answer to "is it on?" is a
# stored decision with a reason, not an undocumented absence.
DEFAULT_BOT_POLLING = {
    "enabled": False,
    "mode": "dedicated-bot",
    "reason": "Phase 11 implements no credential handling; a dedicated bot token "
              "has no supply path, so polling stays off.",
}

VALID_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,31}$")
VALID_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
VALID_URL_RE = re.compile(r"^https://t\.me/s/[A-Za-z0-9_]{3,}$")
VALID_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VALID_CATEGORIES = {"open-source", "developer-communities", "ai", "startups", "business", "technology", "research"}
VALID_VERIFICATIONS = {"http-200-public-preview", "publisher-linked"}

# Only these keys survive into persisted state. Anything else a caller sends
# (headers, tokens, proxies, callbacks) is dropped before it can be stored.
CHANNEL_KEYS = ("id", "username", "title", "url", "category", "topics",
                "enabled", "publisher", "description", "provenance")
TOPIC_KEYS = ("id", "name", "keywords")
PROVENANCE_KEYS = ("primary_url", "verified_at", "verification")

MAX_CHANNELS = 20
MAX_TOPICS = 50
MAX_TEXT = 400
MAX_TOPICS_PER_CHANNEL = 6
MAX_KEYWORDS = 24

# Anything that smells like a credential must never reach disk.
SECRET_RE = re.compile(
    r"(\d{6,12}:[A-Za-z0-9_-]{20,}"          # Telegram bot token shape
    r"|(?i:token|secret|api[_-]?key|password|bearer)\s*[=:]"
    r"|[?&](?i:token|key|auth|access_token)=)"
)

# Privacy redaction applied to every excerpt before it leaves the collector.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_HANDLE_RE = re.compile(r"(?<![\w/])@[A-Za-z][A-Za-z0-9_]{3,31}\b")
_PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{7,17}\d(?![\w.])")
_INVITE_RE = re.compile(r"(?i)\b(?:https?://)?t\.me/(?:joinchat/|\+)[\w-]+")
_TME_RE = re.compile(r"(?i)\b(?:https?://)?t\.me/[\w+/-]+")


def _is_secretish(value: str) -> bool:
    return bool(SECRET_RE.search(value))


def _clean_str(row: dict, key: str, *, max_len: int = MAX_TEXT, required: bool = True) -> str:
    value = row.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    value = " ".join(value.split())
    if required and not value:
        raise ValueError(f"{key} required")
    if len(value) > max_len:
        raise ValueError(f"{key} exceeds {max_len} characters")
    if _is_secretish(value):
        raise ValueError(f"{key} appears to contain a credential")
    return value


def redact(text: str) -> str:
    """Strip personal identifiers and private-invite links from a public excerpt.

    Public channel posts still quote user handles, emails and phone numbers.
    Those identify people who never opted into this pipeline, so they are
    removed before storage — the signal survives, the identifier does not.
    """
    if not text:
        return ""
    text = _EMAIL_RE.sub("[email]", text)
    text = _INVITE_RE.sub("[invite-link]", text)
    text = _TME_RE.sub("[telegram-link]", text)
    text = _HANDLE_RE.sub("[handle]", text)
    text = _PHONE_RE.sub("[number]", text)
    return " ".join(text.split())


def validate_channel(data: dict, valid_topic_ids: set[str]) -> dict:
    """Return a normalized channel row, or raise ValueError. Never mutates input."""
    if not isinstance(data, dict):
        raise ValueError("channel must be an object")
    row = {k: data[k] for k in CHANNEL_KEYS if k in data}

    if not isinstance(row.get("id"), str) or not VALID_ID_RE.match(row.get("id") or ""):
        raise ValueError("invalid channel id")
    if not isinstance(row.get("username"), str) or not VALID_USERNAME_RE.match(row.get("username") or ""):
        raise ValueError("invalid username")
    if not isinstance(row.get("url"), str) or not VALID_URL_RE.match(row.get("url") or ""):
        raise ValueError("invalid url")
    # username must match url path — the allow-list is the URL, not a label.
    if row["username"] != row["url"].rsplit("/", 1)[-1]:
        raise ValueError("username does not match url")

    if row.get("category") not in VALID_CATEGORIES:
        raise ValueError("invalid category")

    topics = row.get("topics")
    if not isinstance(topics, list) or not topics:
        raise ValueError("topics must be a non-empty list")
    if len(topics) > MAX_TOPICS_PER_CHANNEL:
        raise ValueError("too many topics for one channel")
    for topic in topics:
        if not isinstance(topic, str) or topic not in valid_topic_ids:
            raise ValueError(f"unknown topic: {topic!r}")
    row["topics"] = list(dict.fromkeys(topics))

    if not isinstance(row.get("enabled"), bool):
        raise ValueError("enabled must be bool")

    row["title"] = _clean_str(row, "title", max_len=120)
    row["publisher"] = _clean_str(row, "publisher", max_len=120)
    if "=" in row["publisher"] or "?" in row["publisher"]:
        raise ValueError("publisher appears to contain tokens")
    row["description"] = _clean_str(row, "description")

    provenance = row.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("provenance must be dict")
    prov = {k: provenance[k] for k in PROVENANCE_KEYS if k in provenance}
    for key in PROVENANCE_KEYS:
        if key not in prov:
            raise ValueError(f"provenance missing {key}")
    primary = _clean_str(prov, "primary_url", max_len=400)
    if not primary.startswith("https://"):
        raise ValueError("provenance primary_url must be https")
    if not VALID_DATE_RE.match(str(prov.get("verified_at", ""))):
        raise ValueError("provenance verified_at must be YYYY-MM-DD")
    if prov.get("verification") not in VALID_VERIFICATIONS:
        raise ValueError("provenance verification not recognised")
    row["provenance"] = {"primary_url": primary, "verified_at": prov["verified_at"],
                         "verification": prov["verification"]}
    return row


def validate_topic(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("topic must be an object")
    row = {k: data[k] for k in TOPIC_KEYS if k in data}
    if not isinstance(row.get("id"), str) or not VALID_ID_RE.match(row.get("id") or ""):
        raise ValueError("invalid topic id")
    row["name"] = _clean_str(row, "name", max_len=120)
    keywords = row.get("keywords", [])
    if not isinstance(keywords, list):
        raise ValueError("keywords must be list")
    if len(keywords) > MAX_KEYWORDS:
        raise ValueError("too many keywords")
    cleaned = []
    for kw in keywords:
        if not isinstance(kw, str) or not kw.strip():
            raise ValueError("keywords must be nonempty strings")
        if _is_secretish(kw):
            raise ValueError("keyword appears to contain a credential")
        cleaned.append(" ".join(kw.split())[:60])
    row["keywords"] = list(dict.fromkeys(cleaned))
    return row


class IntelligenceRegistry:
    """Persist channel configuration beneath the caller's World Intelligence root."""

    # A channel that keeps failing is parked rather than retried every cycle.
    ERROR_THRESHOLD = 5
    COOLDOWN_SECONDS = 3600

    def __init__(self, root):
        self.store = StateStore(str(root))
        if not self.store.exists(REGISTRY_FILE):
            self.store.update(REGISTRY_FILE, lambda current: current, deepcopy({
                "version": 1, "channels": DEFAULT_CHANNELS, "topics": DEFAULT_TOPICS,
                "bot_polling": DEFAULT_BOT_POLLING,
            }))

    # ------------------------------------------------------------ reads ----
    def topics(self) -> list[dict]:
        return self.store.read(REGISTRY_FILE).get("topics", [])

    def channels(self) -> list[dict]:
        return self.store.read(REGISTRY_FILE, {"channels": []}).get("channels", [])

    def enabled_channels(self) -> tuple[list[dict], list[dict]]:
        """Fail-closed allow-list: (fetchable rows, quarantined rows with reasons).

        Persisted state is not trusted. Every row is re-validated against the
        current rules before it can be fetched, so a registry edited by hand, an
        older schema, or a partially applied write can only ever shrink what the
        collector reaches — never widen it.
        """
        valid_topic_ids = {t["id"] for t in self.topics() if isinstance(t, dict) and "id" in t}
        allowed: list[dict] = []
        quarantined: list[dict] = []
        seen_ids: set[str] = set()
        for raw in self.channels():
            ident = raw.get("id") if isinstance(raw, dict) else None
            label = ident if isinstance(ident, str) and VALID_ID_RE.match(ident) else "<malformed>"
            try:
                row = validate_channel(raw, valid_topic_ids)
            except (ValueError, TypeError, AttributeError) as exc:
                quarantined.append({"id": label, "reason": str(exc)})
                continue
            if row["id"] in seen_ids:
                quarantined.append({"id": label, "reason": "duplicate channel id"})
                continue
            seen_ids.add(row["id"])
            if not row["enabled"]:
                continue
            if len(allowed) >= MAX_CHANNELS:
                quarantined.append({"id": label, "reason": f"exceeds MAX_CHANNELS={MAX_CHANNELS}"})
                continue
            allowed.append(row)
        return allowed, quarantined

    def channels_for_topic(self, topic_id: str) -> list[dict]:
        allowed, _ = self.enabled_channels()
        return [c for c in allowed if topic_id in c["topics"]]

    def channels_for_category(self, category: str) -> list[dict]:
        allowed, _ = self.enabled_channels()
        return [c for c in allowed if c["category"] == category]

    # ----------------------------------------------------------- writes ----
    def upsert_channel(self, data: dict) -> dict:
        row = validate_channel(data, {t["id"] for t in self.topics()})

        def update(current):
            others = [c for c in current.get("channels", []) if c.get("id") != row["id"]]
            if len(others) + 1 > MAX_CHANNELS:
                raise ValueError(f"registry limited to {MAX_CHANNELS} channels")
            current["channels"] = others + [row]
            return current

        self.store.update(REGISTRY_FILE, update, {"channels": []})
        return row

    def upsert_topic(self, data: dict) -> dict:
        row = validate_topic(data)

        def update(current):
            others = [t for t in current.get("topics", []) if t.get("id") != row["id"]]
            if len(others) + 1 > MAX_TOPICS:
                raise ValueError(f"registry limited to {MAX_TOPICS} topics")
            current["topics"] = others + [row]
            return current

        self.store.update(REGISTRY_FILE, update, {"topics": []})
        return row

    # ----------------------------------------------------- bot polling -----
    def bot_polling(self) -> dict:
        stored = self.store.read(REGISTRY_FILE, {}).get("bot_polling")
        return dict(stored) if isinstance(stored, dict) else dict(DEFAULT_BOT_POLLING)

    def bot_polling_decision(self, credential_present: bool = False) -> dict:
        """Decide whether dedicated-bot polling may run. Fails closed, with a reason.

        `credential_present` is a caller-supplied fact, never read from the
        environment here: this module has no business touching secrets. Phase 11
        supplies no credential channel, so the answer is always no — but the
        refusal is explicit and inspectable rather than silent.
        """
        config = self.bot_polling()
        if not config.get("enabled"):
            return {"active": False, "mode": config.get("mode", "dedicated-bot"),
                    "reason": config.get("reason") or "disabled in registry"}
        if not credential_present:
            return {"active": False, "mode": config.get("mode", "dedicated-bot"),
                    "reason": "enabled in registry but no dedicated bot credential was supplied; "
                              "Phase 11 handles no credentials, so polling stays off"}
        return {"active": False, "mode": config.get("mode", "dedicated-bot"),
                "reason": "dedicated bot polling is not implemented in Phase 11; "
                          "public channel previews are the only ingestion path"}

    # --------------------------------------------------------- cursors -----
    def cursor(self, channel_id: str) -> dict:
        cursors = self.store.read(CURSOR_FILE, {"channels": {}}).get("channels", {})
        row = cursors.get(channel_id)
        base = {"last_post_id": 0, "last_success_at": 0.0, "consecutive_errors": 0,
                "last_error_kind": "", "last_error_at": 0.0}
        if isinstance(row, dict):
            for key in base:
                if key in row and isinstance(row[key], type(base[key])):
                    base[key] = row[key]
            if isinstance(row.get("last_post_id"), int):
                base["last_post_id"] = row["last_post_id"]
        return base

    def _mutate_cursor(self, channel_id: str, fn) -> dict:
        if not VALID_ID_RE.match(channel_id or ""):
            raise ValueError("invalid channel id")
        updated: dict = {}

        def update(current):
            nonlocal updated
            channels = current.setdefault("channels", {})
            updated = fn(dict(self.cursor(channel_id)))
            channels[channel_id] = updated
            # Bounded: cursors only exist for channels still in the registry.
            known = {c.get("id") for c in self.channels() if isinstance(c, dict)}
            current["channels"] = {k: v for k, v in channels.items() if k in known}
            current["version"] = 1
            return current

        self.store.update(CURSOR_FILE, update, {"channels": {}})
        return updated

    def advance_cursor(self, channel_id: str, last_post_id: int, *, now: float | None = None) -> dict:
        """Record the highest post id ingested. Monotonic: replays never rewind it."""
        now = time.time() if now is None else now
        post_id = int(last_post_id)

        def fn(row):
            row["last_post_id"] = max(row["last_post_id"], post_id)
            row["last_success_at"] = now
            row["consecutive_errors"] = 0
            row["last_error_kind"] = ""
            return row

        return self._mutate_cursor(channel_id, fn)

    def record_failure(self, channel_id: str, kind: str, *, now: float | None = None) -> dict:
        """Record an error class only — never a message, URL, or response body."""
        now = time.time() if now is None else now
        safe_kind = re.sub(r"[^A-Za-z0-9_.-]", "", str(kind))[:64] or "Error"

        def fn(row):
            row["consecutive_errors"] += 1
            row["last_error_kind"] = safe_kind
            row["last_error_at"] = now
            return row

        return self._mutate_cursor(channel_id, fn)

    def is_cooling_down(self, channel_id: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        row = self.cursor(channel_id)
        return (row["consecutive_errors"] >= self.ERROR_THRESHOLD
                and now - row["last_error_at"] < self.COOLDOWN_SECONDS)
