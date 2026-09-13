#!/usr/bin/env python3
"""
DASHBOARD — read-only visibility. No mutations, no controls.

Aggregates health from every service and renders a single page. CLI remains the
primary interface; this exists so a human can see the fabric at a glance.
"""
import json
from html import escape
import os
import sys
import time
import urllib.parse
import urllib.error
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


# Allowed proxy targets for the read-only intelligence views. No generic proxy paths.
# Each view declares: (world-intelligence path, pinned filters, result key, title).
#
# Pinned filters define what the view *is* — "risk signals" means kind=risk — so
# they override any caller-supplied value for that key instead of being appended
# to the path. The result key is the field holding the view's rows; it drives the
# honest empty state, so a stale key would make a populated view claim emptiness.
# tests/test_phase11_surfaces.py asserts every declared key exists upstream.
VIEWS = {
    "opportunities": ("/opportunities", {}, "opportunities", "Opportunities"),
    "signals": ("/signals", {}, "signals", "Signals"),
    "trends": ("/ask/accelerating", {}, "entities", "Trends"),
    "entities": ("/ask/today", {}, "top_activity", "Entities"),
    "risk-signals": ("/signals", {"kind": "risk"}, "signals", "Risk Signals"),
    "research-queue": ("/ask/research", {}, "candidates", "Research Queue"),
}
ALLOWED_PROXY = {f"/api/intelligence/{name}": (spec[0], spec[1])
                 for name, spec in VIEWS.items()}


def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _proxy_intel(handler, query, wi_path, pinned=None):
    """Proxy a GET to world-intelligence, preserving the caller's query string.

    `wi_path` must carry no query of its own: pinned filters belong in `pinned`
    so they are urlencoded as real parameters. Concatenating "?kind=risk" onto
    the path and then appending "?limit=5" yields kind="risk?limit=5", which
    matches no row and reports an empty view while data exists.

    The caller's raw query is re-parsed with keep_blank_values so `?limit=`
    reaches world-intelligence as blank and is rejected there. The shared parser
    drops blank values, which would silently rewrite a malformed request into a
    valid default-limit one and answer 200 to a request that was never valid.
    """
    if "?" in wi_path:
        raise ValueError(f"proxy target must not embed a query string: {wi_path}")
    raw = getattr(handler, "path", None)
    if isinstance(raw, str):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(raw).query, keep_blank_values=True)
    params = {key: values for key, values in (query or {}).items()}
    params.update({key: [value] for key, value in (pinned or {}).items()})
    qs = urllib.parse.urlencode(params, doseq=True)
    url = f"{WI_URL}{wi_path}"
    if qs:
        url += "?" + qs
    try:
        data = _get(url)
        if not isinstance(data, dict) or data.get("error"):
            return 502, {"error": "invalid world-intelligence response", "upstream": data}
        return 200, data
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode())
        except (ValueError, UnicodeError):
            detail = {"error": str(exc)}
        return exc.code, {"error": "world-intelligence request failed", "upstream": detail}
    except TimeoutError as exc:
        return 504, {"error": f"world-intelligence timed out: {exc}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 502, {"error": f"world-intelligence unavailable: {exc}"}


def _send_html(handler, status, content):
    body = content.encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    return None


def _render_intelligence(title, key, data, status=200):
    # JSON is deliberately rendered as text, not interpolated into scripts or
    # URL attributes. This preserves every score, evidence URL and provenance flag.
    payload = escape(json.dumps(data, indent=2, ensure_ascii=False))
    heading = escape(title.replace("-", " ").title())
    nav = " · ".join(f'<a href="/intelligence/{escape(name)}">{escape(spec[3])}</a>'
                     for name, spec in VIEWS.items())
    if status != 200:
        state = "Upstream failure; intelligence unavailable. This is not an empty result."
    elif not isinstance(data, dict) or key not in data:
        # Never claim "no items" when the field we count is simply absent.
        state = (f"Upstream response has no '{escape(key)}' field; the view cannot confirm "
                 "whether items exist. Treat this as a defect, not as emptiness.")
    elif not data.get(key):
        state = "No items recorded for this view. This is not evidence of absence."
    else:
        state = f"{len(data[key])} item(s) shown."
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{heading}</title>'
            '<style>body{font:16px/1.5 monospace;max-width:1000px;margin:2rem auto;padding:1rem}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body>'
            f'<a href="/">NEXARA home</a><h1>{heading}</h1><nav>{nav}</nav>'
            '<p>Read-only. Stored intelligence; '
            'hypotheses are not validated value. Inspect evidence, retained and is_live_data.</p>'
            f'<p>{state}</p><pre>{payload}</pre></body></html>')


@svc.route("GET", "/intelligence/opportunities")
def opportunities_page(handler, query):
    status, data = _proxy_intel(handler, query, *ALLOWED_PROXY["/api/intelligence/opportunities"])
    return _send_html(handler, status,
                      _render_intelligence("opportunities", "opportunities", data, status))


def _intel_views(name):
    """Register the JSON and HTML routes for one whitelisted read-only view.

    Both routes are generated from VIEWS so a view can never be served from a
    target that was not explicitly allowed, and its pinned filter and result key
    cannot drift apart from the route that presents them.
    """
    wi_path, pinned, key, title = VIEWS[name]
    suffix = name.replace("-", "_")

    def json_view(handler, query, _p=wi_path, _f=pinned):
        return _proxy_intel(handler, query, _p, _f)

    def html_view(handler, query, _p=wi_path, _f=pinned, _k=key, _t=title):
        status, data = _proxy_intel(handler, query, _p, _f)
        return _send_html(handler, status, _render_intelligence(_t, _k, data, status))

    json_view.__name__ = f"intel_{suffix}"
    html_view.__name__ = f"intel_{suffix}_page"
    svc.route("GET", f"/api/intelligence/{name}")(json_view)
    if name != "opportunities":  # already registered above with its explicit handler
        svc.route("GET", f"/intelligence/{name}")(html_view)


for _name in VIEWS:
    _intel_views(_name)


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
{card("Opportunities <span class='dim'>(hypotheses)</span>", opp_rows)}
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