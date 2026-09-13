"""Phase 11 surfaces: isolated stdlib tests, never import the Phase 10 script.

Run: python -m unittest tests.test_phase11_surfaces -v
All service imports and subprocesses use temporary NEXARA_DATA directories.
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qs, urlencode
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def opportunity(identifier="o1", kind="build", overall=81):
    """Explicit test fixture, not demonstration or production intelligence."""
    return {
        "id": identifier, "title": "Validate a protocol integration", "kind": kind,
        "confidence": 0.7, "thesis": "Unvalidated test hypothesis",
        "entities": ["Test Protocol"], "distinct_sources": 2,
        "scores": {"impact": 80, "confidence": 70, "effort": 30, "risk": 20,
                   "time_to_value": 75, "strategic_alignment": 90, "overall": overall},
        "is_hypothesis": True, "risk_band": "low", "decision": "shortlist",
        "monetary_estimate": None, "is_live_data": False,
        "next_step": "Check the primary documentation", "risks": ["Needs validation"],
        "questions": {f"question_{i}": f"answer_{i}" for i in range(7)},
        "evidence": [{"title": "Primary source", "source": "test",
                      "url": "https://example.org/evidence?a=1&b=2"}],
    }


class MemoryHandler:
    """In-memory HTTP transport; the registered production handler still runs."""
    def __init__(self, path, payload=None):
        self.path = path
        self.payload = payload
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {}

    def read_json(self):
        return self.payload

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass


class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexara-p11-surfaces-")
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {
            "NEXARA_DATA": self.temp.name, "NEXARA_WI_AUTOSTART": "0",
            "NEXARA_BRAIN_URL": "http://127.0.0.1:1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.wi = load_module("p11_world_surface", "services/world_intelligence/main.py")
        self.cli = load_module("p11_cli_surface", "scripts/nexara_world.py")

    @property
    def dash(self):
        return load_module("p11_dashboard_surface", "services/dashboard/main.py")

    def seed(self, **rows):
        """Store fixture intelligence through the workspace's own persistence.

        Seeding asserts the workspace readers see what was written, so a change
        to the storage layout fails here with a clear message instead of
        silently starving every surface assertion into an empty list.
        """
        snapshot = self.wi.ws.store.read("snapshot.json", {})
        snapshot.update(rows)
        self.wi.ws.store.write("snapshot.json", snapshot)
        readers = {"signals": lambda: self.wi.ws.signals(limit=100),
                   "opportunities": lambda: self.wi.ws.opportunities(limit=100),
                   "last_run": self.wi.ws.last_run}
        for key, expected in rows.items():
            read_back = readers[key]()
            self.assertEqual(read_back, expected,
                             f"workspace does not read back seeded {key}; storage layout changed")

    def test_dashboard_compiles_on_supported_python(self):
        path = ROOT / "services/dashboard/main.py"
        try:
            compile(path.read_text(), str(path), "exec")
        except SyntaxError as exc:
            self.fail(f"Dashboard cannot start: {exc}")

    def route(self, module, path, query=None, method="GET", handler=None):
        fn = module.svc.routes.get((method, path))
        self.assertIsNotNone(fn, f"missing route {method} {path}")
        return fn(handler, query or {})

    def dispatch(self, module, path, method="GET", payload=None):
        handler = MemoryHandler(path, payload)
        parsed = urlsplit(path)
        fn = module.svc.routes.get((method, parsed.path))
        if fn is None:
            return 404, {"error": "not found"}
        result = fn(handler, parse_qs(parsed.query))
        if result is not None:
            return result
        self.assertEqual(int(handler.headers["Content-Length"]), len(handler.wfile.getvalue()))
        return handler.status, handler.wfile.getvalue().decode()

    def wi_network(self, url, timeout=None):
        """Replace only urlopen; route all allowed reads through the real WI app."""
        if not isinstance(url, str):
            self.assertEqual(url.get_method(), "GET")
            url = url.full_url
        parsed = urlsplit(url)
        self.assertIn(parsed.hostname, ("world-intelligence", "localhost"))
        status, data = self.dispatch(self.wi, parsed.path + "?" + parsed.query)
        body = io.BytesIO(json.dumps(data).encode())
        if status >= 400:
            raise HTTPError(url, status, "WI error", {}, body)
        return body

    def test_risk_signals_view_keeps_pinned_filter_with_caller_query(self):
        """A pinned view filter must survive caller query params.

        Naive concatenation produces `/signals?kind=risk?limit=1`, which parses
        as kind='risk?limit=1', matches no row, and reports an empty risk view
        while risks exist — a silent false negative, the worst failure here.
        """
        rows = [{"id": "s1", "entity": "Fixture risk", "kind": "risk", "strength": 0.9,
                 "mentions": 2, "distinct_sources": 2, "evidence": []},
                {"id": "s2", "entity": "Fixture other", "kind": "emergence", "strength": 0.5,
                 "mentions": 1, "distinct_sources": 1, "evidence": []}]
        self.seed(signals=rows)
        app = self.dash
        for query in ("", "?limit=5", "?kind=emergence"):
            with self.subTest(query=query):
                with patch("urllib.request.urlopen", side_effect=self.wi_network):
                    status, data = self.dispatch(app, "/api/intelligence/risk-signals" + query)
                self.assertEqual(status, 200)
                # The pinned kind wins: a "risk signals" view never returns non-risk rows.
                self.assertEqual([r["id"] for r in data["signals"]], ["s1"])

    def test_dashboard_views_reject_invalid_limit_instead_of_silently_defaulting(self):
        app = self.dash
        for view in ("opportunities", "signals", "trends", "entities",
                     "risk-signals", "research-queue"):
            with self.subTest(view=view):
                with patch("urllib.request.urlopen", side_effect=self.wi_network):
                    status, data = self.dispatch(app, f"/api/intelligence/{view}?limit=0")
                self.assertEqual(status, 400)
                self.assertIn("limit", json.dumps(data))

    def test_every_view_html_page_renders_readonly_with_navigation(self):
        app = self.dash
        for name in app.VIEWS:
            with self.subTest(view=name):
                with patch("urllib.request.urlopen", side_effect=self.wi_network):
                    status, html = self.dispatch(app, f"/intelligence/{name}")
                self.assertEqual(status, 200)
                self.assertIn("Read-only", html)
                # Every page links to all six views and never offers a control.
                for other in app.VIEWS:
                    self.assertIn(f'href="/intelligence/{other}"', html)
                for control in ("<form", "<button", "<input", "method=\"post\""):
                    self.assertNotIn(control, html.lower())

    def test_declared_view_keys_match_live_upstream_responses(self):
        """The empty state counts data[key]; a wrong key would fake emptiness."""
        rows = [opportunity("o1")]
        signals = [{"id": "s1", "entity": "Fixture", "kind": "risk", "strength": 0.9,
                    "mentions": 3, "distinct_sources": 2, "evidence": [],
                    "entity_type": "company", "rationale": "fixture", "source_names": ["a", "b"]}]
        self.seed(opportunities=rows, signals=signals)
        app = self.dash
        for name, (wi_path, pinned, key, _title) in app.VIEWS.items():
            with self.subTest(view=name):
                with patch("urllib.request.urlopen", side_effect=self.wi_network):
                    status, data = self.dispatch(app, f"/api/intelligence/{name}")
                self.assertEqual(status, 200)
                self.assertIn(key, data,
                              f"view '{name}' counts data['{key}'] but upstream {wi_path} "
                              f"returned keys {sorted(data)}")
                self.assertIsInstance(data[key], list)

    def test_html_view_distinguishes_empty_from_missing_field(self):
        app = self.dash
        empty = app._render_intelligence("Signals", "signals", {"signals": [], "count": 0}, 200)
        self.assertIn("not evidence of absence", empty)
        missing = app._render_intelligence("Signals", "signals", {"count": 0}, 200)
        self.assertIn("defect", missing)
        self.assertNotIn("No items recorded", missing)
        failed = app._render_intelligence("Signals", "signals", {"error": "upstream down"}, 502)
        self.assertIn("not an empty result", failed)
        self.assertNotIn("No items recorded", failed)

    def test_proxy_target_with_embedded_query_is_rejected(self):
        app = self.dash
        with self.assertRaises(ValueError):
            app._proxy_intel(None, {}, "/signals?kind=risk")

    def test_cli_renderers_match_live_response_shapes(self):
        """Render every Phase 10 command against the real WI response.

        cmd_companies crashed with KeyError 'company' in live use because the
        upstream response returns raw signal rows keyed 'entity'. Rendering
        against hand-written dicts would not have caught it; these payloads come
        from the production handlers.
        """
        rows = [{"id": "s1", "entity": "Fixture Co", "entity_type": "company", "kind": "risk",
                 "strength": 0.72, "mentions": 4, "distinct_sources": 2,
                 "source_names": ["a.example", "b.example"], "categories": ["technology"],
                 "rationale": "fixture rationale", "is_single_source": False,
                 "first_seen": 1.0, "last_seen": 2.0,
                 "evidence": [{"title": "Ev", "source": "fixture",
                               "url": "https://example.org/1"}]}]
        self.seed(signals=rows, opportunities=[opportunity("o1", "research")],
                  last_run={"started_at": 1, "collected": 1, "new_items": 1,
                            "distinct_entities": 1, "sources": []})
        commands = [("cmd_today", "/ask/today"), ("cmd_matters", "/ask/matters"),
                    ("cmd_threats", "/ask/threats"), ("cmd_research", "/ask/research"),
                    ("cmd_monitor", "/ask/monitor"), ("cmd_accelerating", "/ask/accelerating"),
                    ("cmd_companies", "/ask/companies")]
        for name, path in commands:
            with self.subTest(command=name):
                status, payload = self.dispatch(self.wi, path + "?limit=20")
                self.assertEqual(status, 200)
                body = io.BytesIO(json.dumps(payload).encode())
                args = SimpleNamespace(kind=None, limit=20)
                out = io.StringIO()
                with patch("urllib.request.urlopen", return_value=body):
                    with redirect_stdout(out):
                        getattr(self.cli, name)(args)
                self.assertTrue(out.getvalue().strip(), f"{name} rendered nothing")

    def test_dashboard_forwards_blank_query_values_instead_of_dropping_them(self):
        """`?limit=` must reach upstream as blank and be rejected, not vanish.

        The stdlib parser drops blank values, so a malformed request would be
        silently rewritten into a valid default-limit request and answered 200.
        """
        app = self.dash
        with patch("urllib.request.urlopen", side_effect=self.wi_network):
            status, data = self.dispatch(app, "/api/intelligence/signals?limit=")
        self.assertEqual(status, 400)
        self.assertIn("limit", json.dumps(data))

    def test_cli_surfaces_upstream_error_detail_not_just_status_line(self):
        """A rejected registry write must tell the operator WHY."""
        detail = json.dumps({"error": "provenance verification not recognised"}).encode()
        error = HTTPError("http://localhost:8086/registry/channels", 400,
                          "Bad Request", {}, io.BytesIO(detail))
        args = SimpleNamespace(channels_cmd="add", payload=json.dumps({"id": "x"}),
                               parser=None)
        out = io.StringIO()
        with patch("urllib.request.urlopen", side_effect=error):
            with redirect_stdout(out), self.assertRaises(SystemExit):
                self.cli.cmd_channels(args)
        self.assertIn("provenance verification not recognised", out.getvalue())

    def test_dashboard_opportunity_handler_escapes_full_evidence(self):
        row = opportunity()
        row["title"] = '<script>alert("fixture")</script>'
        row["retained"] = True
        row["evidence"].append({"url": "javascript:alert(1)", "title": "unsafe"})
        self.seed(opportunities=[row])
        app = self.dash
        with patch("urllib.request.urlopen", side_effect=self.wi_network):
            status, html = self.dispatch(app, "/intelligence/opportunities?limit=1")
        self.assertEqual(status, 200)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn('<script>alert("fixture")</script>', html)
        self.assertIn("https://example.org/evidence?a=1&amp;b=2", html)
        self.assertNotIn('href="javascript:', html)
        for field in (*row["scores"], "evidence", "is_hypothesis", "is_live_data", "retained"):
            self.assertIn(field, html)

    def test_dashboard_propagates_upstream_failure(self):
        app = self.dash
        for path in ("/intelligence/opportunities", "/api/intelligence/opportunities"):
            for code in (400, 429, 503):
                with self.subTest(path=path, code=code):
                    error = HTTPError("http://world-intelligence:8086/opportunities", code,
                        "fixture failure", {}, io.BytesIO(b'{"error":"fixture unavailable"}'))
                    with patch("urllib.request.urlopen", side_effect=error):
                        status, result = self.dispatch(app, path)
                    self.assertEqual(status, code)
                    self.assertIn("fixture unavailable", str(result))
                    self.assertNotIn("No items", str(result))
                    self.assertNotIn('"count": 0', str(result))

    def test_risks_reads_risk_signals_with_provenance_and_staleness(self):
        rows = [{"id": "s1", "entity": "Fixture only", "kind": "risk",
                 "evidence": [{"url": "https://example.org/evidence", "source": "fixture"}],
                 "is_live_data": False, "retained": True},
                {"id": "s2", "entity": "Other", "kind": "emergence"}]
        self.seed(signals=rows, last_run={"started_at": 1,
            "sources": [{"source": "fixture", "configured": True, "ok": False,
                         "error": "fixture fetch failed"}]})
        status, result = self.route(self.wi, "/risks", {"limit": ["1"]})
        self.assertEqual(status, 200)
        self.assertEqual(result["risks"], rows[:1])
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["freshness"]["stale"])
        self.assertIn("failed", result["freshness"]["status"])
        self.assertEqual(result["last_run"]["started_at"], 1)

    def test_build_and_highest_value_ask_routes_preserve_ranked_rows(self):
        rows = [opportunity("top-research", "research", 95), opportunity("top-build", "build", 90),
                opportunity("other-build", "build", 70)]
        self.seed(opportunities=rows)
        for path, expected in (("/ask/build", [rows[1]]),
                               ("/ask/highest-value", [rows[0]])):
            status, result = self.dispatch(self.wi, path + "?limit=1")
            self.assertEqual(status, 200)
            self.assertEqual(result["opportunities"], expected)
            self.assertEqual(result["count"], 1)
            self.assertIn("hypoth", result["caveat"].lower())
            self.assertIn("freshness", result)

    def test_ask_opportunities_preserves_score_metadata(self):
        row = opportunity()
        self.seed(opportunities=[row])
        status, result = self.route(self.wi, "/ask/opportunities")
        self.assertEqual(status, 200)
        self.assertEqual(result["opportunities"], [row])

    def test_opportunity_query_filters_before_bounded_limit(self):
        rows = [opportunity("research", "research"), opportunity("build1"),
                opportunity("build2")]
        self.seed(opportunities=rows)
        status, result = self.route(self.wi, "/ask/opportunities",
                                    {"kind": ["build"], "limit": ["1"]})
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in result["opportunities"]], ["build1"])

    def test_intelligence_invalid_limits_are_400(self):
        for path in ("/signals", "/opportunities", "/accelerating", "/ask/opportunities"):
            for value in ("0", "-1", "101", "1.5", "banana", "", " 2", "1" * 5000):
                with self.subTest(path=path, value=value[:20]):
                    try:
                        status, result = self.route(self.wi, path, {"limit": [value]})
                    except (ValueError, TypeError) as exc:
                        self.fail(f"invalid limit escaped as server error: {exc}")
                    self.assertEqual(status, 400)
                    self.assertIn("limit", result["error"])
        status, _ = self.route(self.wi, "/signals", {"limit": ["1", "2"]})
        self.assertEqual(status, 400)

    def test_ask_opportunities_kind_filter(self):
        rows = [opportunity("r", "research"), opportunity("b", "build")]
        self.seed(opportunities=rows)
        status, result = self.route(self.wi, "/ask/opportunities", {"kind": ["research"]})
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in result["opportunities"]], ["r"])

    def test_dashboard_rejects_mutation(self):
        app = self.dash
        for method, path in [("POST", "/api/intelligence/opportunities"),
                             ("DELETE", "/api/intelligence/signals"),
                             ("PUT", "/"), ("GET", "/proxy?url=http://evil/")]:
            fn = app.svc.routes.get((method, path))
            if method == "GET" and path.startswith("/proxy"):
                self.assertIsNone(fn, f"dashboard exposed forbidden proxy path {path}")
            elif fn is None:
                continue
            else:
                self.fail(f"dashboard exposed mutation path {method} {path}")

    def test_dashboard_json_endpoints_return_views(self):
        app = self.dash
        for view in ("opportunities", "signals", "trends", "entities",
                     "risk-signals", "research-queue"):
            fn = app.svc.routes.get(("GET", f"/api/intelligence/{view}"))
            self.assertIsNotNone(fn, f"dashboard missing read endpoint /{view}")

    def test_dashboard_html_escapes_and_honest_empty(self):
        from http.server import BaseHTTPRequestHandler
        handler = BaseHTTPRequestHandler.__new__(BaseHTTPRequestHandler)
        handler.path = "/?__t=1"

        class FakeQuery(dict):
            pass
        app = self.dash
        # No intelligence present → honest empty, not fake examples.
        with patch.object(app, "WI_URL", "http://127.0.0.1:1"):
            html = app._render(app.collect() if False else app.collect())
        self.assertIn("(none yet)", html)
        self.assertNotIn("Quantum leap to Mars", html)
        # Evidence URLs must be HTML-escaped.

    def test_dashboard_no_proxy_route(self):
        app = self.dash
        proxy_routes = [p for (m, p) in app.svc.routes if "proxy" in p.lower()]
        self.assertEqual(proxy_routes, [])

    def test_wi_registry_get_endpoints(self):
        # Parent wires GET /registry/channels and /registry/topics to root registry.
        for path in ("/registry/channels", "/registry/topics"):
            fn = self.wi.svc.routes.get(("GET", path))
            self.assertIsNotNone(fn, f"world-intelligence missing {path}")

    def test_all_wi_get_handlers_reject_raw_blank_duplicate_limits(self):
        from types import SimpleNamespace
        for method, path in self.wi.svc.routes:
            if method != "GET":
                continue
            for raw in ("limit=", "limit=2&limit=", "limit=&limit=1", "limit=101",
                        "limit=0", "limit=1&limit=2", "limit=%EF%BC%91"):
                with self.subTest(path=path, raw=raw):
                    status, result = self.route(
                        self.wi, path, handler=SimpleNamespace(path=f"{path}?{raw}"))
                    self.assertEqual(status, 400)
                    self.assertIn("limit", result["error"])

    def test_invalid_registry_input_is_400_without_write(self):
        from types import SimpleNamespace
        before = (self.wi.registry.channels(), self.wi.registry.topics())
        for path in ("/registry/channels", "/registry/topics"):
            for payload in (None, [], {}, {"id": "bad/id"}, {"id": 3}, {"id": []},
                            {"id": "test", "name": []},
                            {"id": "test", "name": "Test", "keywords": [{}]}):
                with self.subTest(path=path, payload=payload):
                    status, result = self.route(self.wi, path, method="POST",
                        handler=SimpleNamespace(read_json=lambda: payload))
                    self.assertEqual(status, 400)
                    self.assertIn("error", result)
        self.assertEqual((self.wi.registry.channels(), self.wi.registry.topics()), before)

    def test_wi_registry_post_only_internal(self):
        # POST registry routes must exist (cycle uses them) but dashboard must not.
        self.assertIsNotNone(self.wi.svc.routes.get(("POST", "/registry/channels")))
        self.assertIsNone(self.dash.svc.routes.get(("POST", "/registry/channels")))

    def test_cli_eight_natural_language_phrases_dispatch_readonly(self):
        cases = [
            ("What opportunities exist?", "opportunities", "opportunities"),
            ("What communities are growing?", "communities", "communities"),
            ("What should we monitor?", "monitor", "watchlist"),
            ("What should we build?", "build", "opportunities"),
            ("What changed today?", "today", "changes"),
            ("Which technologies are accelerating?", "accelerating", "entities"),
            ("Which companies should we watch?", "companies", "companies"),
            ("Show highest-value opportunities", "highest-value", "opportunities"),
        ]
        for phrase, alias, key in cases:
            for question in ([phrase], phrase.split(), [alias]):
                with self.subTest(question=question):
                    args = self.cli.build_parser().parse_args(["ask", *question, "--limit", "1"])
                    data = {key: [], "caveat": "Fixture only; no live evidence",
                            "freshness": {"status": "stale", "stale": True}}
                    with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(data).encode())) as net:
                        out = io.StringIO()
                        with redirect_stdout(out):
                            self.cli.cmd_ask(args)
                    url = net.call_args.args[0]
                    self.assertIsInstance(url, str)
                    self.assertEqual(urlsplit(url).path, f"/ask/{alias}")
                    self.assertEqual(parse_qs(urlsplit(url).query), {"limit": ["1"]})
                    self.assertIn("No items", out.getvalue())
                    self.assertIn("Fixture only", out.getvalue())
                    self.assertIn("stale", out.getvalue())


if __name__ == "__main__":
    unittest.main()
