#!/usr/bin/env python3
"""
BRAIN RUNTIME — owns Brain 2 (knowledge) and Brain 3 (world model) state.

All persistence goes through the atomic StateStore on a Docker volume, so a
kill -9 mid-write cannot corrupt knowledge. This is the service every other
service reads brain state from.
"""
import os
import sys
import time

sys.path.insert(0, "/app")

from services.common.service import Service
from services.common.store import StateStore

DATA = os.environ.get("NEXARA_DATA", "/data")
KB = os.path.join(DATA, "knowledge-base")

svc = Service("brain-runtime")
store = StateStore(KB)

BRAIN2 = "brain2_knowledge.json"
BRAIN3 = "brain3_world_model.json"


@svc.readiness
def ready():
    # Ready when the data volume is writable — that is the real dependency.
    try:
        probe = os.path.join(KB, ".ready-probe")
        os.makedirs(KB, exist_ok=True)
        with open(probe, "w") as f:
            f.write(str(time.time()))
        os.unlink(probe)
        return True, "data volume writable"
    except OSError as e:
        return False, f"data volume not writable: {e}"


@svc.route("GET", "/brain2")
def brain2(handler, query):
    data = store.read(BRAIN2, [])
    q = (query.get("q") or [None])[0]
    if q:
        ql = q.lower()
        data = [
            e for e in data
            if ql in str(e.get("query", "")).lower()
            or any(ql in str(f).lower() for f in e.get("key_findings", []))
        ]
    return 200, {"count": len(data), "entries": data}


@svc.route("POST", "/brain2")
def brain2_add(handler, query):
    entry = handler.read_json()
    if not entry:
        return 400, {"error": "empty body"}

    def mutate(current):
        if not isinstance(current, list):
            current = []
        # idempotent on artifact_id
        aid = entry.get("artifact_id")
        if aid and any(e.get("artifact_id") == aid for e in current):
            return current
        current.append(entry)
        return current

    new = store.update(BRAIN2, mutate, default=[])
    return 201, {"count": len(new), "artifact_id": entry.get("artifact_id")}


@svc.route("GET", "/brain3")
def brain3(handler, query):
    model = store.read(BRAIN3, {"entities": {}, "signals": [], "updated_at": None})
    return 200, {
        "entities": len(model.get("entities", {})),
        "signals": len(model.get("signals", [])),
        "updated_at": model.get("updated_at"),
    }


@svc.route("GET", "/brain3/entities")
def brain3_entities(handler, query):
    model = store.read(BRAIN3, {"entities": {}})
    ents = model.get("entities", {})
    limit = int((query.get("limit") or ["50"])[0])
    ordered = sorted(ents.items(), key=lambda kv: kv[1].get("last_seen", ""), reverse=True)
    return 200, {"count": len(ents), "entities": dict(ordered[:limit])}


@svc.route("POST", "/brain3")
def brain3_update(handler, query):
    payload = handler.read_json()
    entities = payload.get("entities", {})
    signals = payload.get("signals", [])

    def mutate(model):
        if not isinstance(model, dict):
            model = {"entities": {}, "signals": []}
        model.setdefault("entities", {})
        model.setdefault("signals", [])
        added = 0
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        for name, meta in entities.items():
            if name in model["entities"]:
                model["entities"][name]["last_seen"] = now
            else:
                model["entities"][name] = {**meta, "first_seen": now, "last_seen": now}
                added += 1
        model["signals"].extend(signals)
        model["updated_at"] = now
        model["_last_added"] = added
        return model

    new = store.update(BRAIN3, mutate, default={"entities": {}, "signals": []})
    return 201, {
        "entities": len(new.get("entities", {})),
        "signals": len(new.get("signals", [])),
        "added": new.get("_last_added", 0),
    }


@svc.route("GET", "/stats")
def stats(handler, query):
    b2 = store.read(BRAIN2, [])
    b3 = store.read(BRAIN3, {"entities": {}, "signals": []})
    return 200, {
        "brain2_entries": len(b2) if isinstance(b2, list) else 0,
        "brain2_bytes": store.size(BRAIN2),
        "brain3_entities": len(b3.get("entities", {})),
        "brain3_signals": len(b3.get("signals", [])),
        "brain3_bytes": store.size(BRAIN3),
        "data_dir": KB,
    }


if __name__ == "__main__":
    svc.serve(int(os.environ.get("PORT", "8081")))
