#!/usr/bin/env python3
"""
RESEARCH_FABRIC — Core pipeline module.
Operates the pipeline: Research Request -> Source Collection -> Validation ->
Knowledge Extraction -> Brain 2/3 Update -> Recommendations -> Response.
"""
from dataclasses import dataclass, field, asdict
from typing import Any
import datetime, hashlib, json, time


@dataclass
class ResearchRequest:
    """A structured research request from Hermes or NEXARA."""
    query: str
    requester: str = "hermes"
    domain: str = "general"
    priority: int = 5  # 1-10
    deadline: str | None = None
    request_id: str = field(default_factory=lambda: hashlib.md5(f"{time.time()}".encode()).hexdigest()[:12])
    created_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())


@dataclass
class Source:
    """A collected source with provenance tracking."""
    url: str
    title: str
    retrieved_at: str
    content_type: str  # 'html', 'pdf', 'text', 'api'
    size_chars: int
    sha256: str
    is_live_data: bool = True
    provenance_warning: str | None = None


@dataclass
class ExtractedKnowledge:
    """Knowledge extracted from a source."""
    source_id: str
    summary: str
    key_findings: list[str]
    insights: list[str]
    recommendations: list[str]
    confidence: float  # 0.0-1.0
    related_concepts: list[str]


@dataclass
class ResearchArtifact:
    """A completed research artifact — the primary Brain 2 unit."""
    artifact_id: str
    request_id: str
    research_query: str
    summary: str
    key_findings: list[str]
    insights: list[str]
    recommendations: list[str]
    sources: list[str]  # source IDs
    world_brain_updates: dict[str, Any]
    future_training_assets: list[dict[str, Any]]
    confidence: float
    is_live: bool
    created_at: str
    version: int = 1


class ResearchCore:
    """Core research pipeline orchestrator."""

    def __init__(self, kb_path: str):
        self.kb_path = kb_path
        self.sources: dict[str, Source] = {}
        self.knowledge: dict[str, ExtractedKnowledge] = {}
        self.artifacts: dict[str, ResearchArtifact] = {}

    def receive_request(self, request: ResearchRequest) -> str:
        """Entry point: Hermes/NEXARA submits a research request."""
        print(f"[RESEARCH_CORE] Request received: '{request.query}' (domain={request.domain})")
        return request.request_id

    def archive_source(self, source: Source, content: str) -> str:
        """Archive a source in raw/articles/ and return its source_id."""
        import re
        source_id = hashlib.md5(source.url.encode()).hexdigest()[:12]
        # Sanitize title for filesystem: lowercase, hyphens, strip non-alnum
        safe_title = re.sub(r'[^a-z0-9]+', '-', source.title.lower()).strip('-')[:50]
        safe_name = source_id + "_" + safe_title
        path = f"{self.kb_path}/raw/articles/{safe_name}.md"
        with open(path, "w") as f:
            f.write(f"---\nsource_url: {source.url}\ningested: {datetime.date.today().isoformat()}\nsha256: {source.sha256}\n---\n\n{content}")
        self.sources[source_id] = source
        return source_id

    def store_artifact(self, artifact: ResearchArtifact) -> str:
        """Store a research artifact and update Brain 2/3 indexes."""
        path = f"{self.kb_path}/queries/research-{artifact.artifact_id}.md"
        with open(path, "w") as f:
            f.write(json.dumps(asdict(artifact), indent=2))
        self.artifacts[artifact.artifact_id] = artifact
        return artifact.artifact_id


if __name__ == "__main__":
    core = ResearchCore("/home/ajeetesh/nexara-p8/research-fabric/knowledge-base")
    req = ResearchRequest(query="MCP protocol vs A2A protocol", domain="protocol")
    rid = core.receive_request(req)
    print(f"  request_id={rid}")
    print("  pipeline ready.")
