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
import fcntl
import re
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/app")

from services.common.store import StateStore
from services.world_intelligence import collectors as C
from services.world_intelligence.telegram_intelligence import IntelligenceRegistry as TelegramRegistry
from services.world_intelligence.entities import extract
from services.world_intelligence.engine import SignalEngine, OpportunityEngine, _canonical_url, _stable_id
from services.world_intelligence.history import TrendHistory

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

    def _snapshot(self):
        return self.store.read("snapshot.json", {})

    def run(self, only: list[str] | None = None, push: bool = True) -> dict:
        # A separate cycle lock prevents CLI and timer runs from losing updates.
        with open(self.store.path("cycle.lock"), "w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return self._run(only, push)

    def _run(self, only, push):
        started = time.time()
        registry = TelegramRegistry(self.store.root)
        items, reports = C.collect_all(only, tg_registry=registry)
        prior = self._snapshot()
        seen = {k: ts for k, ts in prior.get("seen", {}).items() if ts >= started - 30 * 86400}
        window = {r["key"]: r for r in prior.get("items", []) if r.get("observed_at", 0) >= started - WINDOW_SECONDS}
        fresh, discarded = [], 0
        for item in items:
            key = _canonical_url(item.url)
            if not key or key in seen:
                continue
            seen[key] = started
            row = self._prepare(item, registry, started)
            if row is None:
                discarded += 1
                continue
            row["key"] = key
            window[key] = row
            fresh.append(row)
        window = sorted(window.values(), key=lambda r: r["observed_at"], reverse=True)[:MAX_WINDOW_ITEMS]
        observations = self._observations(window)
        # Record this window's observations into the non-overlapping trend history
        # so trend computation is replay-safe (deduplicates seen URLs per entity).
        history = TrendHistory(self.store)
        if observations:
            history.observe(observations, reports, now=started)
        trends = history.trends(now=started) if observations else []
        sig_engine = SignalEngine({})  # trend measurement is separate, never overlapping baseline means
        signals = sig_engine.detect(observations)
        signals += sig_engine.detect_convergence(signals)
        signals = list({s.to_dict()["id"]: s for s in signals}.values())
        evaluations = OpportunityEngine().evaluate(signals)
        evaluations.sort(key=lambda e: (-e["scores"]["overall"], e["signal_id"]))
        opportunities = [e["opportunity"] for e in evaluations if e.get("opportunity")]
        sig_dicts = [s.to_dict() for s in signals]
        summary = {
            "schema_version": 11, "started_at": started,
            "duration_seconds": round(time.time() - started, 2), "sources": reports,
            "collected": len(items), "new_items": len(fresh), "noise_discarded": discarded,
            "window_items": len(window), "observations": len(observations),
            "distinct_entities": len({o["entity"] for o in observations}),
            "signals": len(signals), "signals_by_kind": self._by(sig_dicts, "kind"),
            "evaluations": len(evaluations), "decisions": self._by(evaluations, "decision"),
            "opportunities": len(opportunities), "opportunities_by_kind": self._by(opportunities, "kind"),
            "trends": len(trends),
            "pushed": {"brain2": 0, "brain3": 0, "errors": []},
        }
        snapshot = {"version": 11, "seen": dict(sorted(seen.items(), key=lambda x: x[1], reverse=True)[:50000]),
                    "items": window, "signals": sig_dicts, "opportunities": opportunities,
                    "evaluations": evaluations, "trends": [t.to_dict() for t in trends],
                    "last_run": summary}
        self.store.write("snapshot.json", snapshot)
        if push:
            summary["pushed"] = self._push(sig_dicts, opportunities, observations, trends)
            self.store.write("snapshot.json", snapshot)
        return summary

    @staticmethod
    def _prepare(item, registry, observed_at):
        text = " ".join(re.sub(r"<[^>]+>", " ", item.text).split())
        if len(text) < 30 or C.Telegram()._is_noise(text):
            return None
        topics = [t["id"] for t in registry.topics() if any(
            re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", text, re.I) for k in t.get("keywords", []))]
        entities = extract(f"{item.title}. {text}", item.url, max_heuristic=0)
        if not entities and not topics:
            return None
        raw = item.raw
        return {"title": item.title[:160], "url": item.url, "source": item.source,
                "category": item.category, "text": text[:300], "published_at": item.published_at,
                "fetched_at": item.fetched_at, "observed_at": observed_at,
                "is_live_data": raw.get("is_live_data", True) is True,
                "topics": sorted(set(topics + raw.get("topics", []))),
                "channel": raw.get("channel", ""), "channel_id": raw.get("channel_id", ""),
                "publisher": raw.get("publisher", ""),
                "entity_refs": [{"name": e.name, "type": e.type, "method": e.method} for e in entities]}

    @staticmethod
    def _observations(rows):
        observations = []
        for row in rows:
            refs = list(row.get("entity_refs", []))
            if row.get("channel"):
                refs.append({"name": "channel:" + row["channel"], "type": "channel", "method": "registry"})
            if not refs and row.get("topics"):
                refs.append({"name": "topic:" + row["topics"][0], "type": "topic", "method": "registry"})
            observations.extend({"entity": r["name"], "type": r["type"], "method": r["method"],
                                 "weight": 1.0, "item": row} for r in refs)
        return observations

    @staticmethod
    def _by(rows: list[dict], key: str) -> dict:
        out: dict[str, int] = {}
        for r in rows:
            out[r[key]] = out.get(r[key], 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def _push(self, signals: list[dict], opportunities: list[dict],
              observations: list[dict], trends: list) -> dict:
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

        # Brain 3: entities with roles, relationships, and trends
        ents = {}
        roles = {}  # entity -> set of roles
        for s in signals:
            for name in (s["entity"].split(" + ") if s["kind"] == "convergence" else [s["entity"]]):
                etype = s["entity_type"]
                ents[name] = {
                    "source": "world_intelligence",
                    "entity_type": etype,
                    "signal_kind": s["kind"],
                    "strength": s["strength"],
                    "distinct_sources": s["distinct_sources"],
                }
                # Map entity_type to role taxonomy
                role = {
                    "company": "company",
                    "community": "community",
                    "channel": "channel",
                    "protocol": "technology",
                    "framework": "technology",
                    "infrastructure": "technology",
                    "model": "technology",
                    "open-source-project": "project",
                    "technique": "technology",
                    "convergence": "technology",
                }.get(etype)
                if role:
                    roles.setdefault(name, set()).add(role)

        # Add channel as a role when explicitly observed
        for obs in observations:
            for ref in obs.get("item", {}).get("entity_refs", []):
                name = ref["name"]
                if ref["type"] == "channel":
                    roles.setdefault(name, set()).add("channel")

        # Relationships: convergence pairs, and channel/entity co-occurrence
        relationships = []
        # 1. Convergence signals become explicit relationships
        for s in signals:
            if s["kind"] == "convergence":
                a, b = s["entity"].split(" + ", 1)
                relationships.append({
                    "id": _stable_id("rel", a, b, "convergence"),
                    "from": a, "to": b, "type": "convergence",
                    "inference": True, "strength": s["strength"],
                    "evidence": [{"url": ev["url"]} for ev in s["evidence"][:3]],
                })

        # 2. Channel -> entity relationships from observations
        channel_entities = {}
        for obs in observations:
            item = obs.get("item", {})
            channel = item.get("channel")
            if not channel:
                continue
            for ref in item.get("entity_refs", []):
                channel_entities.setdefault(channel, set()).add(ref["name"])
        for channel, ent_names in channel_entities.items():
            for ent in ent_names:
                relationships.append({
                    "id": _stable_id("rel", f"channel:{channel}", ent, "mentioned_in"),
                    "from": f"channel:{channel}", "to": ent, "type": "mentioned_in",
                    "inference": True,
                    "evidence": [],
                })

        # Trends: convert Trend objects to dicts with stable IDs
        # (TrendHistory.trends() already returns Trend objects with .to_dict())
        # trends passed as parameter

        # Build final Brain 3 payload with entities (enriched with roles), signals, relationships, trends
        for name, meta in ents.items():
            if name in roles:
                meta["roles"] = sorted(roles[name])

        # Convert Trend objects to dicts
        trend_dicts = [t.to_dict() if hasattr(t, "to_dict") else t for t in trends]

        payload = {
            "entities": ents,
            "signals": [{"id": s["id"], "entity": s["entity"], "kind": s["kind"],
                         "strength": s["strength"], "ts": s["last_seen"]}
                        for s in signals[:50]],
            "relationships": relationships,
            "trends": trend_dicts,
        }
        if ents or relationships or trend_dicts:
            try:
                r = _post(f"{BRAIN_URL}/brain3", payload)
                result["brain3"] = r.get("added", 0)
            except Exception as e:
                result["errors"].append(f"brain3: {e}")
        return result

    # --------------------------------------------------------- queries ----
    def signals(self, kind: str | None = None, limit: int = 20) -> list[dict]:
        rows = self._snapshot().get("signals", [])
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        return rows[:limit]

    def opportunities(self, kind: str | None = None, limit: int = 20) -> list[dict]:
        rows = self._snapshot().get("opportunities", [])
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

    def evaluations(self, limit: int = 20) -> list[dict]:
        return self._snapshot().get("evaluations", [])[:limit]

    def last_run(self) -> dict:
        return self._snapshot().get("last_run", {})


if __name__ == "__main__":
    ws = Workspace(os.environ.get("NEXARA_WI_DIR", "/tmp/wi-test"))
    s = ws.run(push=False)
    print(json.dumps({k: v for k, v in s.items() if k != "sources"}, indent=2))
    for r in s["sources"]:
        print(f"  {r['source']:16} {r['items']:4} items  ok={r['ok']} configured={r['configured']}")
