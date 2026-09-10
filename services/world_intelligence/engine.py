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
import math
import re
import time
from dataclasses import dataclass, field, asdict

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
        d["id"] = f"sig-{abs(hash((self.entity, self.kind))) % (10**10):010d}"
        return d


@dataclass
class Opportunity:
    title: str
    thesis: str
    entities: list[str]
    kind: str                      # learn | build | automate | invest | research
    confidence: float              # capped at 0.85 — this is inference
    is_hypothesis: bool
    supporting_signals: list[str]
    distinct_sources: int
    evidence: list[dict]
    questions: list[str]           # the six Phase-10 questions, answered
    risks: list[str]
    next_step: str
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = f"opp-{abs(hash(self.title)) % (10**10):010d}"
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
            items = [o["item"] for o in obs]
            sources = sorted({i["source"] for i in items})
            cats = sorted({i["category"] for i in items})
            mentions = len(items)
            etype = obs[0]["type"]

            # extraction quality gate: heuristic-only entities need corroboration
            methods = {o["method"] for o in obs}
            if methods == {"heuristic"} and len(sources) < 2:
                continue

            text_blob = " ".join(f"{i['title']} {i.get('text','')[:400]}" for i in items)
            momentum_hits = MOMENTUM_TERMS.findall(text_blob)

            # Risk must be attributable to THIS entity, not merely present in the
            # same document. Require the risk term within PROXIMITY chars of an
            # entity mention; otherwise "Amazon" inherits an unrelated "shut down".
            risk_hits = []
            risk_items = set()
            ent_pat = re.compile(re.escape(entity), re.I)
            for i in items:
                blob = f"{i['title']} {i.get('text','')[:400]}"
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

            evidence = [{"title": i["title"][:160], "url": i["url"], "source": i["source"]}
                        for i in items[:6]]
            times = [i.get("published_at") or i.get("fetched_at", time.time()) for i in items]

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
            srcs = sorted(set(sa.source_names) | set(sb.source_names))
            out.append(Signal(
                entity=f"{a} + {b}", entity_type="convergence", kind="convergence",
                strength=round(min(0.3 + 0.15 * n + 0.05 * len(srcs), 0.95), 3),
                mentions=sa.mentions + sb.mentions, distinct_sources=len(srcs),
                source_names=srcs, categories=sorted(set(sa.categories) | set(sb.categories)),
                evidence=(sa.evidence + sb.evidence)[:6],
                first_seen=min(sa.first_seen, sb.first_seen),
                last_seen=max(sa.last_seen, sb.last_seen),
                rationale=f"co-occurred in {n} item(s) across {len(srcs)} source(s)",
                is_single_source=len(srcs) < 2,
            ))
        return out[:12]


class OpportunityEngine:
    """
    Turns signals into evidence-backed hypotheses.

    Answers the six Phase-10 questions for every opportunity, and refuses to
    emit one that has no distinct-source corroboration.
    """

    ACTION_BY_TYPE = {
        "protocol": ("build", "Interoperability layers tend to reward early adopters."),
        "framework": ("build", "Framework shifts change what is cheap to build."),
        "infrastructure": ("automate", "Infrastructure changes are automation surface."),
        "technique": ("research", "Technique shifts change what is feasible."),
        "model": ("learn", "Model capability changes reset assumptions."),
        "company": ("invest", "Company moves signal where capital and talent flow."),
        "open-source-project": ("build", "Active projects are reusable capability."),
        "convergence": ("build", "Convergent trends open combination opportunities."),
    }

    def generate(self, signals: list[Signal], min_sources: int = 2) -> list[Opportunity]:
        opps: list[Opportunity] = []
        for s in signals:
            if s.distinct_sources < min_sources:
                continue  # no corroboration -> no opportunity, only an observation

            kind, why = self.ACTION_BY_TYPE.get(s.entity_type, ("research", "Worth understanding."))
            if s.kind == "risk":
                kind = "research"

            # Confidence: evidence-driven, hard-capped. Inference never reaches certainty.
            conf = 0.25 + 0.10 * min(s.distinct_sources, 4) + 0.25 * s.strength
            if s.kind == "convergence":
                conf += 0.05
            if s.kind == "risk":
                conf -= 0.05
            conf = round(max(0.10, min(conf, 0.85)), 3)

            entities = s.entity.split(" + ") if s.kind == "convergence" else [s.entity]
            verb = {"risk": "poses a risk worth understanding",
                    "emergence": "is emerging",
                    "momentum": "is accelerating",
                    "convergence": "is converging"}.get(s.kind, "is notable")

            opps.append(Opportunity(
                title=f"{s.entity}: {s.kind} across {s.distinct_sources} sources",
                thesis=f"{s.entity} ({s.entity_type}) {verb}. {s.rationale}. {why}",
                entities=entities, kind=kind, confidence=conf, is_hypothesis=True,
                supporting_signals=[s.to_dict()["id"]],
                distinct_sources=s.distinct_sources, evidence=s.evidence,
                questions=self._answer_six(s, kind),
                risks=self._risks(s),
                next_step=self._next_step(s, kind),
            ))
        opps.sort(key=lambda o: (o.confidence, o.distinct_sources), reverse=True)
        return opps

    def _answer_six(self, s: Signal, kind: str) -> list[str]:
        e = s.entity
        return [
            f"Can we learn from this? Yes — {s.mentions} item(s) across "
            f"{', '.join(s.source_names[:3])} document how {e} is being used.",
            f"Can we build from this? {'Likely' if kind == 'build' else 'Possibly'} — "
            f"{e} is a {s.entity_type}; integration cost is unknown until scoped.",
            f"Can we automate this? {'Directly relevant' if kind == 'automate' else 'Indirect'} — "
            f"depends on whether {e} exposes an API or CLI surface.",
            f"Can we invest in this? {'Signal present' if kind == 'invest' else 'Not assessed'} — "
            f"no financial data collected; this is a discussion-volume signal only.",
            f"Can we research this? Yes — {s.distinct_sources} independent source(s) "
            f"provide a starting corpus.",
            f"Can we create value from this? Unproven — requires a scoped experiment; "
            f"strength {s.strength:.2f} reflects attention, not validated value.",
        ]

    def _risks(self, s: Signal) -> list[str]:
        r = []
        if s.is_single_source:
            r.append("Single-source signal — may be noise or promotion.")
        if s.distinct_sources < 3:
            r.append(f"Only {s.distinct_sources} distinct sources — weak corroboration.")
        if s.kind == "momentum":
            r.append("Attention spikes decay; verify persistence in the next window.")
        if s.kind == "risk":
            r.append("Risk language may describe an unrelated context.")
        if s.entity_type == "unclassified":
            r.append("Entity extracted heuristically — identity may be wrong.")
        if s.kind == "emergence":
            r.append("'Emergence' means absent from prior windows — the baseline is "
                     "short, so this may reflect collection history, not the world.")
        if not r:
            r.append(f"Corroborated by {s.distinct_sources} sources, but attention "
                     f"volume is not evidence of value.")
        return r

    def _next_step(self, s: Signal, kind: str) -> str:
        return {
            "build": f"Scope a spike: smallest working integration with {s.entity}.",
            "automate": f"Identify one manual workflow {s.entity} could remove.",
            "research": f"Run a NEXARA research pass on '{s.entity}' and file to Brain 2.",
            "invest": f"Collect financial/adoption data on {s.entity} before any judgement.",
            "learn": f"Read the {s.distinct_sources} cited sources and summarise into Brain 2.",
        }.get(kind, f"Monitor {s.entity} for one more window.")
