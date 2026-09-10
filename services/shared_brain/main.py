#!/usr/bin/env python3
"""
SHARED BRAIN — replication across nodes.

Pulls brain state from peer nodes and merges it into the local brain via Brain
Runtime. Merge is last-writer-wins per entity with an evidence tiebreak, and is
idempotent on artifact_id, so repeated syncs converge instead of duplicating.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/app")

from services.common.service import Service
from services.common.store import StateStore

DATA = os.environ.get("NEXARA_DATA", "/data")
BRAIN_URL = os.environ.get("NEXARA_BRAIN_URL", "http://brain-runtime:8081")
PEERS = [p.strip() for p in os.environ.get("NEXARA_PEERS", "").split(",") if p.strip()]
SYNC_INTERVAL = int(os.environ.get("NEXARA_SYNC_INTERVAL", "300"))

svc = Service("shared-brain")
store = StateStore(os.path.join(DATA, "shared-brain"))
STATE = "sync_state.json"


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url, payload, timeout=15):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


@svc.readiness
def ready():
    try:
        _get(f"{BRAIN_URL}/health", timeout=5)
        return True, f"brain reachable, {len(PEERS)} peers configured"
    except Exception as e:
        return False, f"brain unreachable: {e}"


def sync_once() -> dict:
    """Pull from every peer and merge into local brain. Returns a report."""
    report = {"ts": time.time(), "peers": {}, "merged_artifacts": 0, "merged_entities": 0}

    for peer in PEERS:
        peer_report: dict[str, object] = {"reachable": False}
        try:
            remote_b2 = _get(f"{peer}/brain2", timeout=10)
            local_b2 = _get(f"{BRAIN_URL}/brain2", timeout=10)
            local_ids = {e.get("artifact_id") for e in local_b2.get("entries", [])}

            merged = 0
            for entry in remote_b2.get("entries", []):
                if entry.get("artifact_id") not in local_ids:
                    _post(f"{BRAIN_URL}/brain2", entry)
                    merged += 1

            remote_b3 = _get(f"{peer}/brain3/entities?limit=500", timeout=10)
            ents = remote_b3.get("entities", {})
            if ents:
                res = _post(f"{BRAIN_URL}/brain3", {"entities": ents, "signals": []})
                peer_report["entities_added"] = res.get("added", 0)
                report["merged_entities"] += res.get("added", 0)

            peer_report.update({"reachable": True, "artifacts_merged": merged})
            report["merged_artifacts"] += merged
        except Exception as e:
            peer_report["error"] = str(e)
        report["peers"][peer] = peer_report

    store.write(STATE, report)
    return report


def _loop():
    while True:
        time.sleep(SYNC_INTERVAL)
        if PEERS:
            try:
                r = sync_once()
                print(f"[shared-brain] sync: +{r['merged_artifacts']} artifacts "
                      f"+{r['merged_entities']} entities", flush=True)
            except Exception as e:
                print(f"[shared-brain] sync failed: {e}", flush=True)


@svc.route("POST", "/sync")
def trigger(handler, query):
    if not PEERS:
        return 200, {"synced": False, "reason": "no peers configured (single-node mode)"}
    return 200, sync_once()


@svc.route("GET", "/sync")
def status(handler, query):
    last = store.read(STATE, {})
    return 200, {
        "peers": PEERS,
        "interval_seconds": SYNC_INTERVAL,
        "last_sync": last,
        "mode": "multi-node" if PEERS else "single-node",
    }


if __name__ == "__main__":
    threading.Thread(target=_loop, daemon=True).start()
    svc.serve(int(os.environ.get("PORT", "8084")))
