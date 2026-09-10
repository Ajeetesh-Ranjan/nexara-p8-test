#!/usr/bin/env python3
"""
RELAY — node registry, discovery, health, and cross-node messaging.

Nodes register themselves, heartbeat periodically, and are marked stale/down
when heartbeats stop. Registry is persisted atomically so relay restarts do not
lose the fabric.
"""
import os
import sys
import time

sys.path.insert(0, "/app")

from services.common.service import Service
from services.common.store import StateStore

DATA = os.environ.get("NEXARA_DATA", "/data")
RELAY_DIR = os.path.join(DATA, "relay")
HEARTBEAT_TIMEOUT = int(os.environ.get("NEXARA_HEARTBEAT_TIMEOUT", "90"))

svc = Service("relay")
store = StateStore(RELAY_DIR)

NODES = "nodes.json"
MESSAGES = "messages.json"


def _now():
    return time.time()


def _node_status(node):
    age = _now() - node.get("last_heartbeat", 0)
    if age > HEARTBEAT_TIMEOUT * 2:
        return "down"
    if age > HEARTBEAT_TIMEOUT:
        return "stale"
    return "online"


@svc.readiness
def ready():
    try:
        os.makedirs(RELAY_DIR, exist_ok=True)
        return True, "registry available"
    except OSError as e:
        return False, str(e)


@svc.route("POST", "/nodes/register")
def register(handler, query):
    body = handler.read_json()
    name = body.get("name")
    if not name:
        return 400, {"error": "name required"}

    def mutate(nodes):
        if not isinstance(nodes, dict):
            nodes = {}
        existing = nodes.get(name, {})
        nodes[name] = {
            "name": name,
            "role": body.get("role", "node"),
            "capabilities": body.get("capabilities", []),
            "resources": body.get("resources", {}),
            "services": body.get("services", []),
            "address": body.get("address"),
            "registered_at": existing.get("registered_at", _now()),
            "last_heartbeat": _now(),
        }
        return nodes

    nodes = store.update(NODES, mutate, default={})
    return 201, {"registered": name, "total_nodes": len(nodes)}


@svc.route("POST", "/nodes/heartbeat")
def heartbeat(handler, query):
    body = handler.read_json()
    name = body.get("name")
    if not name:
        return 400, {"error": "name required"}

    def mutate(nodes):
        if not isinstance(nodes, dict):
            nodes = {}
        if name not in nodes:
            return nodes
        nodes[name]["last_heartbeat"] = _now()
        if "health" in body:
            nodes[name]["health"] = body["health"]
        return nodes

    nodes = store.update(NODES, mutate, default={})
    if name not in nodes:
        return 404, {"error": "node not registered", "name": name}
    return 200, {"name": name, "status": "online"}


@svc.route("GET", "/nodes")
def list_nodes(handler, query):
    nodes = store.read(NODES, {})
    out = []
    for name, n in nodes.items():
        out.append({
            **n,
            "status": _node_status(n),
            "heartbeat_age_seconds": round(_now() - n.get("last_heartbeat", 0), 1),
        })
    out.sort(key=lambda n: n["name"])
    return 200, {
        "count": len(out),
        "online": sum(1 for n in out if n["status"] == "online"),
        "nodes": out,
    }


@svc.route("GET", "/nodes/discover")
def discover(handler, query):
    """Find online nodes advertising a capability."""
    cap = (query.get("capability") or [None])[0]
    nodes = store.read(NODES, {})
    matches = [
        {"name": n["name"], "address": n.get("address"), "capabilities": n.get("capabilities", [])}
        for n in nodes.values()
        if _node_status(n) == "online" and (cap is None or cap in n.get("capabilities", []))
    ]
    return 200, {"capability": cap, "count": len(matches), "nodes": matches}


@svc.route("POST", "/messages")
def publish(handler, query):
    body = handler.read_json()

    def mutate(msgs):
        if not isinstance(msgs, list):
            msgs = []
        msgs.append({
            "from": body.get("from", "unknown"),
            "to": body.get("to", "*"),
            "topic": body.get("topic", "general"),
            "payload": body.get("payload"),
            "ts": _now(),
        })
        return msgs[-1000:]  # bounded queue

    msgs = store.update(MESSAGES, mutate, default=[])
    return 201, {"queued": len(msgs)}


@svc.route("GET", "/messages")
def consume(handler, query):
    msgs = store.read(MESSAGES, [])
    topic = (query.get("topic") or [None])[0]
    since = float((query.get("since") or ["0"])[0])
    out = [m for m in msgs if m["ts"] > since and (topic is None or m["topic"] == topic)]
    return 200, {"count": len(out), "messages": out[-100:]}


if __name__ == "__main__":
    svc.serve(int(os.environ.get("PORT", "8082")))
