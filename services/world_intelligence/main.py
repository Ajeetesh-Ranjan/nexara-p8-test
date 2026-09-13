#!/usr/bin/env python3
"""
WORLD INTELLIGENCE SERVICE — HTTP surface for the operational loop.

Runs the collect->signal->opportunity cycle on a schedule and on demand, and
answers the Phase 10 NEXARA questions from stored intelligence.
"""
import json
import os
import sys
import threading
import time
from functools import wraps
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, "/app")

from services.common.service import Service
from services.world_intelligence.workspace import Workspace
from services.world_intelligence.registry import IntelligenceRegistry

DATA = os.environ.get("NEXARA_DATA", "/data")
WI_DIR = os.path.join(DATA, "world-intelligence")
CYCLE_INTERVAL = int(os.environ.get("NEXARA_WI_INTERVAL", "3600"))
AUTO_START = os.environ.get("NEXARA_WI_AUTOSTART", "1") == "1"

svc = Service("world-intelligence")
ws = Workspace(WI_DIR)
registry = IntelligenceRegistry(WI_DIR)

_lock = threading.Lock()
_running = False


def _limit(query, default=20):
    values = query.get("limit", [str(default)])
    if (len(values) != 1 or not isinstance(values[0], str)
            or not 1 <= len(values[0]) <= 3 or not values[0].isascii()
            or not values[0].isdigit() or not 1 <= int(values[0]) <= 100):
        raise ValueError("limit must be one integer from 1 to 100")
    return int(values[0])


def _bounded(fn):
    @wraps(fn)
    def checked(handler, query):
        # The shared service parser drops blank values; preserve them here.
        if handler is not None and hasattr(handler, "path"):
            query = parse_qs(urlsplit(handler.path).query, keep_blank_values=True)
        try:
            _limit(query)
        except ValueError as exc:
            return 400, {"error": str(exc)}
        return fn(handler, query)
    return checked


@svc.readiness
def ready():
    try:
        os.makedirs(WI_DIR, exist_ok=True)
        return True, "workspace writable"
    except OSError as e:
        return False, str(e)


def _cycle(reason: str):
    global _running
    with _lock:
        if _running:
            return {"skipped": "cycle already running"}
        _running = True
    try:
        t0 = time.time()
        summary = ws.run()
        print(f"[world-intelligence] cycle ({reason}): {summary['new_items']} new items, "
              f"{summary['signals']} signals, {summary['opportunities']} opportunities, "
              f"{round(time.time()-t0,1)}s", flush=True)
        return summary
    finally:
        with _lock:
            _running = False


@svc.route("POST", "/cycle")
def cycle(handler, query):
    return 200, _cycle("manual")


@svc.route("GET", "/cycle")
def cycle_status(handler, query):
    return 200, {"running": _running, "interval_seconds": CYCLE_INTERVAL,
                 "last_run": ws.last_run()}


def _view(key, rows, **extra):
    """Preserve stored evidence; collection recency is not row-level freshness."""
    last = ws.last_run()
    stamp = last.get("started_at")
    age = max(0, time.time() - stamp) if isinstance(stamp, (int, float)) else None
    stale = age is None or age > max(1, CYCLE_INTERVAL) * 2
    reports = last.get("sources", [])
    failures = [r for r in reports if r.get("configured", True) and not r.get("ok")]
    status = "never_collected" if not last else ("stale" if stale else "recent")
    if last.get("error") or failures:
        status = "partial_failure" if any(r.get("ok") for r in reports) else "failed"
    return {"count": len(rows), key: rows, "last_run": last,
            "freshness": {"status": status, "stale": stale,
                          "age_seconds": round(age, 1) if age is not None else None,
                          "note": "Collection recency only; inspect retained/is_live_data and row evidence."},
            **extra}


@svc.route("GET", "/risks")
def risks(handler, query):
    return 200, _view("risks", ws.signals(kind="risk", limit=_limit(query)),
                      caveat="Explicit risk-language signals, not confirmed incidents.")


@svc.route("GET", "/signals")
def signals(handler, query):
    kind = (query.get("kind") or [None])[0]
    limit = _limit(query)
    rows = ws.signals(kind, limit)
    return 200, {"count": len(rows), "signals": rows}


@svc.route("GET", "/opportunities")
def opportunities(handler, query):
    kind = (query.get("kind") or [None])[0]
    limit = _limit(query)
    rows = ws.opportunities(kind, limit)
    return 200, {"count": len(rows), "opportunities": rows}


@svc.route("GET", "/accelerating")
def accelerating(handler, query):
    limit = _limit(query, 15)
    return 200, {"entities": ws.accelerating(limit)}


# ------------------------- NEXARA question endpoints ------------------------

@svc.route("GET", "/ask/today")
def today(handler, query):
    """What happened today? — observations, not inference."""
    last = ws.last_run()
    sigs = ws.signals(limit=100)
    return 200, {
        "question": "What happened today?",
        "window": {"ran_at": last.get("started_at"),
                   "items_collected": last.get("collected", 0),
                   "new_items": last.get("new_items", 0)},
        "sources": [s for s in last.get("sources", [])],
        "entities_observed": last.get("distinct_entities", 0),
        "top_activity": [
            {"entity": s["entity"], "mentions": s["mentions"],
             "sources": s["distinct_sources"], "evidence": s["evidence"][:2]}
            for s in sorted(sigs, key=lambda x: -x["mentions"])[:10]
        ],
    }


@svc.route("GET", "/ask/matters")
def matters(handler, query):
    """What matters today? — strongest multi-source signals."""
    sigs = [s for s in ws.signals(limit=100) if s["distinct_sources"] >= 2]
    return 200, {
        "question": "What matters today?",
        "basis": "signals with >=2 independent sources, ranked by measured strength",
        "signals": [
            {"entity": s["entity"], "kind": s["kind"], "strength": s["strength"],
             "why": s["rationale"], "sources": s["source_names"],
             "evidence": s["evidence"][:3]}
            for s in sigs[:10]
        ],
    }


@svc.route("GET", "/ask/opportunities")
def ask_opps(handler, query):
    kind = (query.get("kind") or [None])[0]
    rows = ws.opportunities(kind=kind, limit=_limit(query, 15))
    return 200, {
        "question": "What opportunities exist?",
        "caveat": "Every item is a hypothesis derived from discussion volume, "
                  "not validated value. Confidence is capped at 0.85.",
        "opportunities": rows,
    }


OPPORTUNITY_CAVEAT = ("Every opportunity is an unvalidated hypothesis. Scores rank validation "
                      "priority, not monetary value; monetary_estimate is null and "
                      "confidence is capped at 0.85.")


@svc.route("GET", "/ask/build")
def build(handler, query):
    return 200, _view("opportunities", ws.opportunities(kind="build", limit=_limit(query)),
                      question="What should we build?", caveat=OPPORTUNITY_CAVEAT)


@svc.route("GET", "/ask/highest-value")
def highest_value(handler, query):
    # Workspace filters and ranks BEFORE applying limit; never rerank a truncated page.
    return 200, _view("opportunities", ws.opportunities(limit=_limit(query)),
                      question="Show highest-value opportunities",
                      basis="scores.overall descending; not an ROI or revenue estimate",
                      caveat=OPPORTUNITY_CAVEAT)


@svc.route("GET", "/ask/threats")
def threats(handler, query):
    rows = ws.signals(kind="risk", limit=20)
    return 200, {
        "question": "What threats exist?",
        "basis": "items containing explicit risk language (CVE, breach, deprecation, EOL, ...)",
        "count": len(rows),
        "threats": [
            {"entity": s["entity"], "strength": s["strength"], "why": s["rationale"],
             "sources": s["source_names"], "evidence": s["evidence"][:3]}
            for s in rows
        ],
    }


@svc.route("GET", "/ask/research")
def research(handler, query):
    rows = [o for o in ws.opportunities(limit=100) if o["kind"] == "research"]
    return 200, {
        "question": "What should we research?",
        "candidates": [
            {"topic": o["entities"][0] if o["entities"] else o["title"],
             "why": o["thesis"], "confidence": o["confidence"],
             "command": f'nexara research "{o["entities"][0] if o["entities"] else o["title"]}"'}
            for o in rows[:10]
        ],
    }


@svc.route("GET", "/ask/monitor")
def monitor(handler, query):
    """What should we monitor? — entities seen but not yet corroborated."""
    sigs = ws.signals(limit=200)
    weak = [s for s in sigs if s["distinct_sources"] < 3 and s["strength"] < 0.8]
    return 200, {
        "question": "What should we monitor?",
        "basis": "signals too weak to act on yet — watch for a second window",
        "watchlist": [
            {"entity": s["entity"], "kind": s["kind"], "strength": s["strength"],
             "sources": s["distinct_sources"], "why": s["rationale"]}
            for s in weak[:15]
        ],
    }


@svc.route("GET", "/ask/accelerating")
def ask_accel(handler, query):
    rows = ws.accelerating(15)
    return 200, {
        "question": "Which technologies are accelerating?",
        "basis": "measured mention counts vs per-entity historical baseline",
        "note": "requires >=2 observation windows; entities seen once are excluded",
        "entities": rows,
        "windows_available": max([r["windows"] for r in rows], default=0),
    }


@svc.route("GET", "/ask/companies")
def companies(handler, query):
    sigs = [s for s in ws.signals(limit=200) if s["entity_type"] == "company"]
    return 200, _view("communities", sigs[:12],
                      question="Which companies should we watch?",
                      caveat="Post attention, not membership counts. Source quality varies.")


@svc.route("GET", "/ask/communities")
def communities(handler, query):
    sigs = [s for s in ws.signals(limit=200) if s["entity_type"] == "company"]
    return 200, _view("communities", sigs[:12],
                      question="What communities are growing?",
                      caveat="Post attention, not membership counts. Source quality varies.")


# ------------------------- Registry management endpoints ---------------------

def _registry_write(handler, resource):
    """Validate JSON shapes before invoking the registry's domain validation."""
    try:
        payload = handler.read_json()
        if not isinstance(payload, dict):
            raise ValueError("registry input must be a JSON object")
        required = (("id", "username", "url", "category", "publisher", "description")
                    if resource == "channels" else ("id", "name"))
        for field in required:
            if not isinstance(payload.get(field), str) or not payload[field].strip():
                raise ValueError(f"{field} must be a nonempty string")
        list_field = "topics" if resource == "channels" else "keywords"
        values = payload.get(list_field, [])
        if not isinstance(values, list) or any(
                not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError(f"{list_field} must be a list of nonempty strings")
        if resource == "channels":
            registry.upsert_channel(payload)
        else:
            registry.upsert_topic(payload)
    except (ValueError, TypeError, KeyError) as exc:
        return 400, {"error": str(exc)}
    return 200, {"status": "ok"}

@svc.route("GET", "/registry/channels")
def registry_channels(handler, query):
    """List all registered channels."""
    limit = _limit(query, 50)
    return 200, {"channels": registry.channels()[:limit]}


@svc.route("POST", "/registry/channels")
def registry_channels_add(handler, query):
    """Internal use only: upsert a channel dict."""
    return _registry_write(handler, "channels")


@svc.route("GET", "/registry/topics")
def registry_topics(handler, query):
    """List all registered topics."""
    limit = _limit(query, 50)
    return 200, {"topics": registry.topics()[:limit]}


@svc.route("POST", "/registry/topics")
def registry_topics_add(handler, query):
    """Internal use only: upsert a topic dict."""
    return _registry_write(handler, "topics")


# Apply limit validation to all intelligence reads, including legacy routes.
for _key, _fn in list(svc.routes.items()):
    if _key[0] == "GET":
        svc.routes[_key] = _bounded(_fn)


def _loop():
    if AUTO_START:
        time.sleep(20)  # let brain-runtime settle
        try:
            _cycle("startup")
        except Exception as e:
            print(f"[world-intelligence] startup cycle failed: {e}", flush=True)
    while True:
        time.sleep(CYCLE_INTERVAL)
        try:
            _cycle("scheduled")
        except Exception as e:
            print(f"[world-intelligence] scheduled cycle failed: {e}", flush=True)


if __name__ == "__main__":
    threading.Thread(target=_loop, daemon=True).start()
    svc.serve(int(os.environ.get("PORT", "8086")))