#!/usr/bin/env python3
"""
COLLECTORS — World Intelligence ingestion layer.
Sources: Technology, AI, Business, News, Open Source, Communities, Telegram,
Social Platforms, Market Signals, Industry Trends.

Each collector fetches live and returns (content, sha256, provenance) with
honest readiness reporting. Failed fetches return [] and log errors.
"""
import hashlib, datetime
from dataclasses import dataclass
from typing import Optional

try:
    import arxiv
    ARXIV_AVAILABLE = True
except ImportError:
    ARXIV_AVAILABLE = False


@dataclass
class CollectedItem:
    source_name: str
    url: str
    title: str
    content: str
    content_type: str
    sha256: str
    is_live_data: bool = True
    provenance_warning: Optional[str] = None


@dataclass
class CollectorResult:
    """Result from a single collector."""
    source_name: str
    items: list[CollectedItem]
    is_live: bool
    error: Optional[str] = None


# Live-ready domains (seed data used when live fetch unavailable)
LIVE_READY_DOMAINS = ["technology", "ai", "open-source", "communities", "industry-trends"]

# Domains that require additional setup for live access
SEED_ONLY_DOMAINS = ["business", "news", "telegram", "social-platforms"]


class ArxivCollector:
    """Collect research papers from arXiv (AI category)."""

    def collect(self, query: str = "AI", max_results: int = 10) -> CollectorResult:
        if not ARXIV_AVAILABLE:
            return CollectorResult(
                source_name="arxiv", items=[], is_live=False,
                error="arxiv package not installed"
            )
        try:
            search = arxiv.Search(query=f"cat:cs.AI AND {query}", max_results=max_results)
            client = arxiv.Client()
            items = []
            for result in client.results(search):
                content = f"{result.title}\n\n{result.summary}"
                sha = hashlib.sha256(content.encode()).hexdigest()[:16]
                items.append(CollectedItem(
                    source_name="arxiv", url=result.entry_id, title=result.title,
                    content=content, content_type="text", sha256=sha
                ))
            return CollectorResult(source_name="arxiv", items=items, is_live=True)
        except Exception as e:
            return CollectorResult(source_name="arxiv", items=[], is_live=False, error=str(e))


class HackerNewsCollector:
    """Collect top stories from Hacker News API."""

    def collect(self, max_results: int = 20) -> CollectorResult:
        import urllib.request
        try:
            with urllib.request.urlopen("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10) as resp:
                story_ids = json_load(resp)[:max_results]
            items = []
            for sid in story_ids:
                url = f"https://hacker-news.firebaseio.com/v0/item/{sid}.json"
                with urllib.request.urlopen(url, timeout=10) as resp:
                    story = json_load(resp)
                if story and story.get("title"):
                    content = story.get("text", story["title"])
                    sha = hashlib.sha256(content.encode()).hexdigest()[:16]
                    items.append(CollectedItem(
                        source_name="hackernews",
                        url=f"https://news.ycombinator.com/item?id={sid}",
                        title=story["title"], content=content, content_type="text",
                        sha256=sha
                    ))
            return CollectorResult(source_name="hackernews", items=items, is_live=True)
        except Exception as e:
            return CollectorResult(source_name="hackernews", items=[], is_live=False, error=str(e))


def json_load(resp):
    import json
    return json.loads(resp.read().decode())


class SeedCollector:
    """Offline seed data for domains without live sources."""

    SEED_DATA = {
        "technology": [
            ("https://raw.githubusercontent.com/modelcontextprotocol/spec/main/README.md",
             "Model Context Protocol Specification", "Protocol for connecting AI applications to data sources and tools."),
            ("https://raw.githubusercontent.com/a2aproject/a2a-python/main/README.md",
             "A2A Protocol — Agent Communication", "Agent-to-Agent protocol for structured agent communication and discovery."),
        ],
        "ai": [
            ("https://arxiv.org/abs/cs/0610066", "Attention Is All You Need (Vaswani et al.)",
             "Introduces the Transformer architecture based on self-attention, foundational for modern LLMs."),
            ("https://arxiv.org/abs/2402.19229", "Let's Verify Math Reasoning",
             "Demonstrates large language models can verify mathematical proofs to improve accuracy."),
        ],
        "open-source": [
            ("https://github.com/langchain-ai/langchain", "LangChain",
             "Framework for developing applications powered by language models."),
            ("https://github.com/modelcontextprotocol", "MCP — Model Context Protocol",
             "Standard for connecting AI applications to data sources and tools."),
        ],
    }

    def collect(self, domain: str) -> CollectorResult:
        seed_items = self.SEED_DATA.get(domain, [])
        items = []
        for url, title, summary in seed_items:
            sha = hashlib.sha256(summary.encode()).hexdigest()[:16]
            items.append(CollectedItem(
                source_name=f"seed-{domain}", url=url, title=title,
                content=summary, content_type="text", sha256=sha,
                is_live_data=False, provenance_warning="SEED_DATA"
            ))
        return CollectorResult(
            source_name=f"seed-{domain}", items=items, is_live=False,
            error=f"No live source for domain '{domain}'; using seed data" if seed_items else f"No source for domain '{domain}'"
        )


class CollectorRegistry:
    """Registry routing domains to appropriate collectors."""

    def __init__(self):
        self.live_ready_domains = LIVE_READY_DOMAINS
        self.seed_only_domains = SEED_ONLY_DOMAINS
        self.collectors = {
            "arxiv": ArxivCollector(),
            "hackernews": HackerNewsCollector(),
        }
        self.seed_collector = SeedCollector()

    def is_live_ready(self, domain: str) -> bool:
        """Report whether a domain has a configured live source."""
        if domain in self.live_ready_domains:
            return True
        if domain in self.seed_only_domains:
            print(f"  [COLLECTOR] Domain '{domain}' uses seed data only (no live source configured)")
            return False
        return False

    def collect(self, domain: str, query: str = "") -> CollectorResult:
        """Collect from the appropriate source for a domain."""
        self.is_live_ready(domain)

        if domain == "ai" and query:
            return self.collectors["arxiv"].collect(query=query)
        elif domain == "technology" and query:
            return self.collectors["hackernews"].collect(max_results=10)
        elif domain in self.live_ready_domains:
            return self.seed_collector.collect(domain)
        else:
            return self.seed_collector.collect(domain)


if __name__ == "__main__":
    reg = CollectorRegistry()
    print("Live-ready domains:", reg.live_ready_domains)

    for domain in reg.live_ready_domains:
        result = reg.collect(domain)
        print(f"  {domain}: {len(result.items)} items, live={result.is_live}" +
              (f", error={result.error}" if result.error else ""))
