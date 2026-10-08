import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from archive_station.config import Settings
from archive_station.updates import API_URL, INTERVAL, Updates, fetch_releases, select_release
from archive_station.web import WebApp


def release(version, **values):
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "html_url": "https://untrusted.example/",
        "assets": [
            {"name": f"ArchiveStation-{version}-x86_64.spk", "state": "uploaded", "size": 42}
        ],
        **values,
    }


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = Settings(self.root, self.root / "downloads", [self.root])
        self.clock = Mock(return_value=1000000.0)
        self.fetch = Mock(return_value=[release("0.2.0-10")])
        self.updates = self.service()

    def service(self, current="0.2.0-9"):
        updates = Updates(self.settings, self.root, current, self.fetch, self.clock)
        self.addCleanup(updates.shutdown)
        return updates

    def finish(self, updates=None):
        updates = updates or self.updates
        self.assertIsNotNone(updates.worker)
        updates.worker.join(timeout=2)
        self.assertFalse(updates.worker.is_alive())
        return updates.snapshot()

    def test_opt_in_and_once_daily_persist_across_restarts(self):
        self.updates.tick()
        self.fetch.assert_not_called()
        self.assertEqual(self.updates.snapshot()["state"], "disabled")
        self.settings.update({"check_updates": True})
        self.updates.configure()
        self.updates.tick()
        self.assertEqual(self.finish()["state"], "available")
        restarted = self.service()
        restarted.tick()
        self.assertEqual(self.fetch.call_count, 1)
        self.clock.return_value += INTERVAL - 1
        restarted.tick()
        self.assertEqual(self.fetch.call_count, 1)
        self.clock.return_value += 1
        restarted.tick()
        self.finish(restarted)
        self.assertEqual(self.fetch.call_count, 2)

    def test_manual_check_works_with_automatic_checks_off_and_throttles_clicks(self):
        self.updates.request()
        result = self.finish()
        self.assertEqual(result["latest_version"], "0.2.0-10")
        self.assertEqual(result["state"], "available")
        self.assertEqual(result["retry_after"], 60)
        self.assertEqual(
            result["release_url"],
            "https://github.com/jbdemonte/synology-archive-downloader/releases/tag/v0.2.0-10",
        )
        self.updates.request()
        self.assertEqual(self.fetch.call_count, 1)
        self.clock.return_value += 60
        self.updates.request()
        self.finish()
        self.assertEqual(self.fetch.call_count, 2)

    def test_filter_drafts_previews_and_missing_packages_then_compare_build_numbers(self):
        versions = [
            release("0.3.0-1", draft=True),
            release("0.2.0-12", prerelease=True),
            release("0.2.0-11", assets=[]),
            release("0.2.0-10"),
            release("0.2.0-9"),
            release("0.2.0-15", assets=[{"name": "arm.spk", "state": "uploaded", "size": 42}]),
            release("0.2.0-16", tag_name="../../malicious"),
            None,
        ]
        self.assertEqual(select_release(versions, False)["tag"], "v0.2.0-10")
        self.assertEqual(select_release(versions, True)["tag"], "v0.2.0-12")
        for state, size in [("new", 42), ("uploaded", 0), ("uploaded", True)]:
            candidate = release("0.2.0-17")
            candidate["assets"][0].update(state=state, size=size)
            self.assertIsNone(select_release([candidate], True))
        with self.assertRaises(ValueError):
            select_release({"message": "API error"}, False)

    def test_installed_version_recomputed_after_upgrade_and_never_downgrades(self):
        self.updates.request()
        self.finish()
        for current in ["0.2.0-10", "0.3.0-1", "1.0.0-1"]:
            self.assertEqual(self.service(current).snapshot()["state"], "current")
        self.assertEqual(self.fetch.call_count, 1)

    def test_none_private_rate_limited_and_offline_are_distinct_from_up_to_date(self):
        for answer, expected in [
            ([], "none"),
            (HTTPError(API_URL, 404, "Not found", {}, None), "none"),
            (HTTPError(API_URL, 403, "Rate limit", {}, None), "error"),
            (HTTPError(API_URL, 503, "Unavailable", {}, None), "error"),
            (TimeoutError("offline"), "error"),
            ({"message": "invalid response"}, "error"),
        ]:
            with self.subTest(answer=answer):
                self.clock.return_value += 60
                self.fetch.side_effect = answer if isinstance(answer, Exception) else None
                self.fetch.return_value = answer
                with patch("archive_station.updates.LOG"):
                    self.updates.request()
                    result = self.finish()
                self.assertEqual(result["state"], expected)
                self.assertIsNone(result["release_url"])

    def test_manual_request_and_shutdown_do_not_wait_for_github(self):
        entered, finish = threading.Event(), threading.Event()

        def slow_fetch():
            entered.set()
            finish.wait(3)
            return [release("0.2.0-10")]

        self.fetch.side_effect = slow_fetch
        try:
            before = time.monotonic()
            self.assertEqual(self.updates.request()["state"], "checking")
            self.assertLess(time.monotonic() - before, 0.5)
            self.assertTrue(entered.wait(1))
            self.updates.request()
            self.assertEqual(self.fetch.call_count, 1)
            before = time.monotonic()
            self.updates.shutdown()
            self.assertLess(time.monotonic() - before, 0.5)
        finally:
            finish.set()
            self.finish()
        self.assertFalse(self.updates.path.exists())

    def test_change_channel_during_request_discards_old_result(self):
        finish = threading.Event()
        self.fetch.side_effect = lambda: finish.wait(3) and [release("0.2.0-10")]
        self.updates.request()
        try:
            self.settings.update({"update_prereleases": True})
            self.updates.configure()
            self.assertEqual(self.updates.snapshot()["state"], "checking")
        finally:
            finish.set()
            self.finish()
        self.assertEqual(self.updates.snapshot()["state"], "disabled")
        self.assertFalse(self.updates.path.exists())
        self.fetch.side_effect = None
        self.fetch.return_value = [release("0.2.0-12", prerelease=True)]
        self.updates.request()
        self.assertTrue(self.finish()["prerelease"])

    def test_cache_rejects_inconsistent_corrupt_future_and_wrong_channel_results(self):
        valid = {
            "state": "ok",
            "checked_at": self.clock(),
            "include_prereleases": False,
            "release": {"tag": "v0.2.0-10", "prerelease": False},
        }
        for change in [
            {"release": None},
            {"release": {"tag": "javascript:alert(1)", "prerelease": False}},
            {"checked_at": self.clock() + 1},
            {"include_prereleases": True},
            {"state": "error"},
        ]:
            self.updates.path.write_text(json.dumps({**valid, **change}))
            self.assertEqual(self.service().snapshot()["state"], "disabled")
        self.updates.path.write_text("incomplete{")
        self.assertEqual(self.service().snapshot()["state"], "disabled")

    def test_check_api_requires_dsm_session_and_does_not_touch_download_database(self):
        store = Mock()
        app = WebApp(store, None, self.settings, self.root, dsm_auth=True, updates=self.updates)
        env = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/api/updates/check",
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(b"{}"),
            "CONTENT_LENGTH": "2",
            "REMOTE_ADDR": "127.0.0.1",
        }
        self.assertEqual(app.dispatch(env)[0], 401)
        self.fetch.assert_not_called()
        env["HTTP_X_ARCHIVE_STATION_DSM_AUTHENTICATED"] = "1"
        env["wsgi.input"] = io.BytesIO(b"{}")
        self.assertEqual(app.dispatch(env)[0], 202)
        self.finish()
        self.assertEqual(store.mock_calls, [])

    def test_settings_validate_boolean_options_and_scheduler_wakes_when_enabled(self):
        for key in ["check_updates", "update_prereleases"]:
            for value in ["true", 1, None]:
                with self.assertRaises(ValueError):
                    self.settings.update({key: value})
        checked = threading.Event()
        self.fetch.side_effect = lambda: checked.set() or []
        self.updates.start()
        self.settings.update({"check_updates": True})
        self.updates.configure()
        self.assertTrue(checked.wait(2))
        self.finish()
        self.settings.update({"check_updates": False})
        self.updates.configure()
        self.clock.return_value += INTERVAL
        self.updates.tick()
        self.assertEqual(self.fetch.call_count, 1)

    def test_http_request_is_bounded_and_has_no_credentials(self):
        with patch("archive_station.updates.urlopen", return_value=io.BytesIO(b"[]")) as open_url:
            self.assertEqual(fetch_releases(), [])
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, API_URL)
        self.assertEqual(open_url.call_args.kwargs["timeout"], 10)
        self.assertNotIn("authorization", {key.lower() for key in request.headers})
        with (
            patch("archive_station.updates.urlopen", return_value=io.BytesIO(b" " * 2_000_001)),
            self.assertRaisesRegex(ValueError, "too large"),
        ):
            fetch_releases()
