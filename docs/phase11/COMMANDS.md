# NEXARA Phase 11 — World Intelligence CLI & Dashboard Surfaces

The CLI is the primary interface. The dashboard is visibility only: it has no
mutation routes, no controls, and no generic proxy. All existing Phase 10
commands remain unchanged.

## CLI: `nexara-world` (host wrapper: `scripts/nexara`)

### Phase 10 commands (unchanged)
| Command | Description |
|---------|-------------|
| `nexara-world cycle` | Run a collection/analysis cycle now |
| `nexara-world today` | What happened today (observations) |
| `nexara-world matters` | What matters today (strongest multi-source signals) |
| `nexara-world opportunities [--kind]` | What opportunities exist (filter by kind) |
| `nexara-world threats` | What threats exist (risk-language signals) |
| `nexara-world research` | What should we research |
| `nexara-world monitor` | What should we monitor |
| `nexara-world accelerating` | Which technologies are accelerating |
| `nexara-world companies` | Which companies to watch |
| `nexara-world sources` | Source health & configuration |

### Registry management
| Command | Description |
|---------|-------------|
| `nexara-world channels list` | List registered channels |
| `nexara-world channels add <json>` | Upsert a channel (POST `/registry/channels`) |
| `nexara-world channels update <json>` | Alias for add |
| `nexara-world topics list` | List registered topics |
| `nexara-world topics add <json>` | Upsert a topic (POST `/registry/topics`) |
| `nexara-world topics update <json>` | Alias for add |

A rejected write prints the server's stated reason (e.g. `provenance
verification not recognised`) and exits non-zero — never a bare status line.

**Channel JSON:**
```json
{
  "id": "channel-id",
  "username": "telegramusername",
  "title": "Display Title",
  "url": "https://t.me/s/telegramusername",
  "category": "technology",
  "topics": ["technology"],
  "enabled": true,
  "publisher": "publisher-name",
  "description": "Human-readable description",
  "provenance": {
    "primary_url": "https://example.org/source",
    "verified_at": "2026-09-10",
    "verification": "http-200-public-preview"
  }
}
```
`category` must be one of the registry's valid categories, every entry in
`topics` must be a registered topic id, `username` must match the `url` path,
and `verification` must be `http-200-public-preview` or `publisher-linked`.

**Topic JSON:**
```json
{"id": "topic-id", "name": "Topic Name", "keywords": ["keyword1", "keyword2"]}
```

### The eight questions
```
nexara-world ask <question> [--limit N]
```
Each question accepts the natural-language phrase (quoted or bare) or its short
alias. `--limit` is forwarded and bounded 1–100 by the service.

| Question | Alias | HTTP path | Result key |
|----------|-------|-----------|------------|
| What opportunities exist? | `opportunities` | `/ask/opportunities` | `opportunities` |
| Show highest-value opportunities | `highest-value` | `/ask/highest-value` | `opportunities` |
| What should we build? | `build` | `/ask/build` | `opportunities` |
| What should we monitor? | `monitor` | `/ask/monitor` | `watchlist` |
| What communities are growing? | `communities` | `/ask/communities` | `communities` |
| Which companies should we watch? | `companies` | `/ask/companies` | `communities` |
| Which technologies are accelerating? | `accelerating` | `/ask/accelerating` | `entities` |
| What changed today? | `today` | `/ask/today` | `changes` |

All eight are reads: each issues exactly one GET and never mutates stored
intelligence. Answers are printed as full JSON so no score, evidence URL,
provenance flag or null is dropped in formatting. An empty result prints
"No items recorded for this view. This is not evidence of absence."

```bash
nexara-world ask "What should we build?" --limit 5
nexara-world ask build --limit 5          # identical
```

## Dashboard (localhost:8080)

Read-only. Six views, each available as JSON and as an HTML page. Every view is
generated from a single `VIEWS` whitelist in `services/dashboard/main.py`, so a
view cannot be served from a target that was not explicitly allowed.

| View | JSON | HTML | Upstream | Pinned filter | Result key |
|------|------|------|----------|---------------|------------|
| Opportunities | `/api/intelligence/opportunities` | `/intelligence/opportunities` | `/opportunities` | — | `opportunities` |
| Signals | `/api/intelligence/signals` | `/intelligence/signals` | `/signals` | — | `signals` |
| Trends | `/api/intelligence/trends` | `/intelligence/trends` | `/ask/accelerating` | — | `entities` |
| Entities | `/api/intelligence/entities` | `/intelligence/entities` | `/ask/today` | — | `top_activity` |
| Risk signals | `/api/intelligence/risk-signals` | `/intelligence/risk-signals` | `/signals` | `kind=risk` | `signals` |
| Research queue | `/api/intelligence/research-queue` | `/intelligence/research-queue` | `/ask/research` | — | `candidates` |

```bash
curl -s http://localhost:8080/api/intelligence/opportunities | jq .
```

### Safety and honesty properties
- **No mutation routes.** POST/PUT/DELETE/PATCH on `/api/intelligence/*` are not
  registered. Registry writes exist only on world-intelligence (:8086).
- **No generic proxy.** No `/proxy?url=` style route exists.
- **Pinned filters are parameters, not path text.** A view's own filter is
  urlencoded alongside caller params. Concatenating `?kind=risk` onto the path
  and then appending `?limit=5` yields `kind="risk?limit=5"`, which matches no
  row and reports an *empty risk view while risks exist* — a silent false
  negative. `_proxy_intel` rejects any target containing `?`.
- **Blank query values are forwarded, not dropped.** `?limit=` reaches the
  service as blank and is rejected 400. The stdlib parser drops blank values,
  which would silently rewrite a malformed request into a valid one and answer 200.
- **Bounded limits.** `limit` must be one integer 1–100; anything else (`0`,
  `101`, `banana`, blank, duplicated) returns 400 with a stated reason.
- **Escaped untrusted content.** Titles, evidence URLs and provenance are
  HTML-escaped and rendered as text, never interpolated into scripts or `href`
  attributes. A `javascript:` evidence URL is never emitted as a link.
- **Empty ≠ missing ≠ failed.** An empty view says items are absent, not that
  absence is proven. A missing result field says the view cannot confirm
  anything and is a defect. An upstream failure propagates the real status code
  and says it is not an empty result — it never renders as "no items".

## World Intelligence service (localhost:8086)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/registry/channels` | List channels (bounded by `limit`) |
| POST | `/registry/channels` | Upsert channel (internal only; not on dashboard) |
| GET | `/registry/topics` | List topics (bounded by `limit`) |
| POST | `/registry/topics` | Upsert topic (internal only; not on dashboard) |

## Verification

Unit tests (isolated temp `NEXARA_DATA`, never `/data`, no network):
```bash
python -m unittest tests.test_phase11_surfaces -v
```

Live end-to-end check — starts the real world-intelligence and dashboard
services as subprocesses on ports 8199/8198 with fixture intelligence in a temp
directory, then drives every CLI command and every view over real HTTP:
```bash
python scripts/live_surface_check.py
```
It exercises: the eight questions by natural-language phrase, all Phase 10
commands, a registry add→list round-trip plus a rejected invalid write, the six
JSON views (asserting each declared result key exists upstream), the six HTML
pages, mutation refusal on all four verbs, and bounded-limit rejection. It exits
non-zero on any failure and never reports success it did not observe.
