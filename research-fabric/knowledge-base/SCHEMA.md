# Research Fabric Knowledge Base Schema

## Domain
NEXARA Research Fabric — AI/ML systems, distributed intelligence, agent communication protocols, orchestration frameworks, and associated technologies. Focused on understanding (not just collecting): what happened, why it matters, who should care.

## Conventions
- File names: lowercase, hyphens, no spaces (e.g., `mcp-protocol.md`)
- Every wiki page starts with YAML frontmatter (see below)
- Use `[[wikilinks]]` to link between pages (minimum 2 outbound links per page)
- When updating a page, always bump the `updated` date
- Every new page must be added to `index.md` under the correct section
- Every action must be appended to `log.md`
- **Provenance markers:** On pages that synthesize 3+ sources, append `^[raw/articles/source-file.md]` at the end of paragraphs whose claims come from a specific source.

## Frontmatter
```yaml
---
title: Page Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | comparison | query | summary
tags: [from taxonomy below]
sources: [raw/articles/source-name.md]
confidence: high | medium | low
contested: true
contradictions: [other-page-slug]
---
```

## Tag Taxonomy
- **Protocol:** protocol, api, specification, interoperability
- **System:** agent, orchestration, framework, runtime, protocol-suite
- **Technology:** model, architecture, benchmark, optimization, inference, data-pipeline, vector-db, memory
- **Research:** paper, experiment, finding, hypothesis, result
- **Market:** company, product, ecosystem, adoption, trend, opportunity
- **Community:** person, lab, open-source, contributor, maintainer
- **Meta:** comparison, timeline, controversy, prediction, evaluation

## Page Thresholds
- **Create a page** when an entity/concept appears in 2+ sources OR is central to one source
- **Add to existing page** when a source mentions something already covered
- **DON'T create a page** for passing mentions, minor details, or things outside the domain
- **Split a page** when it exceeds ~200 lines
- **Archive a page** when content is fully superseded — move to `_archive/`, remove from index

## Update Policy
1. Check dates — newer generally supersedes older
2. If genuinely contradictory, note both with dates and sources
3. Mark contradiction in frontmatter: `contradictions: [page-name]`
4. Flag for user review in lint report

## Confidence Rules
- `confidence: high` — well-supported across 2+ independent sources
- `confidence: medium` — supported by 1 source or conflicting sources reconciled
- `confidence: low` — single source, fast-moving topic, or opinion/unverified