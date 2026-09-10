#!/usr/bin/env python3
"""
DASHBOARD — read-only visibility. No mutations, no controls.

Aggregates health from every service and renders a single page. CLI remains the
primary interface; this exists so a human can see the fabric at a glance.
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, "/app")

from services.common.service import Service

BRAIN_URL = os.environ.get("NEXARA_BRAIN_URL", "http://brain-runtime:8081")
RELAY_URL = os.environ.get("NEXARA_RELAY_URL", "http://relay:8082")
RESEARCH_URL = os.environ.get("NEXARA_RESEARCH_URL", "http://research-fabric:8083")
SHARED_URL = os.environ.get("NEXARA_SHARED_URL", "http://shared-brain:8084")
INFRA_URL = os.environ.get("NEXARA_INFRA_URL", "http://infrastructure:8085")
WI_URL = os.environ.get("NEXARA_WI_URL", "http://world-intelligence:8086")

svc = Service("dashboard")


def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def collect() -> dict:
    def safe(fn):
        try:
            return fn()
        except Exception as e:
            return {"error": str(e)}

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "brain": safe(lambda: _get(f"{BRAIN_URL}/stats")),
        "nodes": safe(lambda: _get(f"{RELAY_URL}/nodes")),
        "wiki": safe(lambda: _get(f"{RESEARCH_URL}/wiki")),
        "artifacts": safe(lambda: _get(f"{RESEARCH_URL}/artifacts")),
        "sync": safe(lambda: _get(f"{SHARED_URL}/sync")),
        "services": safe(lambda: _get(f"{INFRA_URL}/services")),
        "host": safe(lambda: _get(f"{INFRA_URL}/observe")),
        "world": safe(lambda: _get(f"{WI_URL}/cycle")),
        "signals": safe(lambda: _get(f"{WI_URL}/signals?limit=6")),
        "opps": safe(lambda: _get(f"{WI_URL}/opportunities?limit=5")),
    }


@svc.readiness
def ready():
    return True, "dashboard serving"


@svc.route("GET", "/api/status")
def api_status(handler, query):
    return 200, collect()


def _render(d: dict) -> str:
    brain = d.get("brain", {})
    nodes = d.get("nodes", {})
    wiki = d.get("wiki", {})
    services = d.get("services", {})
    host = d.get("host", {})
    sync = d.get("sync", {})

    def card(title, rows):
        body = "".join(
            f'<div class="row"><span class="k">{k}</span><span class="v">{v}</span></div>'
            for k, v in rows
        )
        return f'<section class="card"><h2>{title}</h2>{body}</section>'

    svc_rows = []
    for name, info in (services.get("services") or {}).items():
        status = info.get("status", "?")
        cls = "ok" if status == "healthy" else "bad"
        extra = f' <span class="dim">{info.get("latency_ms")}ms</span>' if info.get("latency_ms") else ""
        svc_rows.append((name, f'<span class="{cls}">{status}</span>{extra}'))

    node_rows = []
    for n in (nodes.get("nodes") or []):
        cls = {"online": "ok", "stale": "warn"}.get(n.get("status"), "bad")
        node_rows.append((
            n.get("name", "?"),
            f'<span class="{cls}">{n.get("status")}</span> '
            f'<span class="dim">{n.get("role","")} · {len(n.get("capabilities",[]))} caps</span>'
        ))
    if not node_rows:
        node_rows = [("(none registered)", "—")]

    disk = (host.get("data_disk") or {})
    gpu = (host.get("gpu") or {})

    world = d.get("world", {}) or {}
    wlast = world.get("last_run", {}) or {}
    sig_rows = []
    for s in (d.get("signals", {}) or {}).get("signals", [])[:6]:
        cls = "ok" if s.get("strength", 0) >= 0.7 else "warn"
        sig_rows.append((s.get("entity", "?")[:26],
                         f'<span class="{cls}">{s.get("strength",0):.2f}</span> '
                         f'<span class="dim">{s.get("kind","")} · {s.get("distinct_sources",0)}src</span>'))
    if not sig_rows:
        sig_rows = [("(no signals yet)", "—")]

    opp_rows = []
    for o in (d.get("opps", {}) or {}).get("opportunities", [])[:5]:
        opp_rows.append((o.get("title", "?")[:30],
                         f'<span class="warn">{o.get("confidence",0):.2f}</span> '
                         f'<span class="dim">{o.get("kind","")}</span>'))
    if not opp_rows:
        opp_rows = [("(none yet)", "—")]

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>NEXARA</title>
<meta http-equiv="refresh" content="15">
<style>
 :root {{ --bg:#0d1117; --card:#161b22; --br:#30363d; --fg:#e6edf3; --dim:#8b949e;
          --ok:#3fb950; --warn:#d29922; --bad:#f85149; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; padding:24px; background:var(--bg); color:var(--fg);
        font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }}
 h1 {{ font-size:18px; margin:0 0 4px; letter-spacing:.5px; }}
 .sub {{ color:var(--dim); font-size:12px; margin-bottom:20px; }}
 .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; }}
 .card {{ background:var(--card); border:1px solid var(--br); border-radius:8px; padding:14px 16px; }}
 h2 {{ font-size:12px; text-transform:uppercase; letter-spacing:1px;
       color:var(--dim); margin:0 0 10px; font-weight:600; }}
 .row {{ display:flex; justify-content:space-between; gap:12px; padding:3px 0; }}
 .k {{ color:var(--dim); }} .v {{ text-align:right; }}
 .ok {{ color:var(--ok); }} .warn {{ color:var(--warn); }} .bad {{ color:var(--bad); }}
 .dim {{ color:var(--dim); font-size:12px; }}
 footer {{ margin-top:20px; color:var(--dim); font-size:12px; }}
</style></head><body>
<h1>NEXARA</h1>
<div class="sub">read-only · auto-refresh 15s · generated {d.get('generated_at')}</div>
<div class="grid">
{card("Services", svc_rows or [("(none)", "—")])}
{card("Brain", [
    ("Brain 2 entries", brain.get("brain2_entries", "—")),
    ("Brain 3 entities", brain.get("brain3_entities", "—")),
    ("Brain 3 signals", brain.get("brain3_signals", "—")),
    ("Brain 2 size", f"{brain.get('brain2_bytes', 0):,} B"),
])}
{card("Knowledge", [
    ("Wiki pages", wiki.get("total_pages", "—")),
    ("Artifacts", (d.get("artifacts") or {}).get("count", "—")),
    ("Queries", (wiki.get("sections") or {}).get("queries", "—")),
])}
{card("Nodes", node_rows)}
{card("Host", [
    ("Node", host.get("node_name", "—")),
    ("Role", host.get("node_role", "—")),
    ("CPU", host.get("cpu_count", "—")),
    ("Memory", f"{host.get('host_memory_mb','—')} MB"),
    ("GPU", "yes" if gpu.get("present") else "none"),
    ("Data free", f"{disk.get('free_gb','—')} GB ({disk.get('percent_used','—')}% used)"),
])}
{card("World Signals", sig_rows)}
{card("Opportunities <span class=\'dim\'>(hypotheses)</span>", opp_rows)}
{card("Intelligence Cycle", [
    ("Last collected", wlast.get("collected", "—")),
    ("New items", wlast.get("new_items", "—")),
    ("Entities", wlast.get("distinct_entities", "—")),
    ("Signals", wlast.get("signals", "—")),
    ("Opportunities", wlast.get("opportunities", "—")),
    ("Baseline entities", wlast.get("baseline_entities", "—")),
])}
{card("Replication", [
    ("Mode", sync.get("mode", "—")),
    ("Peers", len(sync.get("peers") or [])),
    ("Interval", f"{sync.get('interval_seconds','—')}s"),
])}
</div>
<footer>CLI is the primary interface — this view is visibility only. JSON: <code>/api/status</code></footer>
</body></html>"""


@svc.route("GET", "/")
def index(handler, query):
    html = _render(collect()).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(html)))
    handler.end_headers()
    handler.wfile.write(html)
    return None  # response already written


if __name__ == "__main__":
    svc.serve(int(os.environ.get("PORT", "8080")))
