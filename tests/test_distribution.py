import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_downloads import Base

from archive_station.distribution import Distribution
from archive_station.notifications import Notifications
from archive_station.updates import Updates
from archive_station.web import WebApp
from scripts.stage_synology import stage

ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(Base):
    def test_service_options_are_validated_and_cannot_be_changed_by_settings(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Distribution.from_env(), Distribution())
        with patch.dict(
            os.environ,
            {
                "ARCHIVE_STATION_PACKAGE_ID": "archivestation",
                "ARCHIVE_STATION_SERVICE_USER": "sc-archivestation",
                "ARCHIVE_STATION_PACKAGE_CENTER": "1",
            },
            clear=True,
        ):
            app = WebApp(self.store, self.client, self.settings, self.root, no_auth=True)
        self.addCleanup(app.updates.shutdown)
        self.settings.update({"check_updates": True})
        status, settings, *_ = app.dispatch({"PATH_INFO": "/api/settings", "REQUEST_METHOD": "GET"})
        self.assertEqual(status, 200)
        self.assertEqual(
            settings["distribution"], {"service_user": "sc-archivestation", "update_checks": False}
        )
        self.assertFalse(app.updates.enabled)
        for key, value in [
            ("ARCHIVE_STATION_PACKAGE_ID", "../outside"),
            ("ARCHIVE_STATION_SERVICE_USER", "<b>root</b>"),
            ("ARCHIVE_STATION_PACKAGE_CENTER", "false"),
        ]:
            with patch.dict(os.environ, {key: value}, clear=True), self.assertRaises(ValueError):
                Distribution.from_env()

    def test_disabled_distribution_ignores_saved_opt_in_cache_and_manual_checks(self):
        self.settings.update({"check_updates": True, "update_prereleases": True})
        cache = {
            "state": "ok",
            "checked_at": 100,
            "include_prereleases": True,
            "release": {"tag": "v99.0.0-1", "prerelease": False},
        }
        (self.root / "updates.json").write_text(json.dumps(cache))
        fetch = Mock(side_effect=AssertionError("Disabled distribution queried GitHub"))
        updates = Updates(self.settings, self.root, fetch=fetch, enabled=False)
        self.addCleanup(updates.shutdown)
        updates.start()
        updates.tick()
        app = WebApp(
            self.store,
            self.client,
            self.settings,
            self.root,
            no_auth=True,
            updates=updates,
            distribution=Distribution(update_checks=False),
        )
        status, value, *_ = app.dispatch(
            {
                "PATH_INFO": "/api/updates/check",
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": "application/json",
                "wsgi.input": io.BytesIO(b"{}"),
                "CONTENT_LENGTH": "2",
                "REMOTE_ADDR": "127.0.0.1",
            }
        )
        self.assertEqual(status, 202)
        self.assertEqual(value["state"], "disabled")
        self.assertIsNone(value["release_url"])
        self.assertIsNone(value["latest_version"])
        self.assertIsNone(updates.scheduler)
        self.assertIsNone(updates.worker)
        fetch.assert_not_called()
        self.assertEqual(json.loads((self.root / "updates.json").read_text()), cache)

    def test_notification_namespace_follows_the_installed_package(self):
        notifier = Notifications(self.store, self.settings, package_id="archivestation")
        with patch("archive_station.notifications.subprocess.run") as run:
            notifier.deliver("completed", "event-key")
        self.assertEqual(
            run.call_args.args[0][-2:],
            ["archivestation:notifications:title", "archivestation:notifications:completed"],
        )

    def test_permission_error_exposes_account_as_translation_data(self):
        app = WebApp(
            self.store,
            self.client,
            self.settings,
            self.root,
            no_auth=True,
            distribution=Distribution(service_user="sc-archivestation"),
        )
        self.addCleanup(app.updates.shutdown)
        with patch.object(app, "dispatch", side_effect=PermissionError):
            value = json.loads(b"".join(app({}, lambda *args: None)))
        self.assertIn("{account}", value["error"])
        self.assertEqual(value["error_values"], {"account": "sc-archivestation"})


class StagingTests(unittest.TestCase):
    def test_both_editions_use_identical_sources_assets_and_catalogs(self):
        with tempfile.TemporaryDirectory() as directory:
            for package, python in [
                ("ArchiveStation", "python/bin/python3"),
                ("archivestation", "env/bin/python3"),
            ]:
                with self.subTest(package=package):
                    root = Path(directory) / package
                    stage(root, "1.0.1-1", package, python)
                    config = json.loads((root / "ui/config").read_text())[".url"][
                        "com.archivestation.app"
                    ]
                    self.assertEqual(
                        config["url"], f"/webman/3rdparty/{package}/web/index.html?v=1.0.1-1"
                    )
                    gateway = root / "ui/gateway.cgi"
                    self.assertTrue(
                        gateway.read_text().startswith(
                            f"#!/var/packages/{package}/target/{python}\n"
                        )
                    )
                    self.assertTrue(gateway.stat().st_mode & 0o111)
                    self.assertNotIn("{", (root / "ui/style.css").read_text())
                    self.assertIn("app.js?v=1.0.1-1", (root / "ui/web/index.html").read_text())
                    for path in (ROOT / "src/archive_station").rglob("*"):
                        if not path.is_file() or "__pycache__" in path.parts:
                            continue
                        relative = path.relative_to(ROOT / "src/archive_station")
                        self.assertEqual(
                            (root / "app/archive_station" / relative).read_bytes(),
                            path.read_bytes(),
                        )
                    for name in [
                        "app.js",
                        "i18n.js",
                        "style.css",
                        "locales/fr.json",
                        "locales/ja.json",
                    ]:
                        self.assertEqual(
                            (root / "ui/web" / name).read_bytes(),
                            (ROOT / "src/archive_station/static" / name).read_bytes(),
                        )
                    self.assertFalse((root / "python").exists())

    def test_invalid_paths_are_rejected_before_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            for package, python in [
                ("bad/name", "env/bin/python3"),
                ("valid", "../python3"),
                ("valid", "/bin/python3"),
                ("valid", "bin/python3\ncode"),
            ]:
                with self.assertRaises(ValueError):
                    stage(Path(directory) / "output", "1.0.1-1", package, python)
            self.assertFalse((Path(directory) / "output").exists())
