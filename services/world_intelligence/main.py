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

sys.path.insert(0, "/app")

from services.common.service import Service
from services.world_intelligence.workspace import Workspace

DATA = os.environ.get("NEXARA_DATA", "/data")
WI_DIR = os.path.join(DATA, "world-intelligence")
CYCLE_INTERVAL = int(os.environ.get("NEXARA_WI_INTERVAL", "3600"))
AUTO_START = os.environ.get("NEXARA_WI_AUTOSTART", "1") == "1"

svc = Service("world-intelligence")
ws = Workspace(WI_DIR)

_lock = threading.Lock()
_running = False


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


@svc.route("GET", "/signals")
def signals(handler, query):
    kind = (query.get("kind") or [None])[0]
    limit = int((query.get("limit") or ["20"])[0])
    rows = ws.signals(kind, limit)
    return 200, {"count": len(rows), "signals": rows}


@svc.route("GET", "/opportunities")
def opportunities(handler, query):
    kind = (query.get("kind") or [None])[0]
    limit = int((query.get("limit") or ["20"])[0])
    rows = ws.opportunities(kind, limit)
    return 200, {"count": len(rows), "opportunities": rows}


@svc.route("GET", "/accelerating")
def accelerating(handler, query):
    limit = int((query.get("limit") or ["15"])[0])
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
    rows = ws.opportunities(limit=15)
    return 200, {
        "question": "What opportunities exist?",
        "caveat": "Every item is a hypothesis derived from discussion volume, "
                  "not validated value. Confidence is capped at 0.85.",
        "opportunities": [
            {"title": o["title"], "kind": o["kind"], "confidence": o["confidence"],
             "thesis": o["thesis"], "next_step": o["next_step"],
             "risks": o["risks"], "sources": o["distinct_sources"],
             "evidence": o["evidence"][:3]}
            for o in rows
        ],
    }


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
    return 200, {
        "question": "Which companies should we watch?",
        "companies": [
            {"company": s["entity"], "kind": s["kind"], "strength": s["strength"],
             "mentions": s["mentions"], "sources": s["source_names"],
             "evidence": s["evidence"][:2]}
            for s in sigs[:12]
        ],
    }


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
