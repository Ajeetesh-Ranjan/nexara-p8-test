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
"""
import argparse
import json
import os
import sys
import urllib.request

WI = os.environ.get("NEXARA_WI_URL", "http://localhost:8086")

C = {"dim": "\033[2m", "b": "\033[1m", "g": "\033[32m", "y": "\033[33m",
     "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}
if not sys.stdout.isatty():
    C = {k: "" for k in C}


def get(path: str, timeout: int = 300):
    try:
        with urllib.request.urlopen(f"{WI}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"{C['r']}cannot reach world-intelligence at {WI}{C['x']}: {e}")
        print(f"{C['dim']}is the stack up?  docker compose ps{C['x']}")
        sys.exit(1)


def post(path: str, timeout: int = 600):
    req = urllib.request.Request(f"{WI}{path}", data=b"{}",
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"{C['r']}request failed{C['x']}: {e}")
        sys.exit(1)


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
    hdr("WHICH COMPANIES SHOULD WE WATCH", "companies appearing in multi-source signals")
    for c in d["companies"]:
        print(f"  {bar(c['strength'])} {c['strength']:.2f}  {C['b']}{c['company']}{C['x']} "
              f"{C['dim']}{c['mentions']} mentions / {len(c['sources'])} sources{C['x']}")
        for e in c["evidence"][:1]:
            print(f"    {C['dim']}[{e['source']}] {e['title'][:76]}{C['x']}")


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


def main():
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
    a = p.parse_args()
    if not a.cmd:
        p.print_help()
        return
    globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    main()
