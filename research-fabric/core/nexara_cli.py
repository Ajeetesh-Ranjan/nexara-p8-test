#!/usr/bin/env python3
"""
NEXARA CLI — unified intelligence interface for Phase 8.
Integrates Research Fabric into the NEXARA command structure.

Commands:
  nexara research "<query>" [domain]  — Execute a research request
  nexara summarize [hours]             — Summarize recent AI/Research developments
  nexara investigate <tech>            — Investigate a technology
  nexara opportunities               — Find opportunities in markets
  nexara brain2 [query]                — Query Brain 2 knowledge store
  nexara brain3 [entities|signals]     — Query Brain 3 world model
  nexara wiki [page]                   — Search/Read wiki pages
  nexara lint                          — Lint wiki health
"""
import sys
import os
import json
import argparse
import datetime

# Ensure imports work from any cwd
PIPELINE_ROOT = "/home/ajeetesh/nexara-p8/research-fabric"
sys.path.insert(0, PIPELINE_ROOT)

from core.research_core import ResearchCore, ResearchRequest
from pipeline.research_pipeline import Pipeline
from core.brain_integration import BrainIntegration

KB_PATH = "/home/ajeetesh/nexara-p8/research-fabric/knowledge-base"


def cmd_research(args):
    """Execute a research request."""
    core = ResearchCore(KB_PATH)
    pipeline = Pipeline(core)
    brains = BrainIntegration(KB_PATH)

    request = ResearchRequest(
        query=args.query,
        domain=args.domain,
        requester="nexara-cli"
    )
    rid = core.receive_request(request)
    print(f"\n🔍 Research request: '{args.query}' (domain: {args.domain})")
    artifact = pipeline.execute(request)
    results = brains.process_artifact(artifact)

    print(f"\n✅ Research complete!")
    print(f"  Artifact: research-{artifact.artifact_id}")
    print(f"  Confidence: {artifact.confidence:.2f}")
    print(f"  Sources processed: {len(artifact.sources)}")
    print(f"  Findings: {len(artifact.key_findings)}")
    print(f"  Brain 2 entries: 1")
    print(f"  Brain 3 entities: {results['brain3']}")
    print(f"  Wiki pages: {len(results['wiki_pages'])}")
    print(f"\n  Wiki: {results['wiki_pages'][0].replace(KB_PATH + '/llm-wiki', '~/wiki')}")

    # Print key findings
    if artifact.key_findings:
        print(f"\n📋 Key Findings:")
        for i, f in enumerate(artifact.key_findings[:5], 1):
            print(f"  {i}. {f[:100]}...")

    return artifact


def cmd_summarize(args):
    """Summarize recent AI developments."""
    hours = args.hours or 24
    core = ResearchCore(KB_PATH)
    pipeline = Pipeline(core)
    brains = BrainIntegration(KB_PATH)

    # Check Brain 2 for recent artifacts
    brain2_path = os.path.join(KB_PATH, "brain2_knowledge.json")
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
    recent = []
    if os.path.exists(brain2_path):
        with open(brain2_path) as f:
            knowledge = json.load(f)
        for entry in knowledge:
            created = datetime.datetime.fromisoformat(entry.get("created_at", ""))
            if created > cutoff:
                recent.append(entry)

    print(f"📅 Development summary (last {hours} hours)")
    print(f"  Recent research artifacts in Brain 2: {len(recent)}")
    if recent:
        for entry in recent[-10:]:
            print(f"  - [{entry['artifact_id'][:8]}] {entry['query']}")
            print(f"    confidence={entry['confidence']:.2f}, findings={len(entry['key_findings'])}")

    # Run a fresh collection if none recent
    if not recent:
        print(f"\n  No recent artifacts. Running fresh collection...")
        request = ResearchRequest(
            query="recent AI developments",
            domain="ai",
            requester="nexara-summarize"
        )
        core.receive_request(request)
        artifact = pipeline.execute(request)
        brains.process_artifact(artifact)
        print(f"\n  Fresh artifact: research-{artifact.artifact_id}")
        print(f"  Confidence: {artifact.confidence:.2f}")
        if artifact.key_findings:
            print(f"  Findings:")
            for f in artifact.key_findings[:5]:
                print(f"    - {f[:120]}...")


def cmd_investigate(args):
    """Investigate a technology."""
    core = ResearchCore(KB_PATH)
    pipeline = Pipeline(core)
    brains = BrainIntegration(KB_PATH)

    request = ResearchRequest(
        query=f"Investigate: {args.tech}",
        domain="technology",
        requester="nexara-investigate"
    )
    rid = core.receive_request(request)
    print(f"\n🔬 Investigating: {args.tech}")
    artifact = pipeline.execute(request)
    brains.process_artifact(artifact)
    print(f"\n  Investigation artifact: research-{artifact.artifact_id}")
    print(f"  Confidence: {artifact.confidence:.2f}")
    print(f"  Findings: {len(artifact.key_findings)}")
    print(f"  Recommendations: {len(artifact.recommendations)}")


def cmd_opportunities(args):
    """Find opportunities in markets."""
    import glob
    print("🔍 Recent Opportunity Reports")
    print()

    # Look at wiki pages about protocols/technologies
    wiki_queries = os.path.join(KB_PATH, "llm-wiki/queries")
    if os.path.exists(wiki_queries):
        pages = sorted(glob.glob(f"{wiki_queries}/*.md"), reverse=True)[:10]
        print(f"  Recent research pages ({len(pages)}):")
        for page in pages[:5]:
            print(f"  - {os.path.basename(page).replace('.md','')}")

    # Look at brain3 for trending entities
    brain3_path = os.path.join(KB_PATH, "brain3_world_model.json")
    if os.path.exists(brain3_path):
        with open(brain3_path) as f:
            model = json.load(f)
        print(f"\n  World model: {len(model.get('entities',{}))} entities, {len(model.get('signals',[]))} signals")
        # Show recently seen entities
        entities = model.get("entities", {})
        recent_entities = sorted(entities.items(), key=lambda x: x[1].get("last_seen",""), reverse=True)[:5]
        print(f"  Recently observed:")
        for name, info in recent_entities:
            print(f"  - {name} (via {info.get('source','unknown')})")

    # Generate fresh opportunities from recent research
    brain2_path = os.path.join(KB_PATH, "brain2_knowledge.json")
    if os.path.exists(brain2_path):
        with open(brain2_path) as f:
            knowledge = json.load(f)
        tech_topics = [e["query"] for e in knowledge if "protocol" in e.get("query","").lower() or "MCP" in e.get("query","") or "A2A" in e.get("query","")]
        if tech_topics:
            print(f"\n  Potential opportunity areas from research:")
            for topic in set(tech_topics):
                print(f"  - {topic}")


def cmd_brain2(args):
    """Query Brain 2 knowledge store."""
    brain2_path = os.path.join(KB_PATH, "brain2_knowledge.json")
    if not os.path.exists(brain2_path):
        print("Brain 2 is empty. Run research to populate.")
        return

    with open(brain2_path) as f:
        knowledge = json.load(f)

    if args.query:
        results = [k for k in knowledge if args.query.lower() in k.get("query","").lower() or
                   any(args.query.lower() in f.lower() for f in k.get("key_findings",[]))]
        print(f"Search '{args.query}': {len(results)} results\n")
    else:
        results = knowledge
        print(f"Brain 2 Knowledge Store: {len(results)} entries\n")

    for entry in results:
        print(f"  [{entry['artifact_id'][:8]}] {entry['query']}")
        print(f"    confidence={entry['confidence']:.2f}, sources={len(entry['sources'])}")
        print(f"    findings={len(entry['key_findings'])}, insights={len(entry['insights'])}")
        print(f"    created={entry['created_at']}")
        print()


def cmd_brain3(args):
    """Query Brain 3 world model."""
    brain3_path = os.path.join(KB_PATH, "brain3_world_model.json")
    if not os.path.exists(brain3_path):
        print("Brain 3 is empty. Run research to populate.")
        return

    with open(brain3_path) as f:
        model = json.load(f)

    if args.subcommand == "entities":
        entities = model.get("entities", {})
        print(f"Brain 3 Entities: {len(entities)}\n")
        for name, info in sorted(entities.items(), key=lambda x: x[1].get("last_seen",""), reverse=True)[:20]:
            print(f"  {name}")
            print(f"    first_seen={info.get('first_seen','?')}")
            print(f"    last_seen={info.get('last_seen','?')}")
            print(f"    source={info.get('source','?')}")
    elif args.subcommand == "signals":
        signals = model.get("signals", [])
        print(f"Brain 3 Signals: {len(signals)}\n")
        for s in signals[-20:]:
            print(f"  signal: {s}")
    else:
        print(f"Brain 3: {len(model.get('entities',{}))} entities, {len(model.get('signals',[]))} signals")
        print(f"  Updated: {model.get('updated_at','?')}")


def cmd_wiki(args):
    """Search and read wiki pages."""
    from pathlib import Path
    wiki_path = os.path.join(KB_PATH, "llm-wiki")

    if not args.page:
        # Show index
        index_path = os.path.join(wiki_path, "index.md")
        if os.path.exists(index_path):
            print(open(index_path).read())
        return

    # Find page
    search = args.page.lower()
    for section in ["entities", "concepts", "comparisons", "queries"]:
        section_path = os.path.join(wiki_path, section)
        if os.path.exists(section_path):
            for f in Path(section_path).glob("*.md"):
                if search in f.name.lower() or search in open(f).read().lower()[:200]:
                    print(f"{f.name} ({section}):")
                    content = open(f).read()
                    # Print frontmatter + first part of body
                    print(content[:2000])
                    return


def cmd_lint(args):
    """Lint wiki health."""
    wiki_path = os.path.join(KB_PATH, "llm-wiki")
    issues = []

    # Check required files
    for required in ["SCHEMA.md", "index.md", "log.md"]:
        path = os.path.join(wiki_path, required)
        if not os.path.exists(path):
            issues.append(f"Missing required file: {required}")

    # Check page count vs index
    total_pages = 0
    for section in ["entities", "concepts", "comparisons", "queries"]:
        section_path = os.path.join(wiki_path, section)
        if os.path.exists(section_path):
            pages = [f for f in os.listdir(section_path) if f.endswith(".md")]
            total_pages += len(pages)

    # Check index mentions
    index_path = os.path.join(wiki_path, "index.md")
    if os.path.exists(index_path):
        index_content = open(index_path).read()
        index_count = index_content.count("`[[")
        if index_count < total_pages:
            issues.append(f"Index has {index_count} entries but {total_pages} pages exist")

    # Check frontmatter on pages
    from pathlib import Path
    missing_frontmatter = 0
    for section in ["entities", "concepts", "comparisons", "queries"]:
        section_path = os.path.join(wiki_path, section)
        if os.path.exists(section_path):
            for f in Path(section_path).glob("*.md"):
                content = open(f).read()
                if not content.startswith("---"):
                    missing_frontmatter += 1
    if missing_frontmatter:
        issues.append(f"{missing_frontmatter} pages missing frontmatter")

    # Check log exists and is non-empty
    log_path = os.path.join(wiki_path, "log.md")
    if os.path.exists(log_path):
        lines = open(log_path).readlines()
        if len(lines) < 5:
            issues.append("log.md is very short (<5 lines)")

    print("📋 Wiki Lint Report")
    print("="*50)
    print(f"Pages: {total_pages}")
    print(f"Index entries: {index_count if os.path.exists(index_path) else 0}")
    print(f"Issues found: {len(issues)}")
    if issues:
        for i in issues:
            print(f"  ⚠️  {i}")
    else:
        print("  ✅ All checks passed")


def main():
    parser = argparse.ArgumentParser(
        prog="nexara",
        description="NEXARA Intelligent Research Interface (Phase 8)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  nexara research "MCP vs A2A protocol" technology
  nexara summarize 24
  nexara investigate "transformer architecture"
  nexara opportunities
  nexara brain2 "MCP"
  nexara wiki
  nexara lint
"""
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # research
    p_research = subparsers.add_parser("research", help="Execute a research request")
    p_research.add_argument("query", help="Research query")
    p_research.add_argument("domain", nargs="?", default="technology",
                           choices=["technology", "ai", "open-source", "communities",
                                    "industry-trends", "business", "news"],
                           help="Domain to research")

    # summarize
    p_summarize = subparsers.add_parser("summarize", help="Summarize recent developments")
    p_summarize.add_argument("hours", nargs="?", type=int, default=24, help="Hours of history")

    # investigate
    p_investigate = subparsers.add_parser("investigate", help="Investigate a technology")
    p_investigate.add_argument("tech", help="Technology to investigate")

    # opportunities
    subparsers.add_parser("opportunities", help="Find recent opportunities")

    # brain2
    p_brain2 = subparsers.add_parser("brain2", help="Query Brain 2 knowledge store")
    p_brain2.add_argument("query", nargs="?", help="Search query")

    # brain3
    p_brain3 = subparsers.add_parser("brain3", help="Query Brain 3 world model")
    p_brain3.add_argument("subcommand", nargs="?", default="status",
                         choices=["entities", "signals", "status"],
                         help="What to query")

    # wiki
    p_wiki = subparsers.add_parser("wiki", help="Search/read wiki pages")
    p_wiki.add_argument("page", nargs="?", help="Page name or search term")

    # lint
    subparsers.add_parser("lint", help="Lint wiki health")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    dispatch = {
        "research": cmd_research,
        "summarize": cmd_summarize,
        "investigate": cmd_investigate,
        "opportunities": cmd_opportunities,
        "brain2": cmd_brain2,
        "brain3": cmd_brain3,
        "wiki": cmd_wiki,
        "lint": cmd_lint,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()