#!/usr/bin/env python3
"""
RESEARCH FABRIC SERVICE — wraps the Phase 8 pipeline behind HTTP.

Runs research on demand, writes artifacts + wiki pages to the shared data
volume, and pushes results into Brain Runtime rather than writing brain state
directly (single writer per store).
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/research-fabric")

from services.common.service import Service
from services.common.store import StateStore

DATA = os.environ.get("NEXARA_DATA", "/data")
KB = os.path.join(DATA, "knowledge-base")
BRAIN_URL = os.environ.get("NEXARA_BRAIN_URL", "http://brain-runtime:8081")

svc = Service("research-fabric")
store = StateStore(os.path.join(DATA, "research"))

_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def _post(url: str, payload: dict, timeout: int = 15):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


@svc.readiness
def ready():
    try:
        os.makedirs(KB, exist_ok=True)
        from core.research_core import ResearchCore  # noqa: F401
        return True, "pipeline importable, data writable"
    except Exception as e:
        return False, f"pipeline not ready: {e}"


def _run_research(job_id: str, query: str, domain: str):
    from core.research_core import ResearchCore, ResearchRequest
    from pipeline.research_pipeline import Pipeline
    from core.brain_integration import BrainIntegration

    try:
        core = ResearchCore(KB)
        pipeline = Pipeline(core)
        brains = BrainIntegration(KB)

        req = ResearchRequest(query=query, domain=domain, requester="research-service")
        core.receive_request(req)
        artifact = pipeline.execute(req)
        results = brains.process_artifact(artifact)

        # Publish to Brain Runtime (authoritative brain writer)
        pushed: dict[str, object] = {"brain2": False, "brain3": False}
        try:
            _post(f"{BRAIN_URL}/brain2", {
                "type": "research_artifact",
                "artifact_id": artifact.artifact_id,
                "query": artifact.research_query,
                "summary": artifact.summary,
                "key_findings": artifact.key_findings,
                "insights": artifact.insights,
                "recommendations": artifact.recommendations,
                "sources": artifact.sources,
                "confidence": artifact.confidence,
                "created_at": artifact.created_at,
            })
            pushed["brain2"] = True
            _post(f"{BRAIN_URL}/brain3", {
                "entities": {
                    e: {"source": "research_fabric", "query": artifact.research_query}
                    for e in artifact.world_brain_updates.get("entities_created", [])
                },
                "signals": [],
            })
            pushed["brain3"] = True
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            pushed["error"] = str(e)

        with _lock:
            _jobs[job_id].update({
                "state": "done",
                "artifact_id": artifact.artifact_id,
                "confidence": artifact.confidence,
                "sources": len(artifact.sources),
                "findings": len(artifact.key_findings),
                "wiki_pages": results["wiki_pages"],
                "pushed": pushed,
                "finished_at": time.time(),
            })
    except Exception as e:
        with _lock:
            _jobs[job_id].update({"state": "failed", "error": str(e), "finished_at": time.time()})


@svc.route("POST", "/research")
def research(handler, query):
    body = handler.read_json()
    q = body.get("query")
    if not q:
        return 400, {"error": "query required"}
    domain = body.get("domain", "technology")
    job_id = f"job-{int(time.time()*1000)}"
    with _lock:
        _jobs[job_id] = {"id": job_id, "query": q, "domain": domain,
                         "state": "running", "started_at": time.time()}
    threading.Thread(target=_run_research, args=(job_id, q, domain), daemon=True).start()
    return 202, {"job_id": job_id, "state": "running"}


@svc.route("GET", "/research")
def job_status(handler, query):
    jid = (query.get("job") or [None])[0]
    with _lock:
        if jid:
            return (200, _jobs[jid]) if jid in _jobs else (404, {"error": "unknown job"})
        return 200, {"count": len(_jobs), "jobs": list(_jobs.values())[-20:]}


@svc.route("GET", "/artifacts")
def artifacts(handler, query):
    qdir = os.path.join(KB, "queries")
    files = sorted(os.listdir(qdir)) if os.path.isdir(qdir) else []
    return 200, {"count": len(files), "artifacts": files[-50:]}


@svc.route("GET", "/wiki")
def wiki(handler, query):
    wiki_root = os.path.join(KB, "llm-wiki")
    pages = {}
    total = 0
    for section in ("entities", "concepts", "comparisons", "queries"):
        d = os.path.join(wiki_root, section)
        if os.path.isdir(d):
            names = [f for f in os.listdir(d) if f.endswith(".md")]
            pages[section] = len(names)
            total += len(names)
    return 200, {"total_pages": total, "sections": pages, "root": wiki_root}


if __name__ == "__main__":
    svc.serve(int(os.environ.get("PORT", "8083")))
