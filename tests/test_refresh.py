import json
import time
from unittest.mock import patch

from test_downloads import PAYLOAD, Base, Response, manifest

from archive_station.archive import ArchiveClient
from archive_station.plans import Plans
from archive_station.refresh import difference


class RefreshTests(Base):
    def test_initial_exclusions_new_changed_absent_and_stale_plan(self):
        data = manifest()
        data["known_names"] = [data["files"][0]["name"], "excluded.zip"]
        job = self.store.add(data, "all", "", self.downloads, paused=True)
        remote = manifest(data=b"changed")
        remote["files"] += [
            {**data["files"][0], "name": name} for name in ["excluded.zip", "new.zip"]
        ]
        diff = difference(self.store, job, remote)
        self.assertEqual((diff["added_count"], diff["changed_count"]), (1, 1))
        self.assertEqual([f["name"] for f in diff["files"]], ["folder/game.zip", "new.zip"])
        self.store.apply_refresh(job, diff, paused=True)
        with self.assertRaisesRegex(ValueError, "expiré"):
            self.store.apply_refresh(job, diff)
        removed = difference(self.store, job, {**remote, "files": []})
        self.assertEqual(removed["absent_count"], 2)
        self.assertEqual(len(self.store.files(job)["files"]), 2)

    def test_refresh_never_reuses_obsolete_partial_and_backs_up_old_content(self):
        job = self.add()
        self.engine.transfer(self.store.claim())
        self.store.finish_jobs()
        row = self.store.files(job)["files"][0]
        self.partial({**row, "job_id": job}, b"old-partial")
        remote = manifest(data=b"updated content")
        self.store.apply_refresh(job, difference(self.store, job, remote))
        self.client.data = b"updated content"
        self.client.offsets.clear()
        self.engine.transfer(self.store.claim())
        self.assertEqual(self.client.offsets, [0])
        self.assertEqual(self.target().read_bytes(), b"updated content")
        backups = list((self.downloads / ".archive-station-replaced").rglob("game.zip"))
        self.assertEqual({p.read_bytes() for p in backups}, {PAYLOAD, b"old-partial"})

    def test_refresh_requires_stopped_workers_and_plan_binding(self):
        job = self.add()
        diff = difference(self.store, job, manifest(data=b"new"))
        with self.assertRaises(ValueError):
            self.store.apply_refresh(job, diff)
        plans = Plans()
        token = plans.create(diff, "all", "", job_id=job)
        with self.assertRaises(ValueError):
            plans.selection(token, "test-item")
        selected, _, _ = plans.selection(token, "test-item", job_id=job)
        self.assertEqual(len(selected["files"]), 1)

    def test_refresh_bypasses_cached_metadata(self):
        client = ArchiveClient()
        client.cache["test-item"] = (
            time.monotonic(),
            {"metadata": {}, "files": [{"name": "old.zip", "size": "1"}]},
        )
        payload = {"metadata": {}, "files": [{"name": "new.zip", "size": "2"}]}
        with patch("archive_station.archive.build_opener") as opener:
            opener.return_value.open.return_value = Response(json.dumps(payload).encode())
            result = client.manifest("test-item", refresh=True)
        self.assertEqual(result["files"][0]["name"], "new.zip")
        opener.return_value.open.assert_called_once()
