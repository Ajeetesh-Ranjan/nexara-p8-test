#!/usr/bin/env python3
"""
Minimal HTTP service base — stdlib only, no framework dependency.

Every NEXARA service exposes:
  GET /health   -> liveness + readiness (used by Docker HEALTHCHECK)
  GET /info     -> service identity and version
Plus its own routes registered via @route.
"""
import json
import os
import signal
import socket
import threading
import time
import traceback
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

START_TIME = time.time()


class Service:
    """Route registry + server lifecycle for one NEXARA service."""

    def __init__(self, name: str, version: str = "9.0.0"):
        self.name = name
        self.version = version
        self.routes: dict[tuple[str, str], Callable] = {}
        self._ready_check = lambda: (True, "ready")
        self._shutdown = threading.Event()

        self.route("GET", "/health")(self._health)
        self.route("GET", "/info")(self._info)

    def route(self, method: str, path: str):
        def deco(fn):
            self.routes[(method.upper(), path)] = fn
            return fn
        return deco

    def readiness(self, fn):
        """Register a readiness probe returning (bool, reason)."""
        self._ready_check = fn
        return fn

    # ---------- built-in endpoints ----------

    def _health(self, handler, query):
        ok, reason = self._ready_check()
        return (200 if ok else 503), {
            "service": self.name,
            "status": "healthy" if ok else "unhealthy",
            "reason": reason,
            "uptime_seconds": round(time.time() - START_TIME, 1),
            "hostname": socket.gethostname(),
        }

    def _info(self, handler, query):
        return 200, {
            "service": self.name,
            "version": self.version,
            "node": os.environ.get("NEXARA_NODE_NAME", socket.gethostname()),
            "node_role": os.environ.get("NEXARA_NODE_ROLE", "unknown"),
            "data_dir": os.environ.get("NEXARA_DATA", "/data"),
            "routes": sorted(f"{m} {p}" for m, p in self.routes),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(START_TIME)),
        }

    # ---------- server ----------

    def serve(self, port: int):
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):
                if self.path != "/health":  # don't spam logs with probes
                    print(f"[{service.name}] {format % args}", flush=True)

            def _dispatch(self, method):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                fn = service.routes.get((method, parsed.path))
                if fn is None:
                    self._send(404, {"error": "not found", "path": parsed.path})
                    return
                try:
                    result = fn(self, query)
                except Exception as e:
                    traceback.print_exc()
                    self._send(500, {"error": str(e), "type": type(e).__name__})
                    return
                # A handler may write the response itself (e.g. HTML) and return None.
                if result is None:
                    return
                status, payload = result
                self._send(status, payload)

            def do_GET(self):
                self._dispatch("GET")

            def do_POST(self):
                self._dispatch("POST")

            def read_json(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                if not length:
                    return {}
                try:
                    return json.loads(self.rfile.read(length).decode())
                except json.JSONDecodeError:
                    return {}

            def _send(self, status, payload):
                body = json.dumps(payload, indent=2, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        Handler.read_json = Handler.read_json
        httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        httpd.daemon_threads = True

        def stop(signum, frame):
            print(f"[{self.name}] signal {signum} received, shutting down", flush=True)
            self._shutdown.set()
            threading.Thread(target=httpd.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        print(f"[{self.name}] listening on :{port} (version {self.version})", flush=True)
        httpd.serve_forever()
        httpd.server_close()
        print(f"[{self.name}] stopped cleanly", flush=True)
