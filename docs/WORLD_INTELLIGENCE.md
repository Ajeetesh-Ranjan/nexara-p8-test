# World Intelligence

Phase 10 turns collected items into understanding: signals that are measured,
opportunities that are labelled as hypotheses, and questions answered from
stored evidence rather than generated prose.

---

## 1. The distinction that governs this subsystem

| Layer | Meaning | Status |
|---|---|---|
| **Observation** | "N sources mentioned X" | Fact — verifiable against stored evidence |
| **Signal** | "X is discussed unusually much" | Measurement — computed against a baseline |
| **Opportunity** | "X may be worth acting on" | **Inference** — `is_hypothesis: true`, confidence ≤ 0.85 |

No opportunity can reach certainty. Confidence is hard-capped at 0.85 in code,
and every opportunity carries an explicit `risks` list stating its own
weaknesses — including the fact that attention volume is not evidence of value.

---

## 2. Pipeline

```
collect → dedupe → extract entities → detect signals → detect convergence
        → generate opportunities → write Brain 2 + Brain 3 → persist baseline
```

Run one cycle:

```bash
docker compose exec world-intelligence \
  sh -c 'cd /app && NEXARA_WI_URL=http://localhost:8086 python scripts/nexara_world.py cycle'
```

Cycles also run automatically every `NEXARA_WI_INTERVAL` seconds (default 3600).

---

## 3. Sources

| Source | Category | Auth | Status |
|---|---|---|---|
| Hacker News | technology | none | live |
| arXiv | research | none | live |
| GitHub | open-source | `GITHUB_TOKEN` optional | live (rate-limited without token) |
| AI news (RSS) | ai | none | live |
| Business (RSS) | business | none | live |
| Dev communities (RSS) | communities | none | live |
| Telegram | communities | `TELEGRAM_BOT_TOKEN` | **requires setup** — see §4 |

A source that cannot be reached returns zero items and records the error. It
never substitutes cached or synthetic content. A source lacking credentials
reports `configured: false`, which is distinct from `ok: false` (broken).

Check source health:

```bash
docker compose exec world-intelligence \
  sh -c 'cd /app && NEXARA_WI_URL=http://localhost:8086 python scripts/nexara_world.py sources'
```

---

## 4. Telegram intelligence setup

Telegram is architecturally wired but inert until you supply a bot token. This
is deliberate: the collector reports `unconfigured` rather than producing
placeholder data.

1. Create a bot with [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token
2. Add the bot to each channel you want to observe, as an **administrator**
   (Telegram only delivers `channel_post` updates to channel admins)
3. Set the token in `.env` — never in chat or in a commit:

```bash
# .env
TELEGRAM_BOT_TOKEN=123456:ABC-your-token
TELEGRAM_CHANNELS=channel_one,channel_two   # optional allow-list; empty = all
```

4. Restart: `docker compose up -d world-intelligence`
5. Verify: the `sources` command should show `telegram ... ok` instead of `unconfigured`

**Noise policy.** Telegram messages pass through the same entity extraction and
multi-source corroboration gates as every other source. A message mentioning
nothing in the lexicon produces no entity, therefore no signal. Raw messages are
never stored as knowledge — only extracted entities and their evidence links.

---

## 5. NEXARA commands

```bash
alias nw="docker compose exec -T world-intelligence sh -c 'cd /app && NEXARA_WI_URL=http://localhost:8086 python scripts/nexara_world.py \"\$@\"' --"

nw today          # What happened today?          (observations)
nw matters        # What matters today?           (multi-source signals)
nw opportunities  # What opportunities exist?     (hypotheses, with risks)
nw threats        # What threats exist?           (risk language near entity)
nw research       # What should we research?      (emits nexara research cmds)
nw monitor        # What should we monitor?       (too weak to act on yet)
nw accelerating   # Which technologies accelerate? (needs ≥2 windows)
nw companies      # Which companies to watch?
nw sources        # Source health and configuration
```

HTTP equivalents live under `/ask/*` on port 8086.

---

## 6. How signals are computed

**Entity extraction** is lexicon + pattern based, not an LLM. Every entity
records the `method` that produced it:

- `lexicon` (weight 1.0) — 87 curated entities, 188 aliases; `MCP` resolves to
  *Model Context Protocol*, `MoE` to *Mixture of Experts*
- `identifier` (0.9) — GitHub `owner/repo` parsed from the URL
- `heuristic` (0.35) — capitalised phrases; **requires ≥2 sources to produce a
  signal**, because this method is the noisy one

**Signal kinds:**

| Kind | Trigger |
|---|---|
| `emergence` | Absent from all prior windows, now in ≥2 sources |
| `momentum` | Mentions exceed the entity's historical mean by ≥1.5× |
| `risk` | Risk language within 120 chars of the entity mention, in ≥2 items or with ≥2 distinct terms |
| `convergence` | Two entities co-occurring in ≥2 of the same items |

The proximity rule on `risk` exists because a document-level match made *Amazon*
inherit an unrelated "shut down" from a different sentence. Risk must be
attributable to the entity, not merely nearby in the same article.

**Baselines** are what make "accelerating" measurable. Each cycle records
per-entity mention counts to `baseline.json`; the next cycle compares against
the stored mean. With fewer than two windows, `accelerating` reports *"not
enough history yet"* rather than inventing a trend.

---

## 7. Brain integration

- **Brain 2** receives each opportunity as a knowledge artifact: thesis, the six
  answered questions, recommendations, risks, cited source URLs, confidence, and
  `is_hypothesis: true`
- **Brain 3** receives entities with their signal state (kind, strength,
  distinct sources) plus the signal stream

Both writes go through `brain-runtime`, which is the single writer for brain
state — the world-intelligence service never writes brain files directly.

---

## 8. Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NEXARA_WI_INTERVAL` | `3600` | Seconds between automatic cycles |
| `NEXARA_WI_AUTOSTART` | `1` | Run a cycle shortly after container start |
| `NEXARA_FETCH_TIMEOUT` | `20` | Per-request timeout (seconds) |
| `GITHUB_TOKEN` | empty | Raises the GitHub search rate limit |
| `TELEGRAM_BOT_TOKEN` | empty | Enables Telegram collection |
| `TELEGRAM_CHANNELS` | empty | Optional channel allow-list |

---

## 9. Known limits

- **Extraction is not semantic.** A technology absent from the lexicon is only
  caught heuristically, and heuristic entities need corroboration to matter.
  Extending `LEXICON` in `entities.py` is the intended way to improve recall.
- **"Emergence" is relative to collection history**, not to the world. A short
  baseline makes everything look new; this caveat is stated in every emergence
  opportunity's `risks`.
- **Discussion volume ≠ importance.** Every opportunity says so explicitly.
- **No financial data is collected**, so `invest`-kind opportunities are signals
  of attention only, never a recommendation.
