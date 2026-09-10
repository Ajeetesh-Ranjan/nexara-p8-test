#!/usr/bin/env python3
"""
RESEARCH_PIPELINE — Pipeline orchestration layer.
Bridges collectors -> validators -> knowledge extractor -> Brain 2/3 update.
"""
import hashlib, datetime
from dataclasses import asdict
from typing import Optional

from core.research_core import ResearchCore, ResearchRequest, ResearchArtifact, ExtractedKnowledge
from collectors.collectors import CollectorResult


class SourceValidator:
    """Validates collected sources: provenance, freshness, credibility."""

    def validate(self, item) -> tuple[bool, str]:
        if item.provenance_warning:
            return False, f"Seed/unverified source: {item.provenance_warning}"
        if not item.is_live_data:
            return False, "Offline source"
        if len(item.content) < 20:
            return False, "Content too short (<20 chars)"
        return True, "Valid"


class KnowledgeExtractor:
    """Extracts structured knowledge from validated sources."""

    def extract(self, source_item, kb_path: str) -> ExtractedKnowledge:
        title = source_item.title
        content = source_item.content
        findings = [line.strip() for line in content.split('\n') if line.strip()][:5]
        insights = [f"Key concept: {title}" if content else "No content extracted"]
        confidence = 0.5 if source_item.is_live_data else 0.2

        return ExtractedKnowledge(
            source_id=hashlib.md5(source_item.url.encode()).hexdigest()[:12],
            summary=f"Source: {title}. {content[:200]}..." if len(content) > 200 else f"Source: {title}. {content}",
            key_findings=findings,
            insights=insights,
            recommendations=[f"Investigate {title} further for protocol implications"],
            confidence=confidence,
            related_concepts=[]
        )


class Pipeline:
    """Orchestrates the full research pipeline."""

    def __init__(self, core: ResearchCore):
        self.core = core
        self.validator = SourceValidator()
        self.extractor = KnowledgeExtractor()
        self.kb_path = core.kb_path

    def execute(self, request: ResearchRequest) -> ResearchArtifact:
        print(f"[PIPELINE] Executing: '{request.query}' (domain={request.domain})")

        # 1. Collection
        from collectors.collectors import CollectorRegistry
        registry = CollectorRegistry()
        result: CollectorResult = registry.collect(request.domain, request.query)
        print(f"  [1/4] Collection: {len(result.items)} sources collected, live={result.is_live}")

        # 2. Validation + 3. Extraction
        validated_count = 0
        extracted: list[ExtractedKnowledge] = []
        for item in result.items:
            is_valid, reason = self.validator.validate(item)
            if is_valid:
                validated_count += 1
                source_id = self.core.archive_source(
                    type('S', (), {
                        'url': item.url, 'title': item.title, 'content_type': item.content_type,
                        'size_chars': len(item.content), 'sha256': item.sha256,
                        'is_live_data': item.is_live_data, 'provenance_warning': item.provenance_warning
                    })(), item.content
                )
                knowledge = self.extractor.extract(item, self.kb_path)
                extracted.append(knowledge)
            else:
                print(f"  [SKIP] {item.title}: {reason}")

        print(f"  [2/4] Validation: {validated_count}/{len(result.items)} sources valid")

        # 3. Knowledge synthesis
        all_findings = []
        all_insights = []
        for k in extracted:
            all_findings.extend(k.key_findings)
            all_insights.extend(k.insights)
        avg_confidence = sum(k.confidence for k in extracted) / max(len(extracted), 1)
        print(f"  [3/4] Extraction: {len(extracted)} knowledge units, avg confidence={avg_confidence:.2f}")

        # 4. Artifact creation
        artifact = ResearchArtifact(
            artifact_id=hashlib.md5(f"{request.request_id}{datetime.datetime.now().isoformat()}".encode()).hexdigest()[:12],
            request_id=request.request_id,
            research_query=request.query,
            summary=f"Research on '{request.query}' across {request.domain} domain ({validated_count} valid sources).",
            key_findings=all_findings[:10] if all_findings else ["No findings from available sources"],
            insights=all_insights[:5] if all_insights else ["Insufficient data for insights"],
            recommendations=[f"Expand sourcing for '{request.query}' in domain '{request.domain}'"],
            sources=[e.source_id for e in extracted],
            world_brain_updates={
                "entities_created": [e.source_id for e in extracted],
                "domain": request.domain,
                "research_query": request.query
            },
            future_training_assets=[{
                "type": "research_artifact",
                "artifact_id": hashlib.md5(f"{request.request_id}{datetime.datetime.now().isoformat()}".encode()).hexdigest()[:12],
                "structured": True
            }],
            confidence=avg_confidence,
            is_live=request.is_live if hasattr(request, 'is_live') else True,
            created_at=datetime.datetime.now().isoformat()
        )
        self.core.store_artifact(artifact)
        print(f"  [4/4] Artifact created: research-{artifact.artifact_id}")
        return artifact


if __name__ == "__main__":
    core = ResearchCore("/home/ajeetesh/nexara-p8/research-fabric/knowledge-base")
    pipeline = Pipeline(core)
    request = ResearchRequest(query="MCP vs A2A protocol comparison", domain="technology")
    rid = core.receive_request(request)
    artifact = pipeline.execute(request)
    print(f"  Done. confidence={artifact.confidence:.2f}, findings={len(artifact.key_findings)}")
