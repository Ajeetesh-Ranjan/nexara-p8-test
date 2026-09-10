#!/usr/bin/env python3
"""
Phase 10 test suite — verifies World Intelligence behaviour, especially the
honesty properties. Run: python3 -m tests.test_world_intelligence
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.world_intelligence.entities import extract, lexicon_size
from services.world_intelligence.engine import SignalEngine, OpportunityEngine
from services.world_intelligence import collectors as C

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail and not cond else ""))


def item(title, url, source, text=""):
    return {"title": title, "text": text, "url": url, "source": source,
            "category": "technology", "published_at": time.time(), "fetched_at": time.time()}


def ob(entity, etype, it, method="lexicon"):
    return {"entity": entity, "type": etype, "method": method, "weight": 1.0, "item": it}


print("\n=== 1. Entity extraction ===")
e = extract("Show HN: MCP server with vLLM and Qdrant, using MoE and RAG", "")
names = {x.name for x in e}
check("alias MCP -> Model Context Protocol", "Model Context Protocol" in names)
check("alias MoE -> Mixture of Experts", "Mixture of Experts" in names)
check("alias RAG -> Retrieval Augmented Generation", "Retrieval Augmented Generation" in names)
check("lexicon hits weight 1.0", all(x.weight == 1.0 for x in e if x.method == "lexicon"))

noise = extract("Here is a new Language Model and There Are Many Ways To Build Systems", "")
noise_names = {x.name for x in noise}
check("stopword 'Here' rejected", "Here" not in noise_names)
check("generic 'Language' rejected", "Language" not in noise_names)
check("verb phrase 'Build Systems' rejected", "Build Systems" not in noise_names)

gh = extract("cool project", "https://github.com/foo/bar")
check("github repo extracted from URL", any(x.name == "foo/bar" for x in gh))
check("lexicon non-trivial", lexicon_size()["canonical_entities"] >= 80,
      str(lexicon_size()))

print("\n=== 2. Signal detection ===")
obs = [ob("Model Context Protocol", "protocol", item(f"MCP news {i}", f"u{i}", s))
       for i, s in enumerate(["hn", "arxiv", "github"])]
sigs = SignalEngine({}).detect(obs)
check("multi-source entity produces signal", len(sigs) >= 1)
check("emergence when no baseline", any(s.kind == "emergence" for s in sigs))
check("distinct_sources counted correctly", sigs[0].distinct_sources == 3 if sigs else False)
check("evidence carries urls", all(all(ev.get("url") for ev in s.evidence) for s in sigs))

single = SignalEngine({}).detect([ob("Qdrant", "infrastructure", item("q", "u", "hn"))])
check("single-source heuristic suppressed",
      all(not s.is_single_source or s.kind == "risk" for s in single) or len(single) == 0)

heur_only = SignalEngine({}).detect([
    ob("Weird Thing", "unclassified", item("a", "u1", "hn"), method="heuristic")])
check("heuristic entity w/ 1 source produces nothing", len(heur_only) == 0)

print("\n=== 3. Baseline / momentum (measured, not asserted) ===")
base = {"Model Context Protocol": {"mean_mentions": 2.0, "windows": 1, "total": 2}}
many = [ob("Model Context Protocol", "protocol",
           item(f"MCP {i}", f"m{i}", ["hn", "arxiv", "github", "biz"][i % 4]))
        for i in range(8)]
msigs = SignalEngine(base).detect(many)
mom = [s for s in msigs if s.kind == "momentum"]
check("momentum detected vs stored baseline", len(mom) >= 1)
check("momentum rationale cites the ratio", bool(mom) and "x)" in mom[0].rationale,
      mom[0].rationale if mom else "no momentum signal")

print("\n=== 4. Risk attribution (proximity, not co-occurrence) ===")
far = SignalEngine({}).detect([
    ob("Amazon", "company", item("Amazon pilots ad services in ChatGPT", "r1", "hn")),
    ob("Amazon", "company", item("Canada to Review Amazon Contracts After Quebec Layoffs", "r2", "biz")),
])
check("unrelated risk language does NOT create a threat",
      not any(s.kind == "risk" for s in far))

near = SignalEngine({}).detect([
    ob("Redis", "infrastructure", item("Redis license change angers users", "r3", "hn")),
    ob("Redis", "infrastructure", item("CVE-2026-1234 in Redis, exploit public", "r4", "biz")),
])
check("genuine risk near entity IS detected", any(s.kind == "risk" for s in near))

print("\n=== 5. Opportunity honesty ===")
opps = OpportunityEngine().generate(SignalEngine({}).detect(obs))
check("opportunities generated", len(opps) >= 1)
check("all flagged is_hypothesis", all(o.is_hypothesis for o in opps))
check("confidence capped at 0.85", all(o.confidence <= 0.85 for o in opps))
check("all answer six questions", all(len(o.questions) == 6 for o in opps))
check("all state risks", all(len(o.risks) >= 1 for o in opps))
check("no 'no weakness' claim",
      all("No structural weakness" not in " ".join(o.risks) for o in opps))
check("evidence cited with urls",
      all(o.evidence and all(ev.get("url") for ev in o.evidence) for o in opps))

uncorroborated = OpportunityEngine().generate(
    SignalEngine({}).detect([ob("Ollama", "framework", item("x", "u9", "hn"))]))
check("single-source signal yields NO opportunity", len(uncorroborated) == 0)

print("\n=== 6. Collector honesty ===")
tg = C.Telegram()
res = tg.collect()
check("telegram reports unconfigured without token",
      (not res.configured and not res.ok) if not os.environ.get("TELEGRAM_BOT_TOKEN") else True)
check("unconfigured source returns zero items, not fake data", len(res.items) == 0)
check("error message points to docs",
      "docs/" in res.error if not os.environ.get("TELEGRAM_BOT_TOKEN") else True)

print("\n=== RESULT ===")
print(f"  passed: {len(PASS)}")
print(f"  failed: {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print(f"    - {f}")
sys.exit(1 if FAIL else 0)
