"""Phase 11 engine contract tests; fixtures are synthetic, never live fetches.

Run without optional dependencies: python3 -m unittest tests.test_phase11_engine -v
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from dataclasses import replace

from services.world_intelligence.engine import Opportunity, OpportunityEngine, Signal, SignalEngine

ROOT = Path(__file__).resolve().parents[1]


def signal(**changes):
    values: dict = dict(entity="Model Context Protocol", entity_type="protocol", kind="emergence",
                  strength=0.8, mentions=3, distinct_sources=3,
                  source_names=["one", "two", "three"], categories=["technology"],
                  evidence=[], first_seen=100.0, last_seen=200.0,
                  rationale="Synthetic observation, not a measured business outcome.",
                  is_single_source=False)
    values.update(changes)
    return Signal(**values)


def legacy_opportunity(**changes):
    values: dict = dict(title="MCP across 3 sources", thesis="Unvalidated integration hypothesis.",
                  entities=["Model Context Protocol"], kind="build", confidence=0.6,
                  is_hypothesis=True, supporting_signals=[signal().to_dict()["id"]],
                  distinct_sources=3, evidence=[], questions=[], risks=["Unvalidated"],
                  next_step="Check the documentation.", created_at=100.0)
    values.update(changes)
    return Opportunity(**values)


def citation(url, title="Model Context Protocol automation release", **changes):
    values = dict(url=url, title=title, text="Reusable API for research and memory workflows.",
                  source="ai-news", category="technology", is_live_data=True,
                  published_at=100.0, collected_at=200.0, topics=["automation", "research"])
    values.update(changes)
    return values


def observation(item, entity="Model Context Protocol", etype="protocol", method="lexicon"):
    return dict(entity=entity, type=etype, method=method, item=item)


class SignalEvidenceTests(unittest.TestCase):
    def test_publishers_not_buckets_include_canonical_telegram_channels(self):
        items = [citation("https://t.me/s/ResearchLab/10", channel="@ResearchLab"),
                 citation("https://telegram.me/researchlab/11", channel="https://t.me/researchlab"),
                 citation("https://t.me/OtherLab/12", channel="OtherLab")]
        result = SignalEngine().detect([observation(i) for i in items])[0]
        self.assertEqual(result.distinct_sources, 2)
        self.assertEqual(result.source_names, ["telegram:otherlab", "telegram:researchlab"])
        self.assertEqual({e["channel"] for e in result.evidence}, {"otherlab", "researchlab"})
        # Same hostname = same publisher regardless of path
        mirrored = [citation("https://primary.example/a", source="ignored"),
                    citation("https://primary.example/b", source="ignored")]
        measured = SignalEngine({"Model Context Protocol": {"mean_mentions": 1, "windows": 2}})
        result = measured.detect([observation(i) for i in mirrored])[0]
        self.assertEqual(result.distinct_sources, 1)
        self.assertTrue(result.is_single_source)
        # Even with different collector "source" values, same hostname = same publisher
        renamed = [citation("https://primary.example/a", source="renamed"),
                   citation("https://primary.example/b", source="renamed")]
        self.assertEqual(measured.detect([observation(i) for i in renamed])[0].distinct_sources, 1)

    def test_retained_citations_bound_and_explain_publisher_count(self):
        items = [citation(f"https://one.example/post/{i}", title=f"MCP update {i}", source="hn")
                 for i in range(9)]
        items += [citation("https://one.example/post/0?utm_source=rss#main", source="rss")]
        items += [citation(f"https://outlet{i}.example/report", title=f"MCP independent {i}")
                  for i in range(8)]
        result = SignalEngine().detect([observation(i) for i in items])[0].to_dict()
        self.assertLessEqual(len(result["evidence"]), 6)
        self.assertEqual(result["distinct_sources"], 6)
        self.assertEqual(result["mentions"], 17)
        self.assertEqual(set(result["source_names"]),
                         {ev["publisher"] for ev in result["evidence"]})
        self.assertEqual(result["distinct_sources"], len(set(result["source_names"])))
        for ev in result["evidence"]:
            self.assertTrue({"publisher", "source", "url", "published_at", "collected_at",
                             "is_live_data", "topics"} <= ev.keys())
            self.assertEqual(ev["topics"], ["automation", "research"])
        reversed_result = SignalEngine().detect([observation(i) for i in reversed(items)])[0]
        self.assertEqual(result, reversed_result.to_dict())

    def test_convergence_cites_shared_urls_only(self):
        items = [citation(f"https://shared.example/pair{i}", topics=["automation", "research"])
                 for i in range(3)]
        items += [citation("https://a-only.example/1", topics=["automation"]),
                  citation("https://b-only.example/2", topics=["research"])]
        # A appears in shared0, shared1, shared2, a-only
        # B appears in shared0, shared1, shared2, b-only
        # Convergence should cite only shared URLs
        a_items = [items[0], items[1], items[2], items[3]]  # shared0, shared1, shared2, a-only
        b_items = [items[0], items[1], items[2], items[4]]  # shared0, shared1, shared2, b-only
        signals = SignalEngine().detect([observation(i, "A", "technique") for i in a_items] +
                                        [observation(i, "B", "technique") for i in b_items])
        conv = SignalEngine().detect_convergence(signals)[0].to_dict()
        self.assertEqual(conv["entity"], "A + B")
        for ev in conv["evidence"]:
            self.assertIn("shared.example", ev["url"])
        self.assertEqual(conv["distinct_sources"], 1)


QUESTION_KEYS = {"build", "learn", "automate", "market", "research", "revenue", "improve_nexara"}
SCORE_KEYS = {"impact", "confidence", "effort", "risk", "time_to_value", "strategic_alignment", "overall"}
EVALUATION_KEYS = {"signal_id", "id", "entity", "entity_type", "kind", "decision", "reasons",
                   "questions", "scores", "evidence", "confidence", "is_live_data", "created_at",
                   "next_step", "opportunity", "score_components", "rationale", "assumptions",
                   "monetary_estimate", "is_hypothesis"}


class EvaluationTests(unittest.TestCase):
    def assert_contract(self, result):
        self.assertTrue(EVALUATION_KEYS <= result.keys(), EVALUATION_KEYS - result.keys())
        self.assertEqual(set(result["questions"]), QUESTION_KEYS)
        self.assertTrue(all(isinstance(v, str) and v for v in result["questions"].values()))
        self.assertEqual(set(result["scores"]), SCORE_KEYS)
        for value in result["scores"].values():
            self.assertIs(type(value), int)
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 100)
        self.assertEqual(set(result["score_components"]), SCORE_KEYS)
        for key, component in result["score_components"].items():
            self.assertEqual(component["value"], result["scores"][key])
            self.assertTrue(component["rationale"])
        self.assertGreaterEqual(result["confidence"], 0)
        self.assertLessEqual(result["confidence"], 0.85)
        self.assertIs(type(result["is_live_data"]), bool)
        self.assertTrue(result["is_hypothesis"])
        self.assertIsNone(result["monetary_estimate"])
        self.assertTrue(result["rationale"])
        self.assertTrue(result["assumptions"])
        self.assertTrue(result["reasons"])
        self.assertTrue(result["next_step"])
        self.assertGreater(result["created_at"], 0)
        json.dumps(result, allow_nan=False)

    def test_missing_evidence_rejects_with_complete_honest_contract(self):
        result = OpportunityEngine().evaluate([signal()])[0]
        self.assert_contract(result)
        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["confidence"], 0)
        self.assertEqual(result["scores"]["overall"], 0)
        self.assertGreaterEqual(result["scores"]["risk"], 60)
        self.assertGreaterEqual(result["scores"]["effort"], 50)
        self.assertIsNone(result["opportunity"])
        self.assertIs(result["is_live_data"], False)
        self.assertEqual(result["entity"], signal().entity)
        self.assertEqual(result["entity_type"], "protocol")
        self.assertEqual(result["kind"], "emergence")
        self.assertEqual(result["signal_id"], signal().to_dict()["id"])
        self.assertTrue(all("hypothesis" in value.lower() for value in result["questions"].values()))


    def test_single_publisher_is_watch_not_a_low_risk_opportunity(self):
        original = citation("http://www.solo.example/update/?utm_source=rss#main")
        duplicate = citation("https://solo.example/update", source="another-collector")
        s = signal(evidence=[original, duplicate], distinct_sources=99, mentions=500,
                   source_names=["fictional"] * 99, strength=100)
        result = OpportunityEngine().evaluate([s])[0]
        self.assert_contract(result)
        self.assertEqual(result["decision"], "watch")
        self.assertEqual(result["distinct_sources"], 1)
        self.assertEqual(result["source_names"], ["solo.example"])
        self.assertEqual(len(result["evidence"]), 1)
        self.assertGreater(result["confidence"], 0)
        self.assertLessEqual(result["confidence"], 0.35)
        self.assertGreaterEqual(result["scores"]["risk"], 60)
        self.assertNotEqual(result["risk_band"].lower(), "low")
        self.assertIsNone(result["opportunity"])
        self.assertIs(result["is_live_data"], True)
        self.assertIn("corrobor", result["next_step"].lower())
        self.assertNotIn("no evidence", " ".join(result["questions"].values()).lower())
        self.assertEqual(s.evidence, [original, duplicate], "evaluation must not mutate caller data")


    def test_corroborated_watch_serializes_opportunity_for_legacy_generate(self):
        s = signal(evidence=[citation("https://one.example/a", title="Protocol notes", text="Context only"),
                             citation("https://two.example/b", title="Protocol notes", text="Context only")],
                   distinct_sources=0, strength=0.2)
        engine = OpportunityEngine()
        result = engine.evaluate([s])[0]
        self.assert_contract(result)
        self.assertEqual(result["decision"], "watch")
        self.assertIsInstance(result["opportunity"], dict)
        opp = result["opportunity"]
        for key in ("questions", "scores", "score_components", "decision", "evidence", "confidence",
                    "is_live_data", "created_at", "next_step", "rationale", "assumptions"):
            self.assertEqual(opp[key], result[key], key)
        self.assertIsNone(opp["monetary_estimate"])
        self.assertTrue(opp["is_hypothesis"])
        self.assertEqual(opp["supporting_signals"], [s.to_dict()["id"]])
        legacy = engine.generate([s])
        self.assertEqual(len(legacy), 1)
        self.assertIsInstance(legacy[0], Opportunity)
        serialized = legacy[0].to_dict()
        self.assertEqual(serialized["id"], opp["id"])
        self.assertEqual(serialized["scores"], result["scores"])
        self.assertEqual(serialized["questions"], result["questions"])
        self.assertEqual(engine.generate([signal(), signal(evidence=s.evidence[:1])]), [])


    def test_explainable_scores_drive_shortlist_with_fixed_threshold(self):
        evidence = [citation(f"https://outlet{i}.example/api", title="NEXARA automation API documentation",
                             text="Research memory workflow tutorial with SDK examples.") for i in range(3)]
        engine = OpportunityEngine()
        result = engine.evaluate([signal(evidence=evidence, strength=0.8)])[0]
        self.assert_contract(result)
        self.assertEqual(result["decision"], "shortlist")
        self.assertIsInstance(result["opportunity"], dict)
        self.assertEqual(result["opportunity"]["scores"], result["scores"])
        self.assertEqual(result["opportunity"]["questions"], result["questions"])
        expected = {"impact": 65, "confidence": 71, "effort": 40, "risk": 50,
                    "time_to_value": 75, "strategic_alignment": 85}
        expected["overall"] = round(.20 * expected["impact"] + .20 * expected["confidence"] +
                                    .15 * (100 - expected["effort"]) + .15 * (100 - expected["risk"]) +
                                    .10 * expected["time_to_value"] + .20 * expected["strategic_alignment"])
        self.assertEqual(result["scores"], expected)
        self.assertEqual(result["confidence"], .71)
        for key, component in result["score_components"].items():
            if key != "overall":
                self.assertIn("base", component)
                self.assertIn("adjustments", component)
                self.assertIn("inputs", component)
        composite = result["score_components"]["overall"]
        self.assertEqual(composite["weights"], {"impact": .20, "confidence": .20, "effort": .15,
                         "risk": .15, "time_to_value": .10, "strategic_alignment": .20})
        self.assertEqual(composite["contributions"]["effort"], .15 * (100 - expected["effort"]))
        self.assertEqual(composite["contributions"]["risk"], .15 * (100 - expected["risk"]))
        self.assertEqual(composite["thresholds"], {"overall": 65, "confidence": 60, "risk_max": 65})
        difficult = [dict(ev, text=ev["text"] + " Breaking migration with an exploit vulnerability.")
                     for ev in evidence]
        risk = engine.evaluate([signal(evidence=difficult, strength=0.8)])[0]
        self.assert_contract(risk)
        self.assertGreater(risk["scores"]["effort"], result["scores"]["effort"])
        self.assertGreater(risk["scores"]["risk"], result["scores"]["risk"])
        self.assertLess(risk["scores"]["time_to_value"], result["scores"]["time_to_value"])
        self.assertLess(risk["scores"]["overall"], result["scores"]["overall"])
        self.assertEqual(risk["decision"], "watch")
        self.assertEqual(risk["risk_band"], "High")
        self.assertIsInstance(risk["opportunity"], dict, "corroborated risk remains a watch hypothesis")


    def test_single_source_single_publisher_and_single_signal_dedupe(self):
        base = citation("https://telegram.org/botnews/1", title="Telegram Bot API release notes",
                        text="Research automation SDK release.")
        dup = citation("https://t.me/botnews/1", channel="botnews", title="Telegram Bot API release notes",
                       text="Research automation SDK release.")
        s = signal(evidence=[base, dup], distinct_sources=99, source_names=["x"] * 99, strength=0.6)
        result = OpportunityEngine().evaluate([s])[0]
        self.assert_contract(result)
        self.assertEqual(result["distinct_sources"], 1)
        self.assertEqual(result["source_names"], ["telegram:botnews"])
        self.assertEqual(len(result["evidence"]), 1)
        self.assertEqual(result["evidence"][0]["url"], "https://telegram.org/botnews/1")
        self.assertEqual(result["signal_id"], s.to_dict()["id"])
        self.assertEqual(result["entity"], s.entity)
        self.assertEqual(result["entity_type"], s.entity_type)
        self.assertEqual(result["entity_kind"], s.kind)

    def test_multiple_signals_one_evaluation_per_unique_signal_id(self):
        sig_a = signal(entity="Model Context Protocol", evidence=[
            citation("https://one.example/a"), citation("https://two.example/b")])
        sig_b = signal(entity="Model Context Protocol", kind="momentum", evidence=[
            citation("https://one.example/a"), citation("https://two.example/b")])
        results = OpportunityEngine().evaluate([sig_a, sig_b])
        self.assertEqual(len(results), 2)
        sig_ids = {r["signal_id"] for r in results}
        self.assertEqual(len(sig_ids), 2)
        self.assertNotEqual(results[0]["id"], results[1]["id"])
        entity_kinds = {r["entity_kind"] for r in results}
        self.assertEqual(len(entity_kinds), 2)

    def test_convergence_signal_is_evaluated(self):
        # Convergence across DISTINCT publishers is corroborated; two entities
        # co-occurring inside one publisher's posts is self-confirmation.
        shared = [citation(f"https://outlet{i}.example/{i}", title="A and B technique API documentation",
                           text="Research memory workflow tutorial with SDK examples for automation.")
                  for i in range(3)]
        a = signal(entity="A", entity_type="technique", evidence=list(shared))
        b = signal(entity="B", entity_type="technique", evidence=list(shared))
        conv = SignalEngine().detect_convergence([a, b])[0]
        results = OpportunityEngine().evaluate([conv])
        self.assertEqual(len(results), 1)
        self.assert_contract(results[0])
        self.assertEqual(results[0]["entity"], "A + B")
        self.assertEqual(results[0]["entity_type"], "convergence")
        self.assertEqual(results[0]["kind"], "convergence")
        self.assertEqual(results[0]["entity_kind"], "convergence")
        self.assertEqual(results[0]["signal_id"], conv.to_dict()["id"])
        self.assertEqual(results[0]["distinct_sources"], 3)
        self.assertEqual(results[0]["decision"], "shortlist")

    def test_convergence_inside_one_publisher_is_not_shortlisted(self):
        same = [citation(f"https://shared.example/{i}", title="A and B technique API documentation",
                         text="Research memory workflow tutorial with SDK examples for automation.")
                for i in range(3)]
        a = signal(entity="A", entity_type="technique", evidence=list(same))
        b = signal(entity="B", entity_type="technique", evidence=list(same))
        conv = SignalEngine().detect_convergence([a, b])[0]
        result = OpportunityEngine().evaluate([conv])[0]
        self.assert_contract(result)
        self.assertEqual(result["distinct_sources"], 1)
        self.assertEqual(result["source_names"], ["shared.example"])
        self.assertEqual(result["decision"], "watch")
        self.assertIsNone(result["opportunity"])


class IdentityTests(unittest.TestCase):
    def test_ids_survive_process_hash_seed_and_mutable_metadata(self):
        code = ("import json; from tests.test_phase11_engine import signal, legacy_opportunity; "
                "print(json.dumps([signal().to_dict()['id'], legacy_opportunity().to_dict()['id']]))")
        identities = []
        for seed in ("1", "77", "random"):
            result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                    env={**os.environ, "PYTHONHASHSEED": seed},
                                    text=True, capture_output=True, check=True)
            identities.append(json.loads(result.stdout))
        self.assertEqual(identities[0], identities[1])
        self.assertEqual(identities[0], identities[2])
        self.assertRegex(identities[0][0], r"^sig-[0-9a-f]{24}$")
        self.assertRegex(identities[0][1], r"^opp-[0-9a-f]{24}$")
        self.assertEqual(signal().to_dict()["id"],
                         signal(mentions=90, distinct_sources=7, first_seen=999).to_dict()["id"])
        self.assertEqual(legacy_opportunity().to_dict()["id"],
                         legacy_opportunity(title="MCP across 7 sources", distinct_sources=7,
                                            created_at=999).to_dict()["id"])
        self.assertNotEqual(signal().to_dict()["id"], signal(kind="risk").to_dict()["id"])


if __name__ == "__main__":
    unittest.main()