# Telegram Intelligence — channel/topic registry and selective ingestion

Phase 11, task `t_69505ac8`. Scope of this document: what the Telegram path
collects, what it refuses to do, and how to inspect or change it.

Modules: `services/world_intelligence/telegram_intelligence.py` (registry,
validation, cursors, redaction), `services/world_intelligence/collectors.py`
(the `Telegram` collector), `services/world_intelligence/registry.py`
(re-export used by the service).

## What this does NOT do

These are properties of the code, covered by tests in
`tests/test_phase11_telegram.py`, not promises:

- **No credentials.** Neither module reads a token from the environment or
  stores one. `test_no_module_path_reads_credentials_from_the_environment`
  asserts the strings `TELEGRAM_BOT_TOKEN`, `api.telegram.org`, `getUpdates`
  and `sendMessage` do not appear in either file.
- **No posting, joining, or DMs.** The only URL shape ever requested is
  `https://t.me/s/<username>` — the public preview page.
  `test_only_public_preview_urls_are_ever_requested` asserts every outbound
  request matches that pattern.
- **No new service or process.** This runs inside the existing
  world-intelligence service on its existing cycle.
- **No dedicated-bot polling.** It is a stored decision that fails closed, not
  an omission — see below.

## Fail-closed allow-list

`IntelligenceRegistry.enabled_channels()` returns `(allowed, quarantined)`.
Persisted state is **not** trusted: every stored row is re-validated against the
current rules on every read. A row edited by hand, written by an older schema,
or left half-applied can only ever shrink what the collector reaches.

A quarantined row is reported with a reason and never fetched. Quarantine
triggers include: non-`t.me/s/` URL, username not matching the URL path,
unknown category, unknown topic, non-boolean `enabled`, non-https or malformed
provenance, duplicate channel id, and exceeding `MAX_CHANNELS` (20).

The collector also fails closed when it has no registry at all: `configured`
is `False`, `items` is empty, and the error says so. A collector that cannot
verify what it is allowed to read reads nothing.

### Stored shape

Only these keys survive a write — anything else a caller sends (headers,
proxies, tokens, callbacks) is dropped before it reaches disk:

```json
{
  "id": "botnews",
  "username": "botnews",
  "title": "BotNews",
  "url": "https://t.me/s/botnews",
  "category": "developer-communities",
  "topics": ["developer-communities", "technology"],
  "enabled": true,
  "publisher": "telegram.org",
  "description": "Official Telegram Bot API release announcements.",
  "provenance": {
    "primary_url": "https://core.telegram.org/bots/api",
    "verified_at": "2026-09-10",
    "verification": "http-200-public-preview"
  }
}
```

Free-text fields are additionally rejected if they look like a credential
(`token=`, `api_key:`, a `123456:AA...` bot-token shape, a `?token=` query).

Topics: `{"id": "...", "name": "...", "keywords": [...]}`. A channel may only
reference topic ids that exist; categories are a fixed set
(`open-source`, `developer-communities`, `ai`, `startups`, `business`,
`technology`, `research`).

Seeded defaults are three publisher-linked public channels, each carrying the
URL that was used to verify it. `verified_at: 2026-09-10` records when that
check happened — it is not a claim of continuing reachability.

## Bounded signals, not archives

- Per-channel cursor (`telegram_cursors.json`) stores `last_post_id`. Posts at
  or below it are skipped **during parsing**, so a replayed window yields
  nothing new — verified by `test_replaying_the_same_preview_yields_no_new_items`.
- The cursor is monotonic: `advance_cursor(id, 500)` then `advance_cursor(id, 200)`
  leaves it at 500. Replays cannot rewind it and re-ingest history.
- Caps: 20 channels, 25 messages per channel per cycle, 300-character excerpts,
  512 KB per response. A 120-post preview yields at most 25 items.
- Cursors are pruned to channels still in the registry, so removing a channel
  removes its state.

## Privacy

Public posts still quote people who never opted into this pipeline, so every
excerpt is redacted **before** truncation (so a half-cut identifier cannot
survive): emails → `[email]`, `@handles` → `[handle]`, phone numbers →
`[number]`, `t.me/joinchat/...` and `t.me/+...` → `[invite-link]`, other
`t.me/` links → `[telegram-link]`. The substantive signal survives; the
identifier does not.

## Errors

- Only the **exception class** is ever recorded — never the message, URL, or
  response body, which may quote a query string. `TimeoutError`, not
  `TimeoutError: https://t.me/s/x?token=...`.
- One failing channel does not suppress the others; the run reports
  `ok=False` with per-channel detail rather than pretending success.
- After 5 consecutive failures a channel enters a 1-hour cooldown and is
  skipped instead of hammered. Any success resets the counter.

## Dedicated-bot polling

`bot_polling` is stored in the registry, defaulting to:

```json
{"enabled": false, "mode": "dedicated-bot",
 "reason": "Phase 11 implements no credential handling; a dedicated bot token has no supply path, so polling stays off."}
```

`bot_polling_decision()` returns `{"active": false, "reason": ...}` in every
reachable case — including when an operator flips `enabled` to true, because no
credential channel exists to make it work. The point is that "is it on?" has a
stored, inspectable answer with a reason, rather than being silently absent.
The decision is surfaced in the collector result under
`details["bot_polling"]`.

## Inspecting and changing the registry

```bash
curl -s localhost:8086/registry/channels | jq .
curl -s localhost:8086/registry/topics   | jq .
```

Writes go through `POST /registry/channels` and `POST /registry/topics` on the
world-intelligence service (not exposed by the dashboard). Both run the same
validation as any other write; an invalid row returns HTTP 400 and changes
nothing.

## Tests

```bash
python3 -m unittest tests.test_phase11_telegram -v
```

32 tests, all offline. Fixture HTML is never treated as live evidence:
`_parse_messages` marks `is_live_data` False unless the bytes came from an
actual fetch.
