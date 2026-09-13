#!/usr/bin/env python3
"""Live end-to-end check: run the real world-intelligence service in a
subprocess and drive every CLI command and dashboard view against it over HTTP.

Fixture intelligence only; writes to a temp NEXARA_DATA and never touches /data.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = "/home/ajeetesh/nexara-p8"
WI_PORT = os.environ.get("LIVE_WI_PORT", "8199")
DASH_PORT = os.environ.get("LIVE_DASH_PORT", "8198")


def opp(i, kind="research", overall=80):
    return {"id": i, "title": f"{i}: research hypothesis", "kind": kind, "confidence": 0.7,
            "thesis": "Unvalidated hypothesis", "entities": ["Fixture Protocol"],
            "distinct_sources": 2,
            "scores": {"impact": 80, "confidence": 70, "effort": 30, "risk": 20,
                       "time_to_value": 75, "strategic_alignment": 90, "overall": overall},
            "is_hypothesis": True, "risk_band": "Low", "decision": "shortlist",
            "monetary_estimate": None, "is_live_data": False,
            "next_step": "Check the primary documentation", "risks": ["Needs validation"],
            "questions": {k: "unassessed" for k in
                          ("build", "learn", "automate", "market", "research",
                           "revenue", "improve_nexara")},
            "evidence": [{"title": "Primary source", "source": "fixture",
                          "url": "https://example.org/e?a=1&b=2"}]}


def sig(i, kind, etype="company"):
    return {"id": i, "entity": f"Entity {i}", "entity_type": etype, "kind": kind,
            "strength": 0.72, "mentions": 4, "distinct_sources": 2,
            "source_names": ["a.example", "b.example"], "categories": ["technology"],
            "rationale": "fixture rationale", "is_single_source": False,
            "first_seen": 1.0, "last_seen": 2.0,
            "evidence": [{"title": "Ev", "source": "fixture",
                          "url": "https://example.org/1"}]}


def seed(data_dir):
    wi_dir = os.path.join(data_dir, "world-intelligence")
    os.makedirs(wi_dir, exist_ok=True)
    snapshot = {
        "version": 11, "seen": {}, "items": [],
        "signals": [sig("s1", "risk"), sig("s2", "emergence"),
                    sig("s3", "momentum", "protocol")],
        "opportunities": [opp("o1", "research", 92), opp("o2", "build", 70)],
        "evaluations": [],
        "last_run": {"started_at": time.time(), "collected": 5, "new_items": 5,
                     "distinct_entities": 3, "signals": 3, "opportunities": 2,
                     "sources": [{"source": "fixture", "category": "technology",
                                  "items": 5, "configured": True, "ok": True, "error": ""}]},
    }
    with open(os.path.join(wi_dir, "snapshot.json"), "w") as fh:
        json.dump(snapshot, fh)
    # baseline gives /ask/accelerating >=2 windows so trends are non-empty
    with open(os.path.join(wi_dir, "baseline.json"), "w") as fh:
        json.dump({"Fixture Protocol": {"mean_mentions": 3.5, "windows": 3, "total": 10}}, fh)


def wait_http(url, tries=80):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                return r.status == 200
        except Exception:
            time.sleep(0.15)
    return False


def main():
    data_dir = tempfile.mkdtemp(prefix="nexara-live-")
    seed(data_dir)
    env = {**os.environ, "NEXARA_DATA": data_dir, "NEXARA_WI_AUTOSTART": "0",
           "NEXARA_BRAIN_URL": "http://127.0.0.1:1", "PYTHONPATH": ROOT}
    procs = []
    failures = []
    try:
        procs.append(subprocess.Popen(
            [sys.executable, f"{ROOT}/services/world_intelligence/main.py"],
            env={**env, "PORT": WI_PORT}, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
        if not wait_http(f"http://127.0.0.1:{WI_PORT}/health"):
            print("world-intelligence failed to start:",
                  procs[0].stderr.read().decode()[-800:])
            return 1
        procs.append(subprocess.Popen(
            [sys.executable, f"{ROOT}/services/dashboard/main.py"],
            env={**env, "PORT": DASH_PORT,
                 "NEXARA_WI_URL": f"http://127.0.0.1:{WI_PORT}"},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
        if not wait_http(f"http://127.0.0.1:{DASH_PORT}/health"):
            print("dashboard failed to start:", procs[1].stderr.read().decode()[-800:])
            return 1
        print(f"world-intelligence :{WI_PORT} and dashboard :{DASH_PORT} live\n")

        cli_env = {**env, "NEXARA_WI_URL": f"http://127.0.0.1:{WI_PORT}"}

        def run_cli(args):
            return subprocess.run([sys.executable, f"{ROOT}/scripts/nexara_world.py", *args],
                                  capture_output=True, text=True, env=cli_env, timeout=60)

        print("EIGHT NEXARA QUESTIONS (natural language)")
        questions = ["What opportunities exist?", "Show highest-value opportunities",
                     "What should we build?", "What should we monitor?",
                     "What communities are growing?", "Which companies should we watch?",
                     "Which technologies are accelerating?", "What changed today?"]
        for q in questions:
            r = run_cli(["ask", q, "--limit", "3"])
            ok = r.returncode == 0 and "Traceback" not in r.stderr
            print(f"  {'PASS' if ok else 'FAIL'}  ask {q!r}")
            if not ok:
                failures.append((f"ask {q}", (r.stderr or r.stdout)[-500:]))

        print("\nPHASE 10 COMMANDS + REGISTRY OPERATIONS")
        for cmd in (["today"], ["matters"], ["opportunities"], ["threats"], ["research"],
                    ["monitor"], ["accelerating"], ["companies"], ["sources"],
                    ["channels", "list"], ["topics", "list"]):
            r = run_cli(cmd)
            ok = r.returncode == 0 and "Traceback" not in r.stderr
            print(f"  {'PASS' if ok else 'FAIL'}  {' '.join(cmd)}")
            if not ok:
                failures.append((" ".join(cmd), (r.stderr or r.stdout)[-500:]))

        print("\nREGISTRY WRITE (CLI -> world-intelligence)")
        channel = json.dumps({
            "id": "livecheck", "username": "livecheckchannel", "title": "Live Check",
            "url": "https://t.me/s/livecheckchannel", "category": "technology",
            "topics": ["technology"], "enabled": True, "publisher": "example.org",
            "description": "Fixture channel for the live surface check.",
            "provenance": {"primary_url": "https://example.org/", "verified_at": "2026-09-11",
                           "verification": "publisher-linked"}})
        r = run_cli(["channels", "add", channel])
        listed = run_cli(["channels", "list"])
        ok = r.returncode == 0 and "livecheck" in listed.stdout
        print(f"  {'PASS' if ok else 'FAIL'}  channels add -> list round-trip")
        if not ok:
            failures.append(("channels add", (r.stderr or r.stdout)[-500:]))

        # A rejected write must explain itself, not just print a status line.
        bad = json.dumps({"id": "livecheck", "username": "livecheckchannel",
                          "title": "Live Check", "url": "https://t.me/s/livecheckchannel",
                          "category": "technology", "topics": ["technology"],
                          "enabled": True, "publisher": "example.org",
                          "description": "Invalid provenance on purpose.",
                          "provenance": {"primary_url": "https://example.org/",
                                         "verified_at": "2026-09-11",
                                         "verification": "not-a-real-method"}})
        r = run_cli(["channels", "add", bad])
        ok = r.returncode != 0 and "verification" in (r.stdout + r.stderr)
        print(f"  {'PASS' if ok else 'FAIL'}  invalid channel rejected with a stated reason")
        if not ok:
            failures.append(("channels add invalid", (r.stdout + r.stderr)[-500:]))

        print("\nSIX READ-ONLY DASHBOARD VIEWS")
        views = ["opportunities", "signals", "trends", "entities",
                 "risk-signals", "research-queue"]
        keys = {"opportunities": "opportunities", "signals": "signals", "trends": "entities",
                "entities": "top_activity", "risk-signals": "signals",
                "research-queue": "candidates"}
        for view in views:
            url = f"http://127.0.0.1:{DASH_PORT}/api/intelligence/{view}?limit=5"
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    body = json.loads(resp.read().decode())
                key = keys[view]
                ok = key in body and isinstance(body[key], list)
                n = len(body.get(key, []))
                print(f"  {'PASS' if ok else 'FAIL'}  GET /api/intelligence/{view:16} "
                      f"-> {n} row(s) under '{key}'")
                if not ok:
                    failures.append((f"json {view}", f"missing '{key}' in {sorted(body)}"))
            except Exception as exc:
                print(f"  FAIL  GET /api/intelligence/{view}: {exc}")
                failures.append((f"json {view}", str(exc)))

        # risk-signals must actually filter, not silently return everything/nothing
        with urllib.request.urlopen(
                f"http://127.0.0.1:{DASH_PORT}/api/intelligence/risk-signals?limit=5") as resp:
            risk = json.loads(resp.read().decode())
        kinds = {r["kind"] for r in risk.get("signals", [])}
        ok = kinds == {"risk"}
        print(f"  {'PASS' if ok else 'FAIL'}  risk-signals filter yields only kind=risk -> {kinds}")
        if not ok:
            failures.append(("risk filter", f"kinds={kinds}"))

        print("\nHTML VIEWS")
        for view in views:
            url = f"http://127.0.0.1:{DASH_PORT}/intelligence/{view}"
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    html = resp.read().decode()
                ok = resp.status == 200 and "Read-only" in html and "<form" not in html.lower()
                print(f"  {'PASS' if ok else 'FAIL'}  GET /intelligence/{view}")
                if not ok:
                    failures.append((f"html {view}", html[:300]))
            except Exception as exc:
                print(f"  FAIL  GET /intelligence/{view}: {exc}")
                failures.append((f"html {view}", str(exc)))

        print("\nMUTATION REFUSAL")
        for method in ("POST", "DELETE", "PUT", "PATCH"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{DASH_PORT}/api/intelligence/opportunities",
                data=b"{}", method=method)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    code = resp.status
            except urllib.error.HTTPError as exc:
                code = exc.code
            except Exception:
                code = "refused"
            ok = code != 200
            print(f"  {'PASS' if ok else 'FAIL'}  {method} /api/intelligence/opportunities -> {code}")
            if not ok:
                failures.append((f"mutation {method}", "accepted"))

        print("\nBOUNDED LIMIT")
        for bad in ("0", "101", "banana", ""):
            url = f"http://127.0.0.1:{DASH_PORT}/api/intelligence/signals?limit={bad}"
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    code = resp.status
            except urllib.error.HTTPError as exc:
                code = exc.code
            ok = code == 400
            print(f"  {'PASS' if ok else 'FAIL'}  limit={bad!r} -> {code}")
            if not ok:
                failures.append((f"limit {bad}", str(code)))
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    print("\n" + ("=" * 60))
    if failures:
        print(f"FAILURES: {len(failures)}")
        for name, detail in failures:
            print(f"\n--- {name} ---\n{detail}")
        return 1
    print("ALL LIVE SURFACE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
