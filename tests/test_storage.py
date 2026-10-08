from types import SimpleNamespace
from unittest.mock import patch

import test_downloads
from test_downloads import PAYLOAD, Base, manifest

from archive_station.engine import Interrupted
from archive_station.storage import capacity
from archive_station.store import Store
from archive_station.web import WebApp


class StorageTests(Base):
    def test_capacity_accounts_for_active_queue_on_same_volume(self):
        self.downloads.mkdir()
        self.add()
        with patch(
            "archive_station.storage.shutil.disk_usage",
            return_value=SimpleNamespace(free=2 * 1024**3),
        ):
            result = capacity(self.store, self.settings, str(self.downloads), 1024**3)
        self.assertFalse(result["fits"])
        self.assertEqual(result["available"], 1024**3 - len(PAYLOAD))

    def test_low_space_stops_before_writing_and_preserves_partial_after_restart(self):
        job = self.add()
        row = self.store.claim()
        partial = self.partial(row, PAYLOAD[:100])
        with patch(
            "archive_station.engine.shutil.disk_usage", return_value=SimpleNamespace(free=1024)
        ):
            with self.assertRaises(Interrupted):
                self.engine.transfer(row)
        self.assertEqual(partial.read_bytes(), PAYLOAD[:100])
        self.store.close()
        self.store = Store(self.root / "state.sqlite3")
        self.engine.store = self.store
        saved = self.store.jobs()[0]
        self.assertEqual((saved["status"], saved["hold_reason"]), ("paused", "disk"))
        self.store.action(job, "resume")
        self.engine.transfer(self.store.claim())
        self.assertEqual(self.target().read_bytes(), PAYLOAD)

    def test_space_is_checked_again_during_transfer(self):
        self.add()
        row = self.store.claim()
        with patch(
            "archive_station.engine.shutil.disk_usage",
            side_effect=[
                SimpleNamespace(free=2 * 1024**3),
                SimpleNamespace(free=2 * 1024**3),
                SimpleNamespace(free=1024),
            ],
        ):
            with self.assertRaises(Interrupted):
                self.engine.transfer(row)
        partial = self.downloads / f".archive-station-parts/{row['job_id']}/{row['id']}.part"
        self.assertEqual(partial.read_bytes(), PAYLOAD[:65536])


class CapacityApiTests(Base):
    request = test_downloads.ApiTests.request

    def setUp(self):
        super().setUp()
        self.app = WebApp(self.store, self.client, self.settings, self.root / "data", no_auth=True)

    def test_oversized_job_can_only_be_added_paused(self):
        self.app.no_auth = True
        token = self.app.plans.create(manifest(), "all", "")
        body = {"url": "test-item", "plan_id": token}
        with patch(
            "archive_station.storage.shutil.disk_usage", return_value=SimpleNamespace(free=1024)
        ):
            self.assertEqual(self.request("/api/jobs", body)["status"], 409)
            self.assertEqual(self.store.jobs(), [])
            self.assertEqual(self.request("/api/jobs", {**body, "paused": True})["status"], 201)
