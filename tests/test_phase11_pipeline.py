"""Offline integration tests: fixture input never becomes production intelligence."""
import tempfile
import unittest
from unittest.mock import patch

from services.world_intelligence.collectors import Item
from services.world_intelligence.workspace import Workspace


def fixtures(now=1800000000):
    rows = [Item("fixture", "technology", str(i), "MCP API release for automation",
                 "Model Context Protocol release provides open source API workflow automation and research.",
                 f"https://pub{i}.example/release", published_at=now-10, fetched_at=now,
                 raw={"is_live_data": False}) for i in range(4)]
    rows.append(Item("telegram", "technology", "telegram/99", "hello world",
                     "hello world", "https://t.me/telegram/99", fetched_at=now,
                     raw={"channel": "telegram", "channel_id": "telegram", "publisher": "telegram.org", "is_live_data": False}))
    return rows


class PipelineTests(unittest.TestCase):
    def test_selective_cycle_persists_evaluations_scores_and_replays_without_duplicates(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root)
            rows = fixtures()
            reports = [{"source": "fixture", "ok": True, "configured": True, "items": len(rows)}]
            with patch("services.world_intelligence.workspace.C.collect_all", return_value=(rows, reports)):
                first = workspace.run(push=False)
                second = workspace.run(push=False)
            self.assertEqual(second["new_items"], 0)
            evaluations = workspace.evaluations(limit=100)
            signals = workspace.signals(limit=100)
            self.assertTrue(evaluations)
            self.assertEqual(len(evaluations), len(signals))
            self.assertEqual({e["signal_id"] for e in evaluations}, {s["id"] for s in signals})
            self.assertTrue(workspace.opportunities())
            for opportunity in workspace.opportunities():
                self.assertIn("overall", opportunity["scores"])
                self.assertFalse(opportunity["is_live_data"])
            self.assertEqual(first["signals"], second["signals"])
            recent = workspace.store.read("snapshot.json")["items"]
            self.assertFalse(any("hello world" in r["text"] for r in recent))
            self.assertEqual(len(recent), 4)


if __name__ == "__main__":
    unittest.main()
