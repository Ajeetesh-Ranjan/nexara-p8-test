#!/usr/bin/env python3
"""
nexara-world — CLI for World Intelligence operations.

  nexara-world cycle                    run a collection/analysis cycle now
  nexara-world today                    what happened today
  nexara-world matters                  what matters today
  nexara-world opportunities [--kind]   what opportunities exist
  nexara-world threats                  what threats exist
  nexara-world research                 what should we research
  nexara-world monitor                  what should we monitor
  nexara-world accelerating             which technologies are accelerating
  nexara-world companies                which companies to watch
  nexara-world sources                  source health and configuration
  nexara-world channels list            list registered channels
  nexara-world channels add <json>      upsert a channel
  nexara-world channels update <json>   upsert a channel (alias)
  nexara-world topics list              list registered topics
  nexara-world topics add <json>        upsert a topic
  nexara-world topics update <json>     upsert a topic (alias)
  nexara-world ask <question>           ask one of the 8 intelligence questions
    opportunities      What opportunities exist? (with scores + hypothesis flags)
    highest-value      Show highest-value opportunities (overall score)
    build              What should we build? (kind=build)
    monitor            What should we monitor?
    communities        What communities are growing?
    changes            What changed today?
    accelerating       Which technologies are accelerating?
    companies          Which companies should we watch?
"""
import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
import urllib.parse
from typing import NoReturn

WI = os.environ.get("NEXARA_WI_URL", "http://localhost:8086")

C = {"dim": "\033[2m", "b": "\033[1m", "g": "\033[32m", "y": "\033[33m",
     "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}
if not sys.stdout.isatty():
    C = {k: "" for k in C}


def _fail(message: str, detail=None) -> NoReturn:
    """Report the upstream reason, not just the status line, then exit non-zero."""
    print(f"{C['r']}{message}{C['x']}")
    if detail:
        print(f"  {detail}")
    sys.exit(1)


def _error_detail(exc) -> str:
    """Extract the server's own error message from an HTTPError body."""
    body = getattr(exc, "read", None)
    if body is None:
        return str(exc)
    try:
        payload = json.loads(body().decode())
    except (ValueError, UnicodeError, OSError):
        return str(exc)
    if isinstance(payload, dict):
        return str(payload.get("error") or payload.get("upstream") or payload)
    return str(payload)


def get(path: str, timeout: int = 300):
    try:
        with urllib.request.urlopen(f"{WI}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        _fail(f"world-intelligence rejected GET {path} (HTTP {e.code})", _error_detail(e))
    except Exception as e:
        _fail(f"cannot reach world-intelligence at {WI}: {e}",
              "is the stack up?  docker compose ps")


def post(path: str, payload: dict | None = None, timeout: int = 600):
    data = b"{}" if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(f"{WI}{path}", data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        _fail(f"world-intelligence rejected POST {path} (HTTP {e.code})", _error_detail(e))
    except Exception as e:
        _fail(f"request failed: {e}")


def bar(v: float, width: int = 12) -> str:
    filled = int(round(v * width))
    colour = C["g"] if v >= 0.7 else (C["y"] if v >= 0.4 else C["dim"])
    return f"{colour}{'█' * filled}{C['dim']}{'·' * (width - filled)}{C['x']}"


def hdr(text: str, sub: str = ""):
    print(f"\n{C['b']}{text}{C['x']}")
    if sub:
        print(f"{C['dim']}{sub}{C['x']}")
    print()


def cmd_cycle(a):
    print("running cycle (collect -> extract -> signals -> opportunities) ...")
    s = post("/cycle")
    if s.get("skipped"):
        print(f"{C['y']}{s['skipped']}{C['x']}")
        return
    hdr("CYCLE COMPLETE", f"{s['duration_seconds']}s")
    print(f"  collected      {s['collected']} items ({s['new_items']} new after dedupe)")
    print(f"  entities       {s['distinct_entities']} distinct from {s['observations']} observations")
    print(f"  signals        {s['signals']}  {C['dim']}{s['signals_by_kind']}{C['x']}")
    print(f"  opportunities  {s['opportunities']}  {C['dim']}{s['opportunities_by_kind']}{C['x']}")
    p = s.get("pushed", {})
    print(f"  brain 2        +{p.get('brain2',0)} artifacts")
    print(f"  brain 3        +{p.get('brain3',0)} entities")
    if p.get("errors"):
        print(f"  {C['r']}errors: {p['errors'][:2]}{C['x']}")
    print(f"\n{C['dim']}sources:{C['x']}")
    for r in s["sources"]:
        state = f"{C['g']}ok{C['x']}" if r["ok"] else (
            f"{C['y']}unconfigured{C['x']}" if not r["configured"] else f"{C['r']}FAIL{C['x']}")
        print(f"  {r['source']:16} {r['items']:4}  {state}"
              + (f"  {C['dim']}{r['error'][:60]}{C['x']}" if r["error"] else ""))


def cmd_today(a):
    d = get("/ask/today")
    w = d["window"]
    hdr("WHAT HAPPENED TODAY",
        f"{w['items_collected']} items collected, {w['new_items']} new, "
        f"{d['entities_observed']} entities observed")
    for t in d["top_activity"]:
        print(f"  {C['c']}{t['entity']}{C['x']}  {C['dim']}{t['mentions']} mentions / "
              f"{t['sources']} sources{C['x']}")
        for e in t["evidence"][:1]:
            print(f"    {C['dim']}[{e['source']}]{C['x']} {e['title'][:78]}")


def cmd_matters(a):
    d = get("/ask/matters")
    hdr("WHAT MATTERS TODAY", d["basis"])
    for s in d["signals"]:
        print(f"  {bar(s['strength'])} {s['strength']:.2f}  {C['b']}{s['entity']}{C['x']} "
              f"{C['dim']}({s['kind']}){C['x']}")
        print(f"    {C['dim']}{s['why']}{C['x']}")
        print(f"    {C['dim']}sources: {', '.join(s['sources'])}{C['x']}")


def cmd_opportunities(a):
    q = f"?kind={a.kind}" if a.kind else ""
    d = get(f"/ask/opportunities{q}")
    hdr("WHAT OPPORTUNITIES EXIST", d["caveat"])
    for o in d["opportunities"]:
        print(f"  {bar(o['confidence'])} {o['confidence']:.2f}  {C['b']}{o['title']}{C['x']} "
              f"{C['dim']}[{o['kind']}]{C['x']}")
        print(f"    {o['thesis'][:150]}")
        print(f"    {C['g']}next:{C['x']} {o['next_step']}")
        for r in o["risks"][:2]:
            print(f"    {C['y']}risk:{C['x']} {C['dim']}{r}{C['x']}")
        print()


def cmd_threats(a):
    d = get("/ask/threats")
    hdr("WHAT THREATS EXIST", d["basis"])
    if not d["threats"]:
        print(f"  {C['dim']}no risk-language signals in the current window{C['x']}")
        return
    for t in d["threats"]:
        print(f"  {bar(t['strength'])} {t['strength']:.2f}  {C['r']}{t['entity']}{C['x']}")
        print(f"    {C['dim']}{t['why']}{C['x']}")
        for e in t["evidence"][:2]:
            print(f"    {C['dim']}[{e['source']}] {e['title'][:74]}{C['x']}")


def cmd_research(a):
    d = get("/ask/research")
    hdr("WHAT SHOULD WE RESEARCH", "candidates ranked by signal confidence")
    for c in d["candidates"]:
        print(f"  {C['c']}{c['topic']}{C['x']} {C['dim']}({c['confidence']:.2f}){C['x']}")
        print(f"    {C['dim']}{c['why'][:130]}{C['x']}")
        print(f"    {C['g']}${C['x']} {c['command']}")


def cmd_monitor(a):
    d = get("/ask/monitor")
    hdr("WHAT SHOULD WE MONITOR", d["basis"])
    for w in d["watchlist"]:
        print(f"  {bar(w['strength'])} {w['strength']:.2f}  {w['entity']} "
              f"{C['dim']}({w['kind']}, {w['sources']} src){C['x']}")


def cmd_accelerating(a):
    d = get("/ask/accelerating")
    hdr("WHICH TECHNOLOGIES ARE ACCELERATING", d["basis"])
    if not d["entities"]:
        print(f"  {C['y']}not enough history yet{C['x']}")
        print(f"  {C['dim']}{d['note']}{C['x']}")
        print(f"  {C['dim']}run 'nexara-world cycle' again later to build a baseline{C['x']}")
        return
    for e in d["entities"]:
        print(f"  {C['c']}{e['entity']:32}{C['x']} mean {e['mean_mentions']:>6.2f}/window "
              f"{C['dim']}over {e['windows']} windows, {e['total_mentions']} total{C['x']}")


def cmd_companies(a):
    d = get("/ask/companies")
    hdr("WHICH COMPANIES SHOULD WE WATCH", d.get("caveat", ""))
    rows = d.get("communities", [])
    if not rows:
        print(f"  {C['dim']}no company signals recorded. This is not evidence of absence.{C['x']}")
        return
    for c in rows:
        # Upstream returns signal rows: the name is 'entity', sources are 'source_names'.
        name = c.get("entity", c.get("company", "?"))
        sources = c.get("source_names", c.get("sources", []))
        print(f"  {bar(c['strength'])} {c['strength']:.2f}  {C['b']}{name}{C['x']} "
              f"{C['dim']}{c.get('mentions', 0)} mentions / {len(sources)} sources{C['x']}")
        for e in c.get("evidence", [])[:1]:
            print(f"    {C['dim']}[{e.get('source','?')}] {e.get('title','')[:76]}{C['x']}")


def cmd_sources(a):
    d = get("/cycle")
    last = d.get("last_run", {})
    hdr("SOURCE HEALTH", f"cycle interval {d['interval_seconds']}s · "
                         f"running={d['running']}")
    for r in last.get("sources", []):
        state = f"{C['g']}ok          {C['x']}" if r["ok"] else (
            f"{C['y']}unconfigured{C['x']}" if not r["configured"] else f"{C['r']}FAIL        {C['x']}")
        print(f"  {r['source']:18} {r['category']:12} {r['items']:4} items  {state}")
        if r["error"]:
            print(f"    {C['dim']}{r['error'][:100]}{C['x']}")


# ------------------------- registry commands -------------------------

def cmd_channels(a):
    if a.channels_cmd == "list":
        d = get("/registry/channels")
        hdr("REGISTERED CHANNELS")
        for c in d["channels"]:
            print(f"  {c['id']}  @{c['username']}  {c['category']}  topics={c['topics']}")
    elif a.channels_cmd in ("add", "update"):
        payload = json.loads(a.payload)
        d = post("/registry/channels", payload)
        print(f"{C['g']}channel upserted{C['x']}: {d}")
    else:
        a.parser.print_help()


def cmd_topics(a):
    if a.topics_cmd == "list":
        d = get("/registry/topics")
        hdr("REGISTERED TOPICS")
        for t in d["topics"]:
            print(f"  {t['id']}  {t['name']}  keywords={t['keywords']}")
    elif a.topics_cmd in ("add", "update"):
        payload = json.loads(a.payload)
        d = post("/registry/topics", payload)
        print(f"{C['g']}topic upserted{C['x']}: {d}")
    else:
        a.parser.print_help()


# ------------------------- ask commands -------------------------

ASK_ALIASES = {
    "opportunities": ("/ask/opportunities", "opportunities", "What opportunities exist?"),
    "communities": ("/ask/communities", "communities", "What communities are growing?"),
    "monitor": ("/ask/monitor", "watchlist", "What should we monitor?"),
    "build": ("/ask/build", "opportunities", "What should we build?"),
    "today": ("/ask/today", "changes", "What changed today?"),
    "accelerating": ("/ask/accelerating", "entities", "Which technologies are accelerating?"),
    "companies": ("/ask/companies", "communities", "Which companies should we watch?"),
    "highest-value": ("/ask/highest-value", "opportunities", "Show highest-value opportunities"),
}


def _question(value):
    text = value if isinstance(value, str) else " ".join(value)
    text = " ".join(text.lower().strip().rstrip("?!.").split())
    phrases = {" ".join(title.lower().rstrip("?!.").split()): alias
               for alias, (_, _, title) in ASK_ALIASES.items()}
    phrases.update({"changes": "today", "what happened today": "today",
                    "which communities are growing": "communities",
                    "show highest value opportunities": "highest-value",
                    "what are the highest-value opportunities": "highest-value"})
    alias = phrases.get(text, text)
    if alias not in ASK_ALIASES:
        raise ValueError("Unknown intelligence question; use: " + ", ".join(ASK_ALIASES))
    return alias


def _render_answer(data, key, title):
    hdr(title)
    # Keep evidence, all seven scores, provenance, nulls and failure caveats intact.
    print(json.dumps({k: v for k, v in data.items() if k != key}, indent=2, ensure_ascii=False))
    if not data.get(key):
        print("No items recorded for this view. This is not evidence of absence.")
    else:
        for row in data[key]:
            print(json.dumps(row, indent=2, ensure_ascii=False))


def cmd_ask(a):
    path, key, title = ASK_ALIASES[_question(a.question)]
    d = get(path + "?" + urllib.parse.urlencode({"limit": a.limit}))
    _render_answer(d, key, title)


def build_parser():
    p = argparse.ArgumentParser(prog="nexara-world",
                                description="NEXARA World Intelligence")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("cycle", help="run a collection/analysis cycle")
    sub.add_parser("today", help="what happened today")
    sub.add_parser("matters", help="what matters today")
    o = sub.add_parser("opportunities", help="what opportunities exist")
    o.add_argument("--kind", choices=["build", "learn", "automate", "invest", "research"])
    sub.add_parser("threats", help="what threats exist")
    sub.add_parser("research", help="what should we research")
    sub.add_parser("monitor", help="what should we monitor")
    sub.add_parser("accelerating", help="which technologies are accelerating")
    sub.add_parser("companies", help="which companies to watch")
    sub.add_parser("sources", help="source health")

    # channels subcommands
    ch = sub.add_parser("channels", help="manage registered channels")
    ch_sub = ch.add_subparsers(dest="channels_cmd")
    ch_sub.add_parser("list", help="list channels")
    add_ch = ch_sub.add_parser("add", help="upsert a channel (JSON)")
    add_ch.add_argument("payload", help="channel JSON dict")
    upd_ch = ch_sub.add_parser("update", help="upsert a channel (JSON)")
    upd_ch.add_argument("payload", help="channel JSON dict")

    # topics subcommands
    tp = sub.add_parser("topics", help="manage registered topics")
    tp_sub = tp.add_subparsers(dest="topics_cmd")
    tp_sub.add_parser("list", help="list topics")
    add_tp = tp_sub.add_parser("add", help="upsert a topic (JSON)")
    add_tp.add_argument("payload", help="topic JSON dict")
    upd_tp = tp_sub.add_parser("update", help="upsert a topic (JSON)")
    upd_tp.add_argument("payload", help="topic JSON dict")

    # ask subcommand with 8 questions
    ask = sub.add_parser("ask", help="ask one of the 8 intelligence questions")
    ask.add_argument("question", nargs="+", help="question alias or natural-language phrase")
    ask.add_argument("--limit", type=int, default=20)

    return p


def main():
    p = build_parser()
    a = p.parse_args()
    if not a.cmd:
        p.print_help()
        return
    if a.cmd == "channels":
        a.parser = p
        cmd_channels(a)
    elif a.cmd == "topics":
        a.parser = p
        cmd_topics(a)
    elif a.cmd == "ask":
        cmd_ask(a)
    else:
        globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    main()