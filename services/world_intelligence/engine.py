#!/usr/bin/env python3
"""
INTELLIGENCE ENGINE — converts collected items into signals, then opportunities.

The distinction that matters:
  observation  = "N sources mentioned X"                  (fact, verifiable)
  signal       = "X is being discussed unusually much"    (measured vs baseline)
  opportunity  = "X may be worth acting on because ..."   (inference, hypothesis)

Every opportunity is tagged `is_hypothesis: True` and capped at confidence 0.85.
Nothing here can produce certainty, and the code refuses to pretend otherwise.
A signal with one source is reported as a single-source signal, never inflated.
"""
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field, asdict, fields
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ------------------------------------------------------------------ signal --

RISK_TERMS = re.compile(
    r"\b(vulnerabilit\w+|CVE-\d{4}-\d+|exploit\w*|breach\w*|outage|deprecat\w+|"
    r"end.of.life|EOL|shut(?:ting)?\s+down|sunset\w*|lawsuit|ban(?:ned|ning)?|"
    r"backdoor|ransomware|zero.day|licen[cs]e\s+chang\w+|rug\s?pull)\b", re.I)

# How close (characters) a risk term must be to an entity mention to count.
RISK_PROXIMITY = 120

MOMENTUM_TERMS = re.compile(
    r"\b(launch\w*|release\w*|announc\w+|open.sourc\w+|raise[ds]?|funding|"
    r"acquir\w+|partnership|benchmark\w*|state.of.the.art|SOTA|breakthrough|"
    r"GA\b|general\s+availability|v\d+\.\d+)\b", re.I)

MAX_EVIDENCE = 6

# Telegram posts are reachable under several official hostnames. The shortener
# forms are mirrors of the same post, so they must never inflate publisher counts.
TELEGRAM_HOSTS = {"t.me", "telegram.me", "telegram.dog", "telegram.org"}
TELEGRAM_SHORTENERS = {"t.me", "telegram.me", "telegram.dog"}


def _canonical_url(value: str) -> str:
    """Remove transport/tracking variants, retaining content query parameters."""
    value = str(value or "").strip()
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return ""
        host = parts.hostname.lower().removeprefix("www.")
        # Unify Telegram domains: t.me -> telegram.org
        if host in {"t.me", "telegram.me", "telegram.dog"}:
            host = "telegram.org"
        if parts.port and parts.port not in {80, 443}:
            host += f":{parts.port}"
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in
                 {"fbclid", "gclid", "mc_cid", "mc_eid"}]
        return urlunsplit(("https", host, parts.path.rstrip("/") or "/",
                          urlencode(sorted(query)), ""))
    except ValueError:
        return ""


def _telegram_post(url: str) -> tuple[str, str] | None:
    """Identity of a Telegram post: (channel, post_id), mirror hostnames folded."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    if host not in TELEGRAM_HOSTS:
        return None
    segments = [seg for seg in parts.path.split("/") if seg]
    if segments and segments[0] == "s":        # t.me/s/<channel>/<id> web preview
        segments = segments[1:]
    if len(segments) < 2 or not segments[1].isdigit():
        return None
    return segments[0].lower(), segments[1]


def _normalize_channel(value: str) -> str:
    channel = str(value or "").strip().lower()
    if not channel:
        return ""
    if channel.startswith("http"):
        identity = _telegram_post(channel)
        if identity:
            return identity[0]
        return urlsplit(channel).path.strip("/").split("/")[0].lstrip("@").lower()
    return channel.lstrip("@")


def _citations(items: list[dict]) -> list[dict]:
    """Canonical URLs unify collector duplicates; never count collector buckets."""
    groups: dict[object, list[dict]] = {}
    for item in sorted(items, key=lambda i: json.dumps(i, sort_keys=True, default=str)):
        url = _canonical_url(item.get("url", ""))
        if not url:
            continue
        ev = {"title": str(item.get("title", ""))[:160], "url": url,
              "excerpt": str(item.get("excerpt", item.get("text", "")))[:400],
              "source": item.get("source", "unknown"),
              "publisher": urlsplit(url).hostname,
              "is_live_data": item.get("is_live_data"),
              "collected_at": item.get("collected_at", item.get("fetched_at")),
              "published_at": item.get("published_at"),
              "category": item.get("category", "unknown")}
        for name in ("topics",):
            if name in item:
                ev[name] = item[name]
        post = _telegram_post(url)
        channel = _normalize_channel(item.get("channel", "")) or (post[0] if post else "")
        if channel:
            ev["channel"] = channel
        # A Telegram post reached through a mirror hostname is one citation,
        # so identity is the post itself; everything else keys on canonical URL.
        groups.setdefault((channel, post[1]) if post else url, []).append(ev)
    return sorted((_merge_citations(group) for group in groups.values()),
                  key=lambda e: (_publisher(e), e["url"]))


def _merge_citations(group: list[dict]) -> dict:
    """One citation per real publication; mirrors never upgrade its claims."""
    def rank(ev):
        host = (urlsplit(ev["url"]).hostname or "").lower()
        return (host in TELEGRAM_SHORTENERS, ev["url"])

    chosen = dict(min(group, key=rank))
    for ev in group:                       # keep the channel any mirror knew about
        if ev.get("channel") and not chosen.get("channel"):
            chosen["channel"] = ev["channel"]
    chosen["is_live_data"] = (True if all(ev.get("is_live_data") is True for ev in group)
                              else chosen.get("is_live_data"))
    if len(group) > 1:
        chosen["mirrors"] = sorted({ev["url"] for ev in group} - {chosen["url"]})
    return chosen


def _publisher(ev: dict) -> str:
    channel = _normalize_channel(ev.get("channel", ""))
    if channel:
        return f"telegram:{channel}"
    publisher = ev.get("publisher")
    if publisher:
        return str(publisher).lower().removeprefix("www.")
    # Evidence assembled elsewhere may omit the derived field; the URL is the
    # source of truth, and an unattributable citation must not read as a publisher.
    host = (urlsplit(str(ev.get("url", ""))).hostname or "").lower().removeprefix("www.")
    return host or "unattributed"


def _representative_evidence(citations: list[dict]) -> list[dict]:
    """Bound storage while giving each retained publisher a representative."""
    representatives, extra, publishers = [], [], set()
    for ev in citations:
        name = _publisher(ev)
        if name in publishers:
            extra.append(ev)
        else:
            representatives.append(ev)
            publishers.add(name)
    return (representatives + extra)[:MAX_EVIDENCE]


def _stable_id(prefix: str, *identity) -> str:
    payload = json.dumps([prefix, 1, *identity], ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:24]}"


@dataclass
class Signal:
    entity: str
    entity_type: str
    kind: str                      # emergence | momentum | risk | convergence
    strength: float                # 0..1, measured
    mentions: int
    distinct_sources: int
    source_names: list[str]
    categories: list[str]
    evidence: list[dict]           # [{title, url, source}]
    first_seen: float
    last_seen: float
    rationale: str
    is_single_source: bool

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = _stable_id("sig", self.entity, self.kind)
        return d


@dataclass
class Opportunity:
    title: str
    thesis: str
    entities: list[str]
    kind: str                      # learn | build | automate | market | research
    confidence: float              # capped at 0.85 — this is inference
    is_hypothesis: bool
    supporting_signals: list[str]
    distinct_sources: int
    evidence: list[dict]
    questions: dict[str, str] | list[str]  # keyed Phase-11 answers; legacy constructor accepts lists
    risks: list[str]
    next_step: str
    # Phase 11: seven scoring dimensions (0..100, higher = better except effort/risk)
    impact: int = 0
    confidence_score: int = 0      # 0..100, distinct from confidence 0..0.85
    effort: int = 0                # 0..100, higher = more effort (worse)
    risk: int = 0                  # 0..100, higher = more risk (worse)
    time_to_value: int = 0         # 0..100, higher = faster value (better)
    strategic_alignment: int = 0   # 0..100, higher = more aligned with NEXARA
    overall: int = 0               # 0..100, composite
    # metadata
    decision: str = "watch"        # shortlist | watch | reject
    rationale: str = ""
    assumptions: list[str] = field(default_factory=list)
    risk_band: str = "Medium"      # Low | Medium | High
    monetary_estimate: dict | None = None
    created_at: float = field(default_factory=time.time)
    scores: dict = field(default_factory=dict)
    score_components: dict = field(default_factory=dict)
    is_live_data: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = _stable_id("opp", sorted(set(self.entities)), self.kind,
                             sorted(set(self.supporting_signals)))
        return d


class SignalEngine:
    """Detects signals from extracted entity observations against a baseline."""

    def __init__(self, baseline: dict[str, dict] | None = None):
        # baseline: entity -> {"mean_mentions": float, "windows": int}
        self.baseline = baseline or {}

    def detect(self, observations: list[dict]) -> list[Signal]:
        """
        observations: [{entity, type, method, item: {...}}]
        Groups by entity, measures spread and momentum, emits signals.
        """
        by_entity: dict[str, list[dict]] = {}
        for o in observations:
            by_entity.setdefault(o["entity"], []).append(o)

        signals: list[Signal] = []
        for entity, obs in by_entity.items():
            items = _citations([o["item"] for o in obs])
            if not items:
                continue
            evidence = _representative_evidence(items)
            sources = sorted({_publisher(i) for i in evidence})
            cats = sorted({i["category"] for i in items})
            mentions = len(items)
            etype = obs[0]["type"]

            # extraction quality gate: heuristic-only entities need corroboration
            methods = {o["method"] for o in obs}
            if methods == {"heuristic"} and len(sources) < 2:
                continue

            text_blob = " ".join(f"{i['title']} {i['excerpt']}" for i in items)
            momentum_hits = MOMENTUM_TERMS.findall(text_blob)

            # Risk must be attributable to THIS entity, not merely present in the
            # same document. Require the risk term within PROXIMITY chars of an
            # entity mention; otherwise "Amazon" inherits an unrelated "shut down".
            risk_hits = []
            risk_items = set()
            ent_pat = re.compile(re.escape(entity), re.I)
            for i in items:
                blob = f"{i['title']} {i['excerpt']}"
                for em in ent_pat.finditer(blob):
                    lo = max(0, em.start() - RISK_PROXIMITY)
                    hi = min(len(blob), em.end() + RISK_PROXIMITY)
                    near = RISK_TERMS.findall(blob[lo:hi])
                    if near:
                        risk_hits.extend(near)
                        risk_items.add(i["url"])
                        break

            base = self.baseline.get(entity, {})
            mean = float(base.get("mean_mentions", 0.0))
            windows = int(base.get("windows", 0))

            times = [i.get("published_at") or i.get("collected_at") or 0.0 for i in items]

            def mk(kind, strength, rationale):
                return Signal(
                    entity=entity, entity_type=etype, kind=kind,
                    strength=round(min(strength, 1.0), 3),
                    mentions=mentions, distinct_sources=len(sources),
                    source_names=sources, categories=cats, evidence=evidence,
                    first_seen=min(times), last_seen=max(times),
                    rationale=rationale, is_single_source=len(sources) < 2,
                )

            # 1. risk — negative language located NEAR the entity itself.
            #    A single loose term in one item is noise, not a threat.
            distinct_risk = sorted({h.lower() for h in risk_hits})
            if risk_hits and (len(risk_items) >= 2 or len(distinct_risk) >= 2):
                s = min(0.30 + 0.12 * len(distinct_risk) + 0.08 * len(risk_items), 1.0)
                signals.append(mk("risk", s,
                    f"{len(distinct_risk)} risk term(s) near '{entity}' in "
                    f"{len(risk_items)} item(s): {', '.join(distinct_risk[:4])}"))

            # 2. emergence — never seen before in baseline, now multi-source
            if windows == 0 and len(sources) >= 2:
                signals.append(mk("emergence", min(0.4 + 0.15 * len(sources), 1.0),
                    f"not present in prior windows; appeared in {mentions} items "
                    f"across {len(sources)} sources"))

            # 3. momentum — measurable rise over baseline
            elif windows > 0 and mean > 0 and mentions > mean:
                ratio = mentions / max(mean, 0.5)
                if ratio >= 1.5:
                    signals.append(mk("momentum", min(0.3 + 0.2 * math.log(ratio + 1)
                                                      + 0.05 * len(sources), 1.0),
                        f"{mentions} mentions vs baseline mean {mean:.1f} "
                        f"({ratio:.1f}x) over {windows} prior window(s)"))

            # 4. launch/announcement momentum regardless of baseline
            elif momentum_hits and len(sources) >= 2:
                signals.append(mk("momentum", min(0.3 + 0.06 * len(momentum_hits)
                                                  + 0.08 * len(sources), 1.0),
                    f"{len(momentum_hits)} momentum term(s) across {len(sources)} sources"))

        signals.sort(key=lambda s: (s.strength, s.distinct_sources), reverse=True)
        return signals

    def detect_convergence(self, signals: list[Signal], min_shared: int = 2) -> list[Signal]:
        """Entities co-occurring in the same items form a convergence signal."""
        out: list[Signal] = []
        by_url: dict[str, set[str]] = {}
        for s in signals:
            for ev in s.evidence:
                by_url.setdefault(ev["url"], set()).add(s.entity)

        pair_count: dict[tuple[str, str], int] = {}
        pair_urls: dict[tuple[str, str], list[str]] = {}
        for url, ents in by_url.items():
            ents_l = sorted(ents)
            for i in range(len(ents_l)):
                for j in range(i + 1, len(ents_l)):
                    k = (ents_l[i], ents_l[j])
                    pair_count[k] = pair_count.get(k, 0) + 1
                    pair_urls.setdefault(k, []).append(url)

        idx = {s.entity: s for s in signals}
        for (a, b), n in sorted(pair_count.items(), key=lambda kv: -kv[1]):
            if n < min_shared:
                continue
            sa, sb = idx.get(a), idx.get(b)
            if not sa or not sb:
                continue
            # Find shared URLs between the two signals
            urls_a = {ev["url"] for ev in sa.evidence}
            urls_b = {ev["url"] for ev in sb.evidence}
            shared_urls = urls_a & urls_b
            shared_evidence = [ev for ev in (sa.evidence + sb.evidence) if ev["url"] in shared_urls]
            # Deduplicate by URL, keeping first
            seen_shared = set()
            unique_shared = []
            for ev in shared_evidence:
                if ev["url"] not in seen_shared:
                    seen_shared.add(ev["url"])
                    unique_shared.append(ev)
            srcs = (sorted({_publisher(ev) for ev in unique_shared}) if unique_shared
                    else sorted(set(sa.source_names) | set(sb.source_names)))
            out.append(Signal(
                entity=f"{a} + {b}", entity_type="convergence", kind="convergence",
                strength=round(min(0.3 + 0.15 * n + 0.05 * len(srcs), 0.95), 3),
                mentions=sa.mentions + sb.mentions, distinct_sources=len(srcs),
                source_names=srcs, categories=sorted(set(sa.categories) | set(sb.categories)),
                evidence=unique_shared[:6],
                first_seen=min(sa.first_seen, sb.first_seen),
                last_seen=max(sa.last_seen, sb.last_seen),
                rationale=f"co-occurred in {n} item(s) across {len(srcs)} source(s)",
                is_single_source=len(srcs) < 2,
            ))
        return out[:12]


class OpportunityEngine:
    """Evaluate observations as unvalidated hypotheses, never promises of value."""

    QUESTION_KEYS = ("build", "learn", "automate", "market", "research", "revenue", "improve_nexara")

    WEIGHTS = {"impact": .20, "confidence": .20, "effort": .15, "risk": .15,
               "time_to_value": .10, "strategic_alignment": .20}
    SHORTLIST = {"overall": 65, "confidence": 60, "risk_max": 65}
    TECHNICAL_TYPES = {"protocol", "framework", "infrastructure", "open-source-project", "convergence"}
    FEATURE_PATTERNS = {
        "surface": re.compile(r"\b(?:api|sdk|cli|plugin|adapter|integration)\b", re.I),
        "automation": re.compile(r"\b(?:automat\w*|workflow\w*|webhook\w*|scheduler|bot)\b", re.I),
        "docs": re.compile(r"\b(?:documentation|docs|tutorial\w*|quickstart|example\w*|guide)\b", re.I),
        "research": re.compile(r"\b(?:research|paper|benchmark\w*|dataset\w*|evaluation|study)\b", re.I),
        "market": re.compile(r"\b(?:adoption|customer\w*|users|demand|distribution)\b", re.I),
        "revenue": re.compile(r"\b(?:pricing|paid|revenue|subscription|commercial)\b", re.I),
        "alignment": re.compile(r"\b(?:nexara|memory|research|retrieval|knowledge|agents?|automat\w*|workflow\w*|intelligence)\b", re.I),
        "nexara": re.compile(r"\bnexara\b", re.I),
        "complexity": re.compile(r"\b(?:migration|breaking|rewrite|training|new infrastructure)\b", re.I),
        "risk": RISK_TERMS,
    }

    # Each user question is answered from ONE named evidence feature, so the
    # answer can always be traced back to the words in the cited text.
    QUESTION_SPEC = {
        "build": ("surface", "a buildable surface (API/SDK/CLI/plugin)"),
        "learn": ("docs", "learning material (documentation/tutorial/example)"),
        "automate": ("automation", "an automation hook (workflow/webhook/bot)"),
        "market": ("market", "market demand language (adoption/customers/demand)"),
        "research": ("research", "research substance (paper/benchmark/dataset)"),
        "revenue": ("revenue", "revenue language (pricing/paid/subscription)"),
        "improve_nexara": ("alignment", "NEXARA fit (memory/research/agents/automation)"),
    }

    def _features(self, evidence: list[dict]) -> dict:
        features = {}
        for name, pattern in self.FEATURE_PATTERNS.items():
            hits = []
            for ev in evidence:
                terms = sorted({m.group().lower() for m in pattern.finditer(f"{ev['title']} {ev['excerpt']}")})
                if terms:
                    hits.append({"url": ev["url"], "terms": terms})
            features[name] = hits
        return features

    def _score(self, s: Signal, evidence: list[dict], publishers: list[str], features: dict):
        """Transparent prioritization heuristics, not measured ROI or probabilities."""
        n = len(publishers)
        strength = max(0.0, min(float(s.strength), .85))
        f = {key: bool(value) for key, value in features.items()}
        inputs = {"publishers": publishers, "citation_count": len(evidence),
                  "strength_capped": strength, "entity_type": s.entity_type, "features": features}
        scores, components = {}, {}

        def dimension(key, base, adjustments, rationale, cap=100):
            raw = base + sum(adjustments.values())
            value = int(round(max(0, min(raw, cap))))
            scores[key] = value
            components[key] = {"value": value, "base": base, "adjustments": adjustments,
                               "inputs": inputs, "rationale": rationale, "cap": cap}
            return max(0, min(raw, cap))

        if not evidence:
            for key, value in {"impact": 0, "confidence": 0, "effort": 70, "risk": 80,
                               "time_to_value": 0, "strategic_alignment": 0}.items():
                dimension(key, value, {}, "No usable evidence; effort/risk remain unknown, not zero.")
            confidence = 0.0
        else:
            dimension("impact", 20, {"surface": 10 * f["surface"], "automation": 10 * f["automation"],
                      "research": 10 * f["research"], "market": 10 * f["market"],
                      "publisher_spread": 5 * min(n, 4)},
                      "Potential relevance from cited keywords and bounded spread; not realized impact.")
            raw_conf = dimension("confidence", 25, {"publisher_spread": 10 * min(n, 4),
                                 "signal_strength": 20 * strength},
                                 "Heuristic evidence confidence, not a calibrated probability.",
                                 cap=35 if n < 2 else 85)
            confidence = round(raw_conf / 100, 3)
            dimension("effort", 70, {"surface": -20 * f["surface"], "documentation": -10 * f["docs"],
                      "complexity": 20 * f["complexity"]},
                      "Higher is harder: a documented surface may lower experiment effort; scope is unknown.")
            dimension("risk", 60, {"publisher_spread": -5 * min(max(n - 1, 0), 3),
                      "uncorroborated": 15 * (n < 2), "risk_language": 25 * (f["risk"] or s.kind == "risk"),
                      "complexity": 10 * f["complexity"]},
                      "Higher is worse: uncertainty persists even with multiple publishers; keyword risks need checking.")
            dimension("time_to_value", 25, {"documentation": 25 * f["docs"], "surface": 15 * f["surface"],
                      "automation": 10 * f["automation"], "complexity": -25 * f["complexity"]},
                      "Higher means faster to test a hypothesis, not a promised delivery date or financial return.")
            dimension("strategic_alignment", 20, {"nexara_workflow_terms": 40 * f["alignment"],
                      "technical_surface": 15 * (s.entity_type in self.TECHNICAL_TYPES and (f["surface"] or f["automation"])),
                      "explicit_nexara": 10 * f["nexara"]},
                      "Lexical overlap with NEXARA research/memory/automation; integration fit is unverified.")
        contributions = {key: weight * (100 - scores[key] if key in {"effort", "risk"} else scores[key])
                         for key, weight in self.WEIGHTS.items()}
        scores["overall"] = int(round(sum(contributions.values()))) if evidence else 0
        components["overall"] = {"value": scores["overall"], "weights": dict(self.WEIGHTS),
                                 "contributions": contributions, "thresholds": dict(self.SHORTLIST),
                                 "evidence_gate": bool(evidence),
                                 "rationale": "Weighted sum, effort/risk inverted; forced zero without evidence."}
        return scores, components, confidence

    def _answer_questions(self, s: Signal, evidence: list[dict], publishers: list[str],
                          features: dict, next_step: str) -> tuple[dict, dict]:
        """One traceable answer per user question; absence is stated, not implied."""
        answers, support = {}, {}
        for key, (feature, description) in self.QUESTION_SPEC.items():
            hits = features.get(feature, []) if evidence else []
            terms = sorted({term for hit in hits for term in hit["terms"]})[:6]
            urls = [hit["url"] for hit in hits][:MAX_EVIDENCE]
            cited = sorted({_publisher(ev) for ev in evidence if ev["url"] in set(urls)})
            if not evidence:
                answers[key] = (f"No: no usable evidence for {s.entity}, so the {key} hypothesis "
                                f"is unassessed. {next_step}")
            elif not hits:
                answers[key] = (f"No: {len(evidence)} citation(s) for {s.entity} show no {description}, "
                                f"so the {key} hypothesis is unsupported. Absence in cited text is "
                                f"not proof of absence in the world.")
            else:
                answers[key] = (
                    f"Possibly: {len(urls)} citation(s) across {len(cited)} publisher(s) mention "
                    f"{description} — {', '.join(terms)}. Keyword evidence is a hypothesis, "
                    f"not a validated {key} outcome. {next_step}")
            support[key] = {"feature": feature, "terms": terms, "citations": urls,
                            "publishers": cited, "answered": bool(hits),
                            "basis": "cited keyword match" if hits else "no matching cited text"}
        return answers, support

    def evaluate(self, signals: list[Signal], min_sources: int = 2) -> list[dict]:
        """Return JSON-ready evaluations; opportunity payloads are already dicts."""
        evaluations = []
        for s in signals:
            evidence = _citations(s.evidence)
            publishers = sorted({_publisher(ev) for ev in evidence})
            features = self._features(evidence)
            scores, components, conf = self._score(s, evidence, publishers, features)
            corroborated = len(publishers) >= max(2, min_sources)
            decision = "reject" if not evidence else "watch"
            if (corroborated and scores["overall"] >= self.SHORTLIST["overall"]
                    and scores["confidence"] >= self.SHORTLIST["confidence"]
                    and scores["risk"] <= self.SHORTLIST["risk_max"]):
                decision = "shortlist"
            rationale = (f"{len(evidence)} citation(s), {len(publishers)} publisher(s); "
                         f"{decision} hypothesis at overall {scores['overall']}, confidence {scores['confidence']}, "
                         f"risk {scores['risk']}." if evidence else
                         "No usable evidence; reject the hypothesis, not the entity.")
            next_step = (f"Collect citable evidence about {s.entity}." if not evidence else
                         f"Seek independent corroboration for {s.entity}." if not corroborated else
                         f"Validate the cited claims about {s.entity} in a bounded research experiment.")
            questions, question_support = self._answer_questions(
                s, evidence, publishers, features, next_step)
            evaluations.append({
                "id": _stable_id("eval", s.to_dict()["id"]),
                "signal_id": s.to_dict()["id"], "entity": s.entity,
                "entity_type": s.entity_type, "kind": s.kind,
                # entity_kind names the signal behaviour behind this entity
                # (emergence/momentum/risk/convergence); `kind` stays as the
                # legacy alias so existing readers keep working.
                "entity_kind": s.kind,
                "decision": decision, "reasons": [rationale],
                "questions": questions,
                "question_support": question_support,
                "scores": scores,
                "score_components": components,
                "evidence": evidence, "distinct_sources": len(publishers), "source_names": publishers,
                "confidence": conf,
                "is_live_data": bool(evidence) and all(ev.get("is_live_data") is True for ev in evidence),
                "created_at": time.time(), "next_step": next_step,
                "opportunity": None, "is_hypothesis": True, "rationale": rationale,
                "assumptions": ["Discussion volume is not validated value.",
                                "Unknown effort and risk are not zero.",
                                "No financial data or monetary estimate is available."],
                "risk_band": "High" if scores["risk"] > 65 else "Medium" if scores["risk"] > 30 else "Low",
                "monetary_estimate": None,
            })
        for row in evaluations:
            if row["distinct_sources"] >= max(2, min_sources):
                row["opportunity"] = self._opportunity(row).to_dict()
        return evaluations

    @staticmethod
    def _opportunity(row: dict) -> Opportunity:
        scores = row["scores"]
        return Opportunity(
            title=f"{row['entity']}: research hypothesis",
            thesis=f"Hypothesis: research {row['entity']}; value remains unvalidated.",
            entities=row["entity"].split(" + ") if row["kind"] == "convergence" else [row["entity"]],
            kind="research", supporting_signals=[row["signal_id"]],
            distinct_sources=row["distinct_sources"], evidence=row["evidence"],
            questions=row["questions"], confidence=row["confidence"], is_hypothesis=True,
            risks=["Publisher diversity does not establish claim truth or business value."],
            next_step=row["next_step"], impact=scores["impact"], confidence_score=scores["confidence"],
            effort=scores["effort"], risk=scores["risk"], time_to_value=scores["time_to_value"],
            strategic_alignment=scores["strategic_alignment"], overall=scores["overall"],
            scores=scores, score_components=row["score_components"], decision=row["decision"],
            rationale=row["rationale"], assumptions=row["assumptions"], risk_band=row["risk_band"],
            monetary_estimate=None, created_at=row["created_at"], is_live_data=row["is_live_data"],
        )

    def generate(self, signals: list[Signal], min_sources: int = 2) -> list[Opportunity]:
        """Legacy adapter; scoring and gating live exclusively in evaluate()."""
        names = {f.name for f in fields(Opportunity)}
        return [Opportunity(**{k: v for k, v in row["opportunity"].items() if k in names})
                for row in self.evaluate(signals, min_sources) if row["opportunity"] is not None]


if __name__ == "__main__":
    # quick smoke test
    from services.world_intelligence.entities import extract
    obs = []
    for row in [{"title": "MCP Release", "url": "https://a.example/1", "source": "github", "category": "open-source"}]:
        for e in extract(f"{row['title']}. {row.get('text','')}", row.get("url", "")):
            obs.append({"entity": e.name, "type": e.type, "method": e.method, "weight": e.weight, "item": row})
    eng = SignalEngine({})
    sigs = eng.detect(obs)
    for s in sigs:
        print(s.to_dict())