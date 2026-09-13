#!/usr/bin/env python3
"""Phase 11 Telegram tests. All fixture input is offline, never live evidence."""
import importlib.util
import json
import re
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def telegram_module():
    name = "services.world_intelligence.telegram_intelligence"
    assert importlib.util.find_spec(name) is not None, "Telegram registry module is missing"
    return __import__(name, fromlist=["IntelligenceRegistry"])


def collectors_module():
    name = "services.world_intelligence.collectors"
    assert importlib.util.find_spec(name) is not None, "Collectors module is missing"
    return __import__(name, fromlist=["Telegram"])


class RegistryTests(unittest.TestCase):
    def test_defaults_seed_only_verified_public_channels_once(self):
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            self.assertEqual({c["username"] for c in registry.channels()},
                             {"telegram", "botnews", "pythontelegrambotchannel"})
            self.assertEqual({t["id"] for t in registry.topics()}, {
                "open-source", "developer-communities", "ai", "startups",
                "business", "technology", "research",
            })
            for channel in registry.channels():
                self.assertTrue(channel["description"])
                self.assertEqual(channel["provenance"]["verification"], "http-200-public-preview")
                self.assertTrue(channel["provenance"]["primary_url"].startswith("https://"))
            row = registry.channels()[0]
            registry.upsert_channel({**row, "enabled": False})
            restarted = telegram_module().IntelligenceRegistry(root)
            self.assertFalse(next(c for c in restarted.channels() if c["id"] == row["id"])["enabled"])

    def test_registry_rejects_unsafe_channel_config_before_persisting(self):
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            valid = dict(registry.channels()[0])
            before = registry.channels()
            for changes in [
                {"username": "@telegram"}, {"username": "foo/bar"}, {"username": "-100123"},
                {"username": "tg"}, {"username": "telegram?token=secret"},
                {"id": "../../escape"}, {"id": ""},
                {"url": "http://t.me/s/telegram"}, {"url": "https://127.0.0.1/"},
                {"url": "https://t.me.evil.test/s/telegram"},
                {"url": "https://t.me@127.0.0.1/s/telegram"},
                {"url": "https://t.me/s/telegram?before=10"},
                {"url": "https://t.me/s/otherchannel"},
                {"enabled": "false"}, {"category": "gambling"},
                {"topics": ["unknown"]}, {"topics": "ai"},
                {"publisher": "token=secret"}, {"publisher": ""},
            ]:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    registry.upsert_channel({**valid, **changes})
            self.assertEqual(registry.channels(), before)

    def test_channel_persists_beneath_supplied_state_root(self):
        with tempfile.TemporaryDirectory() as root:
            module = telegram_module()
            registry = module.IntelligenceRegistry(root)
            channel = registry.upsert_channel({
                "id": "testchannel", "username": "testchannel", "title": "Test channel",
                "url": "https://t.me/s/testchannel", "category": "technology",
                "topics": ["technology"], "enabled": True, "publisher": "example.org",
                "description": "A test channel",
                "provenance": {"primary_url": "https://example.org", "verified_at": "2026-09-10", "verification": "http-200-public-preview"},
            })
            reloaded = module.IntelligenceRegistry(root)
            self.assertIn(channel, reloaded.channels())
            self.assertEqual(channel["id"], "testchannel")
            self.assertTrue(Path(root, "telegram_registry.json").is_file())

    def test_topic_upsert_and_persistence(self):
        with tempfile.TemporaryDirectory() as root:
            module = telegram_module()
            registry = module.IntelligenceRegistry(root)
            topic = registry.upsert_topic({
                "id": "custom-topic", "name": "Custom Topic", "keywords": ["custom", "topic"]
            })
            reloaded = module.IntelligenceRegistry(root)
            self.assertIn(topic, reloaded.topics())
            self.assertEqual(topic["id"], "custom-topic")

    def test_upsert_channel_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            module = telegram_module()
            registry = module.IntelligenceRegistry(root)
            channel = registry.upsert_channel({
                "id": "dup", "username": "dup", "title": "Dup",
                "url": "https://t.me/s/dup", "category": "technology",
                "topics": ["technology"], "enabled": True, "publisher": "example.org",
                "description": "First",
                "provenance": {"primary_url": "https://example.org", "verified_at": "2026-09-10", "verification": "http-200-public-preview"},
            })
            same_id = registry.upsert_channel({**channel, "description": "Second"})
            self.assertEqual(same_id["description"], "Second")
            self.assertEqual(len(registry.channels()), 4)  # 3 defaults + 1


class CollectorTests(unittest.TestCase):
    """Offline tests with captured HTML fixtures."""

    @staticmethod
    def _fixture(name: str) -> str:
        path = Path(__file__).parent / "fixtures" / f"{name}.html"
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    def test_noise_filters_greetings_and_promo(self):
        col = collectors_module().Telegram(registry=None)
        assert col._is_noise("hi there")
        assert col._is_noise("Hello world")
        assert col._is_noise("thanks!")
        assert col._is_noise("🚀")
        assert col._is_noise("Limited offer: buy now 50% off!")
        assert col._is_noise("Subscribe to our channel t.me/example")
        assert not col._is_noise("Released v2.1.0 with new API and bug fixes")

    def test_parse_messages_extracts_items(self):
        col = collectors_module().Telegram(registry=None)
        html = self._fixture("telegram_sample")
        self.assertTrue(html, "Fixture telegram_sample.html not found")
        channel = {
            "id": "telegram", "username": "telegram", "title": "Telegram News",
            "url": "https://t.me/s/telegram", "category": "technology",
            "topics": ["technology"], "enabled": True, "publisher": "telegram.org",
        }
        items = col._parse_messages(html, channel)
        # Verify structure and provenance
        self.assertGreater(len(items), 0)
        for it in items:
            self.assertTrue(it.url.startswith("https://t.me/telegram/"))
            self.assertEqual(it.source, "telegram")
            self.assertEqual(it.category, "technology")
            self.assertIn("channel_id", it.raw)
            self.assertIn("topics", it.raw)
            self.assertFalse(it.raw.get("is_live_data"))
            self.assertTrue(it.published_at > 0)
            self.assertLessEqual(len(it.text), col.MAX_EXCERPT)

    def test_parser_keeps_text_with_own_permalink_and_utc_time(self):
        col = collectors_module().Telegram()
        html = '''<div data-post="telegram/1"><video></video></div>
        <div data-post="telegram/2"><div class="tgme_widget_message_text js-message_text">
        <b>Released</b> v2.1 API <div>nested text</div> for developers &amp; teams.</div>
        <time datetime="2026-09-10T15:00:00+00:00"></time></div>'''
        channel = telegram_module().DEFAULT_CHANNELS[0]
        items = col._parse_messages(html, channel)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://t.me/telegram/2")
        self.assertIn("nested text", items[0].text)
        self.assertTrue(items[0].text.endswith("developers & teams."))
        from datetime import datetime, timezone
        self.assertEqual(items[0].published_at, datetime(2026, 9, 10, 15, tzinfo=timezone.utc).timestamp())
        self.assertFalse(items[0].raw["is_live_data"], "Parsing a fixture is not a live fetch")

    def test_failed_and_disabled_channels_never_report_live_success(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            col = collectors_module().Telegram(registry)
            with patch("urllib.request.urlopen", side_effect=TimeoutError("network timeout")):
                result = col.collect()
            self.assertFalse(result.ok)
            self.assertTrue(result.configured)
            self.assertTrue(result.error)
            self.assertEqual(result.items, [])
            for channel in registry.channels():
                registry.upsert_channel({**channel, "enabled": False})
            with patch.object(col, "_fetch_channel") as fetch:
                result = col.collect()
            fetch.assert_not_called()
            self.assertFalse(result.configured)
            self.assertFalse(result.ok)

    def test_collect_all_with_registry_uses_enabled_channels(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            tg_registry = telegram_module().IntelligenceRegistry(root)
            col = collectors_module().Telegram(registry=tg_registry)
            with patch.object(col, "_fetch_channel", return_value=[]) as fetch:
                res = col.collect(limit=10)
            self.assertTrue(res.configured)
            self.assertEqual(fetch.call_count, 3)
            with patch.object(collectors_module().Telegram, "_fetch_channel", return_value=[]):
                items, reports = collectors_module().collect_all(["telegram"], tg_registry=tg_registry)
            self.assertTrue(reports[0]["configured"])
            self.assertEqual(items, [])


def _html(channel_username: str, posts: list[tuple[int, str]]) -> str:
    """Build a minimal t.me/s/<channel> preview containing the given posts."""
    blocks = []
    for number, text in posts:
        blocks.append(
            f'<div data-post="{channel_username}/{number}">'
            f'<div class="tgme_widget_message_text js-message_text">{text}</div>'
            f'<time datetime="2026-09-10T15:0{number % 10}:00+00:00"></time></div>'
        )
    return "".join(blocks)


class AllowListTests(unittest.TestCase):
    """The allow-list is re-derived from validation, never trusted from disk."""

    LONG_POST = "Released version 2.1.0 of the API with new endpoints and fixes"

    def _registry(self, root):
        return telegram_module().IntelligenceRegistry(root)

    def _poison(self, root, **changes):
        """Write a channel row straight to disk, bypassing upsert validation."""
        path = Path(root, "telegram_registry.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["channels"][0] = {**data["channels"][0], **changes}
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_hand_edited_state_is_quarantined_not_fetched(self):
        for changes in [
            {"url": "https://evil.test/s/telegram"},
            {"username": "telegram", "url": "https://t.me/s/otherchannel"},
            {"category": "gambling"},
            {"topics": ["nonexistent-topic"]},
            {"enabled": "yes"},
            {"provenance": {"primary_url": "http://x.test", "verified_at": "2026-09-10",
                            "verification": "http-200-public-preview"}},
        ]:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as root:
                registry = self._registry(root)
                poisoned_id = registry.channels()[0]["id"]
                self._poison(root, **changes)
                allowed, quarantined = registry.enabled_channels()
                self.assertNotIn(poisoned_id, [c["id"] for c in allowed])
                self.assertIn(poisoned_id, [q["id"] for q in quarantined])
                self.assertTrue(quarantined[0]["reason"])

    def test_quarantined_channel_is_never_fetched(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            registry = self._registry(root)
            self._poison(root, url="https://evil.test/s/telegram")
            col = collectors_module().Telegram(registry)
            with patch.object(col, "_fetch_channel", return_value=[]) as fetch:
                result = col.collect()
            fetched = [call.args[0]["url"] for call in fetch.call_args_list]
            self.assertTrue(all(u.startswith("https://t.me/s/") for u in fetched))
            self.assertEqual(fetch.call_count, 2)
            self.assertTrue(result.details["quarantined"])

    def test_collect_without_registry_fails_closed(self):
        result = collectors_module().Telegram().collect()
        self.assertFalse(result.configured)
        self.assertFalse(result.ok)
        self.assertEqual(result.items, [])
        self.assertIn("fail-closed", result.error)

    def test_bot_polling_is_off_and_says_why(self):
        with tempfile.TemporaryDirectory() as root:
            registry = self._registry(root)
            decision = registry.bot_polling_decision()
            self.assertFalse(decision["active"])
            self.assertTrue(decision["reason"])
            # Even if a future operator flips the flag, no credential path exists.
            registry.store.update("telegram_registry.json",
                                  lambda cur: {**cur, "bot_polling": {"enabled": True, "mode": "dedicated-bot"}})
            self.assertFalse(registry.bot_polling_decision()["active"])
            self.assertFalse(registry.bot_polling_decision(credential_present=True)["active"])

    def test_registry_never_persists_extra_or_secret_bearing_fields(self):
        with tempfile.TemporaryDirectory() as root:
            registry = self._registry(root)
            stored = registry.upsert_channel({
                "id": "extra", "username": "extrachannel", "title": "Extra",
                "url": "https://t.me/s/extrachannel", "category": "technology",
                "topics": ["technology"], "enabled": True, "publisher": "example.org",
                "description": "A channel", "headers": {"Authorization": "Bearer abc"},
                "proxy": "http://127.0.0.1:8080", "bot_token": "123456:AAETTgddsfghjkl_mnopqrstuvwxyz012",
                "provenance": {"primary_url": "https://example.org", "verified_at": "2026-09-10",
                               "verification": "http-200-public-preview"},
            })
            self.assertNotIn("headers", stored)
            self.assertNotIn("proxy", stored)
            self.assertNotIn("bot_token", stored)
            raw = Path(root, "telegram_registry.json").read_text(encoding="utf-8")
            for leaked in ("Authorization", "Bearer", "127.0.0.1", "AAETTgddsfghjkl"):
                self.assertNotIn(leaked, raw)

    def test_registry_rejects_credential_shaped_free_text(self):
        with tempfile.TemporaryDirectory() as root:
            registry = self._registry(root)
            valid = dict(registry.channels()[0])
            for changes in [
                {"description": "use token=abc123 to read this channel"},
                {"description": "bot 123456789:AAHkQvXyZ0123456789abcdefghijklmnop"},
                {"title": "api_key: sk-livesecretvalue"},
            ]:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    registry.upsert_channel({**valid, "id": "cred", "username": "credchannel",
                                             "url": "https://t.me/s/credchannel", **changes})


class CursorReplayTests(unittest.TestCase):
    """Bounded signals, not archives: a replayed window must yield nothing new."""

    POSTS = [(101, "Released version 2.1.0 of the Bot API with new endpoints"),
             (102, "Published a new research paper about agent orchestration benchmarks"),
             (103, "Shipped an SDK update with breaking changes for developers")]

    def _setup(self, root):
        registry = telegram_module().IntelligenceRegistry(root)
        channel = registry.channels()[0]
        col = collectors_module().Telegram(registry)
        return registry, channel, col

    def test_replaying_the_same_preview_yields_no_new_items(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            registry, channel, col = self._setup(root)
            html = _html(channel["username"], self.POSTS)
            for other in registry.channels()[1:]:
                registry.upsert_channel({**other, "enabled": False})
            with patch.object(col, "_read_preview", return_value=html):
                first = col.collect()
                second = col.collect()
            self.assertEqual(len(first.items), 3)
            self.assertEqual(second.items, [], "replay must not re-emit already-ingested posts")
            self.assertTrue(second.ok)
            self.assertEqual(registry.cursor(channel["id"])["last_post_id"], 103)

    def test_cursor_only_admits_posts_newer_than_the_last_ingested(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            registry, channel, col = self._setup(root)
            for other in registry.channels()[1:]:
                registry.upsert_channel({**other, "enabled": False})
            with patch.object(col, "_read_preview", return_value=_html(channel["username"], self.POSTS[:2])):
                col.collect()
            self.assertEqual(registry.cursor(channel["id"])["last_post_id"], 102)
            with patch.object(col, "_read_preview", return_value=_html(channel["username"], self.POSTS)):
                third = col.collect()
            self.assertEqual([i.raw["post_id"] for i in third.items], [103])

    def test_cursor_is_monotonic_and_never_rewinds(self):
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            cid = registry.channels()[0]["id"]
            registry.advance_cursor(cid, 500)
            registry.advance_cursor(cid, 200)
            self.assertEqual(registry.cursor(cid)["last_post_id"], 500)
            reloaded = telegram_module().IntelligenceRegistry(root)
            self.assertEqual(reloaded.cursor(cid)["last_post_id"], 500)

    def test_unknown_channel_has_a_zero_cursor_and_no_crash(self):
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            self.assertEqual(registry.cursor("never-registered")["last_post_id"], 0)
            with self.assertRaises(ValueError):
                registry.advance_cursor("../escape", 5)

    def test_ingestion_stays_bounded_per_channel(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            registry, channel, col = self._setup(root)
            for other in registry.channels()[1:]:
                registry.upsert_channel({**other, "enabled": False})
            many = [(n, f"Release note number {n} describing API changes for developers")
                    for n in range(1, 121)]
            with patch.object(col, "_read_preview", return_value=_html(channel["username"], many)):
                result = col.collect()
            self.assertLessEqual(len(result.items), col.MAX_MESSAGES_PER_CHANNEL)
            for item in result.items:
                self.assertLessEqual(len(item.text), col.MAX_EXCERPT)


class ErrorHandlingTests(unittest.TestCase):
    def test_transport_errors_record_only_the_error_class(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            col = collectors_module().Telegram(registry)
            secret_url = "https://t.me/s/telegram?token=SUPERSECRET123456"
            with patch.object(col, "_fetch_channel", side_effect=TimeoutError(secret_url)):
                result = col.collect()
            self.assertFalse(result.ok)
            self.assertTrue(result.configured)
            self.assertEqual(result.items, [])
            self.assertIn("TimeoutError", result.error)
            self.assertNotIn("SUPERSECRET", result.error)
            state = Path(root, "telegram_cursors.json").read_text(encoding="utf-8")
            self.assertNotIn("SUPERSECRET", state)
            self.assertEqual(registry.cursor("telegram")["last_error_kind"], "TimeoutError")

    def test_one_failing_channel_does_not_suppress_the_others(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            channels = registry.channels()
            col = collectors_module().Telegram(registry)

            def fetch(channel, since_post_id=0):
                if channel["id"] == channels[0]["id"]:
                    raise urllib.error.URLError("boom")
                return col._parse_messages(
                    _html(channel["username"], [(7, "Shipped a new SDK release for developers today")]),
                    channel, is_live_data=True, since_post_id=since_post_id)

            with patch.object(col, "_fetch_channel", side_effect=fetch):
                result = col.collect()
            self.assertFalse(result.ok, "a partial failure is not a success")
            self.assertTrue(result.configured)
            self.assertEqual(len(result.items), 2)
            oks = [r for r in result.details["channels"] if r["ok"]]
            self.assertEqual(len(oks), 2)

    def test_repeated_failures_trigger_cooldown_instead_of_hammering(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            col = collectors_module().Telegram(registry)
            with patch.object(col, "_fetch_channel", side_effect=TimeoutError("t")):
                for _ in range(registry.ERROR_THRESHOLD):
                    col.collect()
            self.assertTrue(registry.is_cooling_down("telegram"))
            with patch.object(col, "_fetch_channel") as fetch:
                result = col.collect()
            fetch.assert_not_called()
            self.assertTrue(any(r.get("skipped") for r in result.details["channels"]))

    def test_a_success_clears_the_error_counter(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            col = collectors_module().Telegram(registry)
            with patch.object(col, "_fetch_channel", side_effect=TimeoutError("t")):
                col.collect()
            self.assertEqual(registry.cursor("telegram")["consecutive_errors"], 1)
            registry.advance_cursor("telegram", 10)
            self.assertEqual(registry.cursor("telegram")["consecutive_errors"], 0)

    def test_oversized_or_empty_previews_are_refused(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            col = collectors_module().Telegram(registry)
            channel = registry.channels()[0]
            with patch.object(col, "_read_preview", side_effect=ValueError("public preview exceeds byte budget")):
                self.assertFalse(col.collect().ok)
            with patch.object(col, "_read_preview", return_value="<html>no posts here</html>"):
                result = col.collect()
            self.assertFalse(result.ok)
            self.assertEqual(result.items, [])
            self.assertEqual(registry.cursor(channel["id"])["last_post_id"], 0)


class PrivacyTests(unittest.TestCase):
    """Public posts still quote people who never opted into this pipeline."""

    def test_personal_identifiers_are_redacted_from_excerpts(self):
        redact = telegram_module().redact
        cases = [
            ("Contact alice@example.com about the release", "alice@example.com", "[email]"),
            ("Ping @some_username for the API details", "@some_username", "[handle]"),
            ("Call +1 415 555 0199 for support enquiries", "555 0199", "[number]"),
            ("Join https://t.me/joinchat/AAAAAEabcdef now", "joinchat", "[invite-link]"),
            ("Mirror at t.me/+XyZprivatelink today", "+XyZprivatelink", "[invite-link]"),
        ]
        for text, secret, marker in cases:
            with self.subTest(text=text):
                out = redact(text)
                self.assertNotIn(secret, out)
                self.assertIn(marker, out)

    def test_redaction_keeps_the_signal(self):
        redact = telegram_module().redact
        out = redact("@dev_team shipped Model Context Protocol v2 with a new streaming API")
        self.assertIn("Model Context Protocol", out)
        self.assertIn("streaming API", out)

    def test_parsed_items_carry_no_personal_identifiers(self):
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            channel = registry.channels()[0]
            col = collectors_module().Telegram(registry)
            html = _html(channel["username"], [
                (11, "Thanks @maintainer_bob — reach me at bob@example.com or +1 415 555 0142 "
                     "about the Model Context Protocol release notes"),
            ])
            items = col._parse_messages(html, channel)
            self.assertEqual(len(items), 1)
            body = items[0].text + items[0].title
            for identifier in ("@maintainer_bob", "bob@example.com", "555 0142"):
                self.assertNotIn(identifier, body)
            self.assertIn("Model Context Protocol", items[0].text)

    def test_excerpts_are_bounded_and_never_full_archives(self):
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            channel = registry.channels()[0]
            col = collectors_module().Telegram(registry)
            long_post = "Release notes for the developer API. " * 200
            items = col._parse_messages(_html(channel["username"], [(21, long_post)]), channel)
            self.assertEqual(len(items), 1)
            self.assertLessEqual(len(items[0].text), col.MAX_EXCERPT)
            self.assertLess(len(items[0].text), len(long_post) / 4)

    def test_no_module_path_reads_credentials_from_the_environment(self):
        for module in (telegram_module(), collectors_module()):
            source = Path(module.__file__).read_text(encoding="utf-8")
            for banned in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "api.telegram.org",
                           "getUpdates", "sendMessage"):
                self.assertNotIn(banned, source,
                                 f"{banned} must not appear in {module.__name__}")

    def test_only_public_preview_urls_are_ever_requested(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            registry = telegram_module().IntelligenceRegistry(root)
            col = collectors_module().Telegram(registry)
            requested = []

            def fake_urlopen(req, timeout=None):
                requested.append(req.full_url)
                raise TimeoutError("stop here")

            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                col.collect()
            self.assertEqual(len(requested), 3)
            for url in requested:
                self.assertTrue(re.fullmatch(r"https://t\.me/s/[A-Za-z0-9_]+", url), url)



if __name__ == "__main__":
    unittest.main()
