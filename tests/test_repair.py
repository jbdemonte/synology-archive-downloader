from test_downloads import PAYLOAD, Base, manifest

from archive_station.store import Store


class RepairTests(Base):
    def test_repair_reuses_valid_and_recovers_corrupt_or_missing_files(self):
        data = manifest()
        data["files"] += [{**data["files"][0], "name": name} for name in ["bad.zip", "missing.zip"]]
        job = self.store.add(data, "all", "", self.downloads, paused=True)
        root = self.downloads / "test-item"
        self.target().parent.mkdir(parents=True)
        self.target().write_bytes(PAYLOAD)
        (root / "bad.zip").write_bytes(b"x" * len(PAYLOAD))
        self.settings.update({"verify_checksums": False})
        self.store.action(job, "repair")
        self.store.close()
        self.store = Store(self.root / "state.sqlite3")
        self.engine.store = self.store
        while row := self.store.claim():
            self.engine.transfer(row)
        self.store.finish_jobs()
        self.assertEqual(self.client.offsets, [0, 0])  # valid file did not use the network
        for path in [self.target(), root / "bad.zip", root / "missing.zip"]:
            self.assertEqual(path.read_bytes(), PAYLOAD)
        backups = list((self.downloads / ".archive-station-replaced").rglob("bad.zip"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"x" * len(PAYLOAD))
        self.assertEqual(self.store.jobs()[0]["status"], "completed")

    def test_repair_waits_for_stopping_workers_and_retains_pause_controls(self):
        job = self.add()
        row = self.store.claim()
        with self.assertRaises(ValueError):
            self.store.action(job, "repair")
        self.store.action(job, "pause")
        with self.assertRaises(ValueError):
            self.store.action(job, "repair")
        self.store.update(row["id"], status="queued")
        self.store.action(job, "repair")
        self.store.action(job, "pause")
        self.assertIsNone(self.store.claim())
