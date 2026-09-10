#!/usr/bin/env python3
"""
BRAIN_INTEGRATION — Updates Brain 2 (knowledge) and Brain 3 (world model)
from research artifacts. Also files results into the llm-wiki knowledge base.
"""
import os, datetime, hashlib
import re

WIKI_BASE = "/home/ajeetesh/nexara-p8/research-fabric/knowledge-base"
WIKI_LLM = os.path.join(WIKI_BASE, "llm-wiki")


class WikiIntegrator:
    """Integrates research artifacts into the Karpathy-style wiki."""

    def __init__(self, kb_path: str):
        self.kb_path = kb_path
        self.wiki_path = os.path.join(kb_path, "llm-wiki")
        self._ensure_wiki_structure()
        self._load_index()

    def _ensure_wiki_structure(self):
        dirs = ["raw/articles", "raw/papers", "raw/transcripts", "raw/assets",
                "entities", "concepts", "comparisons", "queries", "_archive"]
        for d in dirs:
            os.makedirs(os.path.join(self.wiki_path, d), exist_ok=True)
        # Create SCHEMA.md if missing
        schema_path = os.path.join(self.wiki_path, "SCHEMA.md")
        if not os.path.exists(schema_path):
            with open(schema_path, "w") as f:
                f.write(self._default_schema())
        for init_file in ["index.md", "log.md"]:
            path = os.path.join(self.wiki_path, init_file)
            if not os.path.exists(path):
                with open(path, "w") as f:
                    f.write(self._default_index() if "index" in init_file else self._default_log())

    def _default_schema(self) -> str:
        return "# Wiki Schema\n\n## Domain\nNEXARA Research Fabric knowledge base.\n\n## Tag Taxonomy\n- Protocol: protocol, api, specification, interoperability\n- System: agent, orchestration, framework, runtime\n- Technology: model, architecture, benchmark, optimization\n- Research: paper, experiment, finding\n- Market: company, product, trend, opportunity\n- Community: person, lab, open-source, contributor\n- Meta: comparison, timeline, controversy, prediction\n"

    def _default_index(self) -> str:
        return "# Wiki Index\n\n> Last updated: 2026-09-10 | Total pages: 0\n\n## Entities\n## Concepts\n## Comparisons\n## Queries\n"

    def _default_log(self) -> str:
        return "# Wiki Log\n\n## [2026-09-10] create | Wiki initialized\n- Initialized llm-wiki knowledge base\n"

    def _load_index(self):
        self.index_path = os.path.join(self.wiki_path, "index.md")
        with open(self.index_path, "r") as f:
            self.index_content = f.read()

    def _slugify(self, text: str) -> str:
        slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:60]
        return slug or "untitled"

    def create_wiki_page(self, title: str, page_type: str, content: str,
                         tags: list[str], sources: list[str],
                         confidence: str = "medium") -> str:
        """Create or update a wiki page in the appropriate section."""
        slug = self._slugify(title)
        today = datetime.date.today().isoformat()

        # Determine section directory
        dir_map = {"entity": "entities", "concept": "concepts",
                   "comparison": "comparisons", "query": "queries",
                   "summary": "queries"}
        section_dir = dir_map.get(page_type, "concepts")
        page_path = os.path.join(self.wiki_path, section_dir, f"{slug}.md")

        # Build frontmatter
        frontmatter = f"""---
title: {title}
created: {today}
updated: {today}
type: {page_type}
tags: [{', '.join(tags)}]
sources: [{', '.join(sources)}]
confidence: {confidence}
---

{content}
"""
        with open(page_path, "w") as f:
            f.write(frontmatter)

        # Update index
        self._add_to_index(title, slug, section_dir, page_type)

        # Log it
        self._append_log("update", f"{page_type} '{title}'", page_path)

        return page_path

    def _add_to_index(self, title: str, slug: str, section: str, page_type: str):
        """Add a new page entry to the wiki index under the correct section."""
        section_headers = {"entities": "## Entities", "concepts": "## Concepts",
                          "comparisons": "## Comparisons", "queries": "## Queries"}
        target_header = section_headers.get(section, "## Concepts")
        new_entry = f"- `[[{slug}]]` — {title}"

        lines = self.index_content.split('\n')
        insert_idx = None
        for i, line in enumerate(lines):
            if line.strip() == target_header:
                insert_idx = i + 1
                # skip existing entries in this section (they're already there)
                break

        if insert_idx is not None:
            # Insert after the header, before next section or end
            while insert_idx < len(lines) and not lines[insert_idx].startswith("## "):
                insert_idx += 1
            lines.insert(insert_idx, new_entry)
            self.index_content = '\n'.join(lines)
            self._rewrite_index()
        else:
            # Header not found, append at end
            lines.append(target_header)
            lines.append(new_entry)
            self.index_content = '\n'.join(lines)
            self._rewrite_index()

    def _rewrite_index(self):
        # Count pages
        count = 0
        for d in ["entities", "concepts", "comparisons", "queries"]:
            dir_path = os.path.join(self.wiki_path, d)
            if os.path.exists(dir_path):
                count += len([f for f in os.listdir(dir_path) if f.endswith('.md')])
        today = datetime.date.today().isoformat()
        header = f"# Wiki Index\n\n> Last updated: {today} | Total pages: {count}\n\n"
        self.index_content = header + self.index_content.split('> Last updated')[1].split('\n\n', 1)[1]
        with open(self.index_path, "w") as f:
            f.write(self.index_content)

    def _append_log(self, action: str, subject: str, detail: str = ""):
        log_path = os.path.join(self.wiki_path, "log.md")
        today = datetime.date.today().isoformat()
        entry = f"## [{today}] {action} | {subject}"
        if detail:
            entry += f"\n- {detail}\n"
        else:
            entry += "\n"
        with open(log_path, "a") as f:
            f.write("\n" + entry)


class Brain2Updater:
    """Updates Brain 2 (Memory Brain — research knowledge)."""

    def __init__(self, kb_path: str):
        self.kb_path = kb_path
        self.knowledge_store = os.path.join(kb_path, "brain2_knowledge.json")
        self.knowledge: list[dict] = self._load()

    def _load(self) -> list[dict]:
        import json, os
        path = self.knowledge_store
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return []

    def _save(self):
        import json
        os.makedirs(os.path.dirname(self.knowledge_store), exist_ok=True)
        with open(self.knowledge_store, "w") as f:
            json.dump(self.knowledge, f, indent=2)

    def update(self, artifact) -> str:
        """Ingest a research artifact into Brain 2 knowledge store."""
        entry = {
            "type": "research_artifact",
            "query": artifact.research_query,
            "summary": artifact.summary,
            "key_findings": artifact.key_findings,
            "insights": artifact.insights,
            "recommendations": artifact.recommendations,
            "sources": artifact.sources,
            "confidence": artifact.confidence,
            "artifact_id": artifact.artifact_id,
            "created_at": artifact.created_at,
        }
        self.knowledge.append(entry)
        self._save()
        return artifact.artifact_id


class Brain3Updater:
    """Updates Brain 3 (World Intelligence — digital twin)."""

    def __init__(self, kb_path: str):
        self.kb_path = kb_path
        self.world_model_path = os.path.join(kb_path, "brain3_world_model.json")
        self.world_model: dict = self._load()

    def _load(self) -> dict:
        import json, os
        if os.path.exists(self.world_model_path):
            with open(self.world_model_path) as f:
                return json.load(f)
        return {"entities": {}, "signals": [], "updated_at": None}

    def _save(self):
        import json, datetime
        self.world_model["updated_at"] = datetime.datetime.now().isoformat()
        os.makedirs(os.path.dirname(self.world_model_path), exist_ok=True)
        with open(self.world_model_path, "w") as f:
            json.dump(self.world_model, f, indent=2)

    def update(self, artifact) -> int:
        """Ingest research artifact into Brain 3 world model."""
        count = 0
        world_updates = artifact.world_brain_updates

        # Record signals
        if "signals" in world_updates:
            for signal in world_updates["signals"]:
                self.world_model["signals"].append(signal)
                count += 1

        # Record entities
        if "entities_created" in world_updates:
            for ent in world_updates["entities_created"]:
                if ent not in self.world_model["entities"]:
                    self.world_model["entities"][ent] = {
                        "first_seen": datetime.datetime.now().isoformat(),
                        "last_seen": datetime.datetime.now().isoformat(),
                        "source": "research_fabric",
                        "query": artifact.research_query,
                    }
                    count += 1
                else:
                    self.world_model["entities"][ent]["last_seen"] = datetime.datetime.now().isoformat()

        self._save()
        return count


class BrainIntegration:
    """Orchestrates Brain 2/3 updates + wiki integration from research artifacts."""

    def __init__(self, kb_path: str):
        self.kb_path = kb_path
        self.wiki = WikiIntegrator(kb_path)
        self.brain2 = Brain2Updater(kb_path)
        self.brain3 = Brain3Updater(kb_path)

    def process_artifact(self, artifact) -> dict:
        """Process a completed research artifact into all Brain systems."""
        results = {"brain2": None, "brain3": 0, "wiki_pages": []}

        # Update Brain 2
        results["brain2"] = self.brain2.update(artifact)

        # Update Brain 3
        results["brain3"] = self.brain3.update(artifact)

        # Create wiki page for the research result
        wiki_path = self.wiki.create_wiki_page(
            title=f"Research: {artifact.research_query}",
            page_type="query",
            content=f"""## Research Summary

{artifact.summary}

## Key Findings

{chr(10).join(f'- {f}' for f in artifact.key_findings)}

## Insights

{chr(10).join(f'- {i}' for i in artifact.insights)}

## Recommendations

{chr(10).join(f'- {r}' for r in artifact.recommendations)}

## Confidence

{artifact.confidence:.2f} ({'live data' if artifact.is_live else 'seed data'})
""",
            tags=["research", "protocol", "comparison"],
            sources=artifact.sources,
            confidence="medium" if artifact.confidence > 0.5 else "low"
        )
        results["wiki_pages"].append(wiki_path)

        return results
