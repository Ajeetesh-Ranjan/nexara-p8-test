"""Bounded, replay-safe attention history; never subscriber-growth estimates."""
import hashlib
import time
from dataclasses import dataclass, asdict

from services.common.store import StateStore

BUCKET_SECONDS = 3600
WINDOW_SECONDS = 48 * 3600


@dataclass
class Trend:
    entity: str
    entity_type: str
    current_mentions: int
    previous_mentions: int
    ratio: float | None
    direction: str
    windows: int
    evidence: list[dict]
    is_live_data: bool
    note: str = "Unique newly observed posts, not membership or market growth."
    current_window: int | None = None
    previous_window: int | None = None

    def to_dict(self):
        return {**asdict(self), "id": "trend-" + hashlib.sha256(self.entity.encode()).hexdigest()[:24],
                "inference": True}


class TrendHistory:
    def __init__(self, store: StateStore):
        self.store = store
        self.file = "trend_history.json"

    def observe(self, observations: list[dict], reports: list[dict], now: float | None = None):
        now = time.time() if now is None else now
        bucket_key = str(int(now // BUCKET_SECONDS) * BUCKET_SECONDS)

        def mutate(data):
            data.setdefault("buckets", {})
            data["buckets"] = {k: v for k, v in data["buckets"].items()
                               if int(k) >= now - WINDOW_SECONDS}
            bucket = data["buckets"].setdefault(bucket_key,
                {"counts": {}, "seen": {}, "metadata": {}, "coverage": []})
            bucket.setdefault("metadata", {})
            # Coverage must match across two adjacent completed buckets.
            bucket["coverage"] = sorted(r["source"] for r in reports if r.get("ok"))
            for observation in observations:
                entity, item = observation["entity"], observation["item"]
                key = item.get("url") or item.get("key")
                if not key or any(key in b.get("seen", {}).get(entity, []) for b in data["buckets"].values()):
                    continue
                bucket["seen"].setdefault(entity, []).append(key)
                bucket["counts"][entity] = bucket["counts"].get(entity, 0) + 1
                meta = bucket["metadata"].setdefault(entity,
                    {"type": observation["type"], "evidence": [], "is_live_data": True})
                meta["is_live_data"] = meta["is_live_data"] and item.get("is_live_data") is True
                if len(meta["evidence"]) < 6:
                    meta["evidence"].append({k: item[k] for k in
                        ("url", "title", "source", "publisher", "is_live_data", "observed_at", "published_at")
                        if k in item})
            return data

        self.store.update(self.file, mutate, {"buckets": {}})

    def trends(self, now: float | None = None) -> list[Trend]:
        now = time.time() if now is None else now
        active_start = int(now // BUCKET_SECONDS) * BUCKET_SECONDS
        buckets = self.store.read(self.file, {"buckets": {}})["buckets"]
        current_start, previous_start = active_start - BUCKET_SECONDS, active_start - 2 * BUCKET_SECONDS
        current = buckets.get(str(current_start), {})
        previous = buckets.get(str(previous_start), {})
        completed = len([k for k in buckets if now - WINDOW_SECONDS <= int(k) < active_start])
        comparable = bool(current.get("coverage")) and current.get("coverage") == previous.get("coverage")
        # Cold start: expose observed entities without comparing partial buckets.
        all_names = set(current.get("counts", {})) | set(previous.get("counts", {}))
        if not comparable:
            all_names |= set(buckets.get(str(active_start), {}).get("counts", {}))
        result = []
        for entity in sorted(all_names):
            n, prior = current.get("counts", {}).get(entity, 0), previous.get("counts", {}).get(entity, 0)
            ratio = round(n / prior, 3) if comparable and prior else None
            direction = ("growing" if ratio >= 1.5 else "declining" if ratio <= 0.5 else "stable") if ratio is not None else "insufficient_history"
            metas = [b.get("metadata", {}).get(entity, {}) for b in (current, previous)]
            if not any(metas):
                metas = [buckets.get(str(active_start), {}).get("metadata", {}).get(entity, {})]
            evidence = {e["url"]: e for m in metas for e in m.get("evidence", []) if e.get("url")}
            result.append(Trend(entity, next((m["type"] for m in metas if m.get("type")), "unclassified"),
                n, prior, ratio, direction, completed, list(evidence.values())[:12],
                bool(evidence) and all(m.get("is_live_data") is True for m in metas if m),
                current_window=current_start, previous_window=previous_start))
        return result
