#!/usr/bin/env python3
"""
INFRASTRUCTURE WORKSPACE — real observation of the host and the fabric.

Everything reported here is measured, not declared. If a value cannot be
observed it is reported as null with a reason, never guessed.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, "/app")

from services.common.service import Service
from services.common.store import StateStore

DATA = os.environ.get("NEXARA_DATA", "/data")
RELAY_URL = os.environ.get("NEXARA_RELAY_URL", "http://relay:8082")
BRAIN_URL = os.environ.get("NEXARA_BRAIN_URL", "http://brain-runtime:8081")

svc = Service("infrastructure")
store = StateStore(os.path.join(DATA, "infrastructure"))


def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def observe_host() -> dict:
    """Measure the host this container runs on. Container-aware where possible."""
    obs: dict[str, object] = {"observed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    obs["hostname"] = socket.gethostname()
    obs["cpu_count"] = os.cpu_count()

    # memory: prefer cgroup v2 limit (the real container ceiling), fall back to host
    mem_limit = None
    try:
        with open("/sys/fs/cgroup/memory.max") as f:
            v = f.read().strip()
            mem_limit = None if v == "max" else int(v)
    except OSError:
        pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    obs["host_memory_mb"] = int(line.split()[1]) // 1024
                    break
    except OSError:
        obs["host_memory_mb"] = None
    obs["container_memory_limit_mb"] = mem_limit // (1024 * 1024) if mem_limit else None

    # disk on the data volume — the number that actually matters for persistence
    try:
        du = shutil.disk_usage(DATA)
        obs["data_disk"] = {
            "total_gb": round(du.total / 1e9, 1),
            "used_gb": round(du.used / 1e9, 1),
            "free_gb": round(du.free / 1e9, 1),
            "percent_used": round(du.used / du.total * 100, 1),
        }
    except OSError as e:
        obs["data_disk"] = {"error": str(e)}

    # GPU: only claim one if nvidia-smi actually answers
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, timeout=5, text=True)
        gpus = [g for g in r.stdout.strip().split("\n") if g] if r.returncode == 0 else []
        obs["gpu"] = {"present": bool(gpus), "devices": gpus}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        obs["gpu"] = {"present": False, "devices": [], "reason": "nvidia-smi unavailable"}

    obs["node_name"] = os.environ.get("NEXARA_NODE_NAME", obs["hostname"])
    obs["node_role"] = os.environ.get("NEXARA_NODE_ROLE", "unknown")
    return obs


@svc.readiness
def ready():
    return True, "observer active"


@svc.route("GET", "/observe")
def observe(handler, query):
    obs = observe_host()
    store.write("last_observation.json", obs)
    return 200, obs


@svc.route("GET", "/fabric")
def fabric(handler, query):
    """Cross-service view: nodes from relay, brain counts from brain-runtime."""
    out: dict[str, object] = {"observed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        out["nodes"] = _get(f"{RELAY_URL}/nodes")
    except Exception as e:
        out["nodes"] = {"error": str(e)}
    try:
        out["brain"] = _get(f"{BRAIN_URL}/stats")
    except Exception as e:
        out["brain"] = {"error": str(e)}
    out["host"] = observe_host()
    return 200, out


@svc.route("GET", "/services")
def services(handler, query):
    """Probe every known service's health endpoint — real checks, not a static list."""
    targets = {
        "brain-runtime": BRAIN_URL,
        "relay": RELAY_URL,
        "research-fabric": os.environ.get("NEXARA_RESEARCH_URL", "http://research-fabric:8083"),
        "shared-brain": os.environ.get("NEXARA_SHARED_URL", "http://shared-brain:8084"),
        "world-intelligence": os.environ.get("NEXARA_WI_URL", "http://world-intelligence:8086"),
    }
    results = {}
    for name, url in targets.items():
        started = time.time()
        try:
            h = _get(f"{url}/health", timeout=5)
            results[name] = {
                "status": h.get("status"),
                "uptime_seconds": h.get("uptime_seconds"),
                "latency_ms": round((time.time() - started) * 1000, 1),
            }
        except Exception as e:
            results[name] = {"status": "unreachable", "error": str(e)}
    healthy = sum(1 for r in results.values() if r.get("status") == "healthy")
    return 200, {"healthy": healthy, "total": len(targets), "services": results}


if __name__ == "__main__":
    svc.serve(int(os.environ.get("PORT", "8085")))
