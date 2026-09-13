"""Phase 11 Brain projection contracts. All storage is temporary."""
import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

from services.common.store import StateStore


class Request:
    def __init__(self, payload):
        self.payload = payload

    def read_json(self):
        return self.payload


class BrainProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.dict(os.environ, {"NEXARA_DATA": self.tmp.name}):
            self.brain = importlib.import_module("services.brain_runtime.main")
        self.store = StateStore(self.tmp.name)
        self.store_patch = patch.object(self.brain, "store", self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

    def test_replayed_knowledge_refreshes_evidence_without_duplicate(self):
        original = {"artifact_id": "opp-fixture", "type": "world_intelligence_opportunity",
                    "summary": "Initial hypothesis", "sources": ["https://a.example/1"],
                    "scores": {"overall": 20}, "is_hypothesis": True}
        improved = {**original, "summary": "Corroborated hypothesis",
                    "sources": ["https://b.example/2"], "scores": {"overall": 45}}
        self.brain.brain2_add(Request(original), {})
        self.brain.brain2_add(Request(improved), {})
        _, result = self.brain.brain2(None, {})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["entries"][0]["summary"], "Corroborated hypothesis")
        self.assertEqual(set(result["entries"][0]["sources"]),
                         {"https://a.example/1", "https://b.example/2"})
        self.assertEqual(result["entries"][0]["scores"]["overall"], 45)


    def test_graph_replay_updates_entities_and_deduplicates_edges_and_trends(self):
        payload = {
            "entities": {"FixtureTech": {"entity_type": "technology", "strength": 0.3,
                                         "roles": ["competitor"], "sources": ["https://a.example/1"]}},
            "signals": [{"id": "fixture-signal", "entity": "FixtureTech", "kind": "announcement",
                         "strength": 0.3, "evidence": [{"url": "https://a.example/1"}]}],
            "relationships": [{"id": "fixture-edge", "from": "FixtureTech", "to": "FixtureChannel",
                               "type": "mentioned_in", "inference": True,
                               "evidence": [{"url": "https://a.example/1"}]}],
            "trends": [{"id": "fixture-trend", "entity": "FixtureTech", "direction": "insufficient_history"}],
        }
        self.brain.brain3_update(Request(payload), {})
        payload["entities"]["FixtureTech"]["strength"] = 0.6
        self.brain.brain3_update(Request(payload), {})
        model = self.store.read(self.brain.BRAIN3)
        self.assertEqual(len(model["signals"]), 1)
        self.assertEqual(model["entities"]["FixtureTech"]["strength"], 0.6)
        self.assertEqual(model["entities"]["FixtureTech"]["roles"], ["competitor"])
        self.assertEqual(len(model.get("relationships", [])), 1)
        self.assertEqual(len(model.get("trends", [])), 1)
        self.assertTrue(model["relationships"][0]["evidence"])


if __name__ == "__main__":
    unittest.main()
