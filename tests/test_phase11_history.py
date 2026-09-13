"""Non-overlapping, replay-safe observation windows (synthetic fixtures)."""
import importlib
import tempfile
import unittest

from services.common.store import StateStore


class HistoryTests(unittest.TestCase):
    def test_replaying_one_item_never_manufactures_two_windows(self):
        with tempfile.TemporaryDirectory() as root:
            module = importlib.import_module("services.world_intelligence.history")
            history = module.TrendHistory(StateStore(root))
            obs = [{"entity": "Ollama", "type": "model", "item": {
                "key": "fixture-item", "observed_at": 360010,
                "title": "Ollama fixture release", "url": "https://a.example/1",
                "source": "fixture", "is_live_data": False}}]
            reports = [{"source": "fixture", "ok": True}]
            history.observe(obs, reports, now=360010)
            history.observe(obs, reports, now=360020)
            row = history.trends(now=360020)[0]
            self.assertEqual(row.direction, "insufficient_history")
            self.assertEqual(row.windows, 0)
            state = history.store.read("trend_history.json")
            self.assertEqual(state["buckets"]["360000"]["counts"]["Ollama"], 1)
            self.assertFalse(row.is_live_data)


    def test_replay_across_buckets_cannot_manufacture_new_mentions(self):
        with tempfile.TemporaryDirectory() as root:
            history = importlib.import_module("services.world_intelligence.history").TrendHistory(StateStore(root))
            obs = [{"entity": "Ollama", "type": "framework", "item": {
                "key": "one", "url": "https://a.example/one", "title": "Ollama release",
                "source": "fixture", "is_live_data": False}}]
            reports = [{"source": "fixture", "ok": True}]
            history.observe(obs, reports, now=360010)
            history.observe(obs, reports, now=363610)
            data = history.store.read("trend_history.json")
            total = sum(bucket.get("counts", {}).get("Ollama", 0) for bucket in data["buckets"].values())
            self.assertEqual(total, 1)

    def test_growth_requires_completed_comparable_windows_with_citations(self):
        with tempfile.TemporaryDirectory() as root:
            history = importlib.import_module("services.world_intelligence.history").TrendHistory(StateStore(root))
            def rows(keys):
                return [{"entity": "Telegram:example", "type": "community", "item": {
                    "key": key, "url": f"https://a.example/{key}", "source": "fixture",
                    "title": "API release", "is_live_data": True}} for key in keys]
            good = [{"source": "fixture", "ok": True}]
            history.observe(rows(["one"]), good, now=360010)
            history.observe(rows(["two", "three", "four"]), good, now=363610)
            partial = history.trends(now=363620)[0]
            self.assertEqual(partial.direction, "insufficient_history")
            row = history.trends(now=367201)[0]
            self.assertEqual(row.direction, "growing")
            self.assertEqual(row.current_mentions, 3)
            self.assertEqual(row.previous_mentions, 1)
            self.assertEqual(row.windows, 2)
            self.assertEqual(row.entity_type, "community")
            self.assertTrue(row.evidence)
            self.assertTrue(row.is_live_data)
            history.observe([], [{"source": "fixture", "ok": False}], now=367210)
            self.assertEqual(history.trends(now=370801)[0].direction, "insufficient_history")

if __name__ == "__main__":
    unittest.main()
