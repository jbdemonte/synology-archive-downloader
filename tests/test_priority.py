from test_downloads import Base, manifest

from archive_station.store import Store


class PriorityTests(Base):
    def test_task_priority_and_order_survive_restart(self):
        first = self.add()
        second = self.add(identifier="second")
        third = self.add(identifier="third")
        self.store.prioritize(third, move="up")
        self.assertEqual([j["id"] for j in self.store.jobs()], [first, third, second])
        self.store.prioritize(second, priority=1)
        self.store.close()
        self.store = Store(self.root / "state.sqlite3")
        self.assertEqual(self.store.claim()["job_id"], second)
        self.assertEqual(self.store.claim()["job_id"], first)
        self.assertEqual(self.store.claim()["job_id"], third)

    def test_file_priority_matches_activity_order_and_does_not_interrupt(self):
        data = manifest()
        data["files"] += [
            {**data["files"][0], "name": name} for name in ["second.zip", "third.zip"]
        ]
        job = self.store.add(data, "all", "", self.downloads)
        active = self.store.claim()
        files = self.store.files(job)["files"]
        self.store.prioritize(job, priority=1, file_id=files[2]["id"])
        self.assertEqual(self.store.activity(job)["queued"][0]["id"], files[2]["id"])
        self.assertEqual(self.store.claim()["id"], files[2]["id"])
        self.assertEqual(self.store.files(job)["files"][0]["status"], "downloading")
        with self.assertRaises(ValueError):
            self.store.prioritize(job, priority=1, file_id=active["id"])
        with self.assertRaises(ValueError):
            self.store.prioritize(job, priority=100)

    def test_backoff_skips_unavailable_files_and_jobs_without_changing_priority(self):
        first = self.add()
        second = self.add(identifier="second")
        row = self.store.claim()
        self.store.update(row["id"], status="queued", available_at=10**12)
        self.assertEqual(self.store.claim()["job_id"], second)
        self.assertIsNone(self.store.claim())
        self.store.update(row["id"], available_at=0)
        self.assertEqual(self.store.claim()["job_id"], first)
