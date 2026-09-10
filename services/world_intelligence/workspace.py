#!/usr/bin/env python3
"""
WORLD INTELLIGENCE WORKSPACE — the operational loop.

  collect -> deduplicate -> extract entities -> detect signals ->
  detect convergence -> generate opportunities -> write Brain 2 + Brain 3 ->
  persist baseline for the next window

Baselines are what make "accelerating" a measurement rather than an adjective:
each run records per-entity mention counts, so the next run can compare against
history instead of asserting novelty.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/app")

from services.common.store import StateStore
from services.world_intelligence import collectors as C
from services.world_intelligence.entities import extract
from services.world_intelligence.engine import SignalEngine, OpportunityEngine

DATA = os.environ.get("NEXARA_DATA", "/data")
WI_DIR = os.path.join(DATA, "world-intelligence")
BRAIN_URL = os.environ.get("NEXARA_BRAIN_URL", "http://brain-runtime:8081")

BASELINE = "baseline.json"
SEEN = "seen_items.json"
RECENT = "recent_items.json"
LAST_RUN = "last_run.json"
SIGNALS = "signals.json"
OPPORTUNITIES = "opportunities.json"

# How long a signal/opportunity stays visible without re-confirmation.
RETENTION_SECONDS = int(os.environ.get("NEXARA_WI_RETENTION", str(48 * 3600)))

# Signals are computed over every item collected within this window, not merely
# the items that arrived in this cycle. An entity still being discussed must not
# disappear just because its article was already seen in a previous cycle.
WINDOW_SECONDS = int(os.environ.get("NEXARA_WI_WINDOW", str(48 * 3600)))
MAX_WINDOW_ITEMS = int(os.environ.get("NEXARA_WI_MAX_ITEMS", "1500"))


def _post(url: str, payload: dict, timeout: int = 20):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


class Workspace:
    def __init__(self, root: str = WI_DIR):
        self.store = StateStore(root)

    # ------------------------------------------------------------- run ----
    def run(self, only: list[str] | None = None, push: bool = True) -> dict:
        started = time.time()

        # 1. collect
        items, reports = C.collect_all(only)
        collected = len(items)

        # 2. deduplicate against prior windows (persistent, bounded)
        seen: dict = self.store.read(SEEN, {})
        fresh = []
        for it in items:
            k = it.key()
            if k in seen:
                continue
            seen[k] = it.fetched_at
            fresh.append(it)
        # keep the dedupe index bounded to ~30 days
        cutoff = time.time() - 30 * 86400
        seen = {k: v for k, v in seen.items() if v > cutoff}
        self.store.write(SEEN, seen)

        # 3. maintain a rolling window of recent items, then extract over the WINDOW.
        #    Fresh items are added; the window is trimmed by age and size.
        window: list[dict] = self.store.read(RECENT, [])
        if not isinstance(window, list):
            window = []
        for it in fresh:
            window.append({"title": it.title, "url": it.url, "source": it.source,
                           "category": it.category, "text": it.text[:600],
                           "published_at": it.published_at, "fetched_at": it.fetched_at,
                           "key": it.key()})
        wcut = time.time() - WINDOW_SECONDS
        seen_keys: set[str] = set()
        deduped = []
        for row in sorted(window, key=lambda r: r.get("fetched_at", 0), reverse=True):
            k = row.get("key") or row.get("url")
            if k in seen_keys:
                continue
            if row.get("fetched_at", 0) < wcut:
                continue
            seen_keys.add(k)
            deduped.append(row)
        window = deduped[:MAX_WINDOW_ITEMS]
        self.store.write(RECENT, window)

        observations = []
        for row in window:
            for e in extract(f"{row['title']}. {row.get('text','')}", row.get("url", "")):
                observations.append({
                    "entity": e.name, "type": e.type, "method": e.method,
                    "weight": e.weight, "item": row,
                })

        # 4. signals, measured against the stored baseline
        baseline: dict = self.store.read(BASELINE, {})
        sig_engine = SignalEngine(baseline)
        signals = sig_engine.detect(observations)
        signals += sig_engine.detect_convergence(signals)

        # 5. opportunities (hypotheses, corroboration required)
        opportunities = OpportunityEngine().generate(signals)

        # 6. update baseline for the next window
        counts: dict[str, int] = {}
        for o in observations:
            counts[o["entity"]] = counts.get(o["entity"], 0) + 1
        for entity, n in counts.items():
            b = baseline.get(entity, {"mean_mentions": 0.0, "windows": 0, "total": 0})
            b["total"] = b.get("total", 0) + n
            b["windows"] = b.get("windows", 0) + 1
            b["mean_mentions"] = round(b["total"] / b["windows"], 3)
            b["last_seen"] = time.time()
            baseline[entity] = b
        self.store.write(BASELINE, baseline)

        # 7. persist signals + opportunities.
        #    A window with zero new items must never erase prior intelligence:
        #    merge on id and age out by TTL instead of clobbering.
        sig_dicts = [s.to_dict() for s in signals]
        opp_dicts = [o.to_dict() for o in opportunities]
        sig_dicts = self._merge_retained(SIGNALS, sig_dicts, "last_seen")
        opp_dicts = self._merge_retained(OPPORTUNITIES, opp_dicts, "created_at")
        self.store.write(SIGNALS, sig_dicts)
        self.store.write(OPPORTUNITIES, opp_dicts)

        # 8. push into Brain 2 / Brain 3
        pushed = {"brain2": 0, "brain3": 0, "errors": []}
        if push:
            pushed = self._push(sig_dicts, opp_dicts, observations)

        summary = {
            "started_at": started,
            "duration_seconds": round(time.time() - started, 1),
            "sources": reports,
            "collected": collected,
            "new_items": len(fresh),
            "window_items": len(window),
            "observations": len(observations),
            "distinct_entities": len(counts),
            "signals": len(sig_dicts),
            "signals_by_kind": self._by(sig_dicts, "kind"),
            "opportunities": len(opp_dicts),
            "opportunities_by_kind": self._by(opp_dicts, "kind"),
            "pushed": pushed,
            "baseline_entities": len(baseline),
        }
        self.store.write(LAST_RUN, summary)
        return summary

    def _merge_retained(self, name: str, fresh: list[dict], ts_field: str) -> list[dict]:
        """
        Merge freshly computed rows with previously stored ones.

        Fresh rows win on id collision (they carry the newest evidence). Prior
        rows survive until RETENTION_SECONDS old, so a dedupe-empty window keeps
        showing the last real intelligence rather than going blank.
        """
        prior = self.store.read(name, [])
        if not isinstance(prior, list):
            prior = []
        cutoff = time.time() - RETENTION_SECONDS
        merged: dict[str, dict] = {}
        for row in prior:
            ts = row.get(ts_field) or row.get("created_at") or 0
            if isinstance(ts, (int, float)) and ts >= cutoff:
                merged[row.get("id", repr(row))] = {**row, "retained": True}
        for row in fresh:
            merged[row.get("id", repr(row))] = row
        out = list(merged.values())
        out.sort(key=lambda r: (r.get("strength", r.get("confidence", 0)),
                                r.get("distinct_sources", 0)), reverse=True)
        return out

    @staticmethod
    def _by(rows: list[dict], key: str) -> dict:
        out: dict[str, int] = {}
        for r in rows:
            out[r[key]] = out.get(r[key], 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def _push(self, signals: list[dict], opportunities: list[dict],
              observations: list[dict]) -> dict:
        result = {"brain2": 0, "brain3": 0, "errors": []}

        # Brain 2: opportunities are knowledge artifacts with evidence
        for o in opportunities[:25]:
            try:
                _post(f"{BRAIN_URL}/brain2", {
                    "type": "world_intelligence_opportunity",
                    "artifact_id": o["id"],
                    "query": o["title"],
                    "summary": o["thesis"],
                    "key_findings": o["questions"],
                    "insights": [f"kind={o['kind']}", f"entities={', '.join(o['entities'])}"],
                    "recommendations": [o["next_step"]] + o["risks"],
                    "sources": [e["url"] for e in o["evidence"]],
                    "confidence": o["confidence"],
                    "is_hypothesis": True,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                result["brain2"] += 1
            except Exception as e:
                result["errors"].append(f"brain2 {o['id']}: {e}")
                break

        # Brain 3: entities and their signal state
        ents = {}
        for s in signals:
            for name in (s["entity"].split(" + ") if s["kind"] == "convergence" else [s["entity"]]):
                ents[name] = {
                    "source": "world_intelligence",
                    "entity_type": s["entity_type"],
                    "signal_kind": s["kind"],
                    "strength": s["strength"],
                    "distinct_sources": s["distinct_sources"],
                }
        if ents:
            try:
                r = _post(f"{BRAIN_URL}/brain3", {
                    "entities": ents,
                    "signals": [{"id": s["id"], "entity": s["entity"], "kind": s["kind"],
                                 "strength": s["strength"], "ts": s["last_seen"]}
                                for s in signals[:50]],
                })
                result["brain3"] = r.get("added", 0)
            except Exception as e:
                result["errors"].append(f"brain3: {e}")
        return result

    # --------------------------------------------------------- queries ----
    def signals(self, kind: str | None = None, limit: int = 20) -> list[dict]:
        rows = self.store.read(SIGNALS, [])
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        return rows[:limit]

    def opportunities(self, kind: str | None = None, limit: int = 20) -> list[dict]:
        rows = self.store.read(OPPORTUNITIES, [])
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        return rows[:limit]

    def accelerating(self, limit: int = 15) -> list[dict]:
        """Entities whose current mentions exceed their historical mean."""
        baseline = self.store.read(BASELINE, {})
        rows = []
        for entity, b in baseline.items():
            if b.get("windows", 0) < 2:
                continue
            rows.append({
                "entity": entity,
                "mean_mentions": b["mean_mentions"],
                "windows": b["windows"],
                "total_mentions": b.get("total", 0),
            })
        rows.sort(key=lambda r: (r["mean_mentions"], r["total_mentions"]), reverse=True)
        return rows[:limit]

    def last_run(self) -> dict:
        return self.store.read(LAST_RUN, {})


if __name__ == "__main__":
    ws = Workspace(os.environ.get("NEXARA_WI_DIR", "/tmp/wi-test"))
    s = ws.run(push=False)
    print(json.dumps({k: v for k, v in s.items() if k != "sources"}, indent=2))
    for r in s["sources"]:
        print(f"  {r['source']:16} {r['items']:4} items  ok={r['ok']} configured={r['configured']}")
