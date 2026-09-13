# Phase 11 Opportunity Scoring

## Overview

The `OpportunityEngine.evaluate(signals)` method produces an evaluation for every unique signal ID. Each evaluation is an unvalidated hypothesis — nothing reaches certainty — with confidence hard-capped at **0.85**.

## Contract

`evaluate(signals: list[Signal], min_sources: int = 2) -> list[dict]`

Returns one evaluation per input signal (deduplicated by signal ID). Each evaluation contains:

### Core Fields
- **signal_id** — stable hash of entity + kind, independent of hash seed/time/title
- **id** — evaluation ID (`eval-<hash>`)
- **entity** — entity name
- **entity_type** — protocol | framework | infrastructure | technique | model | company | open-source-project | convergence | unclassified
- **kind** — emergence | momentum | risk | convergence
- **decision** — `shortlist` | `watch` | `reject`
- **reasons** — list of rationale strings
- **questions** — dict with exactly 7 keys: `build`, `learn`, `automate`, `market`, `research`, `revenue`, `improve_nexara`; values are non-empty strings, always framed as hypotheses
- **scores** — 7 integer dimensions (0..100)
- **score_components** — per-dimension breakdown with `base`, `adjustments`, `inputs`, `rationale`
- **evidence** — canonical citations (max 6, one per publisher)
- **confidence** — 0.0..0.85 (float)
- **is_live_data** — `True` iff ALL evidence items have `is_live_data=True`
- **created_at** — unix timestamp
- **next_step** — concrete action string
- **opportunity** — serialized Opportunity dict for corroborated watch/shortlist; `None` for single-source or no evidence
- **rationale** — summary string
- **assumptions** — list of explicit assumptions
- **monetary_estimate** — always `None` (no financial data collected)
- **is_hypothesis** — always `True`
- **risk_band** — `Low` | `Medium` | `High` (derived from risk score)

### Score Dimensions (0..100)

| Dimension | Higher Means | Notes |
|-----------|-------------|-------|
| **impact** | greater potential relevance | keyword-driven, not measured ROI |
| **confidence** | stronger heuristic evidence | capped at 0.85 × 100; not a probability |
| **effort** | **more** effort required (worse) | higher = worse |
| **risk** | **more** risk (worse) | higher = worse |
| **time_to_value** | **faster** to test hypothesis (better) | higher = faster |
| **strategic_alignment** | more overlap with NEXARA research/automation | lexical, not integration-verified |
| **overall** | composite priority | weighted sum |

### Composite Formula

```
overall = round(
    0.20 * impact +
    0.20 * confidence +
    0.15 * (100 - effort) +
    0.15 * (100 - risk) +
    0.10 * time_to_value +
    0.20 * strategic_alignment
)
```

### Shortlist Threshold (Fixed)

- **overall** ≥ 65
- **confidence** ≥ 60
- **risk** ≤ 65 (i.e., risk_band != "High")

Thresholds are documented and never adjusted to make live output pass.

## Publisher Identity

- Distinct publisher count is derived from **canonical citations**, not collector buckets
- Telegram domains (`t.me`, `telegram.me`, `telegram.dog`, `telegram.org`) unify to `telegram.org`
- Canonical URLs dedupe: `https://t.me/botnews/1` and `https://telegram.org/botnews/1` are the **same** post
- Explicit validated publisher identities accepted (e.g., `telegram.org` groups `botnews` + `telegram`)
- Channel alone is fallback only; URL-based post identity takes precedence
- Single-source signals are **WATCH** with low confidence (not fake low risk)

## Convergence

- Only shared URLs create convergence signals
- Lone relevant lexicon/channel signals surface as observations for evaluation
- Convergence signals carry entity type `convergence`, entity `"A + B"`, kind `convergence`

## Legacy `generate()`

`generate(signals, min_sources)` is a legacy adapter that returns `Opportunity` objects built from `evaluate()` results. It filters to only non-None `opportunity` payloads (i.e., corroborated watch/shortlist).

## Assumptions (Always Present)

1. "Discussion volume is not validated value."
2. "Unknown effort and risk are not zero."
3. "No financial data or monetary estimate is available."

## Risk Band Mapping

| Risk Score | Band |
|------------|------|
| ≤ 30 | Low |
| 31–65 | Medium |
| > 65 | High |

## Signal Cap

Inference confidence never exceeds 0.85. Even with perfect evidence, `confidence_score ≤ 85`.