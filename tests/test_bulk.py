from test_downloads import Base


class BulkTests(Base):
    def test_bulk_pause_resume_and_remove_preserve_files_and_cancelled_tasks(self):
        first = self.add()
        second = self.add(identifier="second")
        third = self.add(identifier="third")
        row = self.store.claim()
        self.engine.transfer(row)
        self.store.finish_jobs()
        self.store.action(third, "cancel")
        self.assertEqual(self.store.bulk("pause_all")["updated"], [second])
        self.assertEqual(self.store.bulk("resume_all")["updated"], [second])
        self.assertEqual(self.store.bulk("remove_completed")["updated"], [first])
        self.assertTrue(self.target().exists())
        self.assertEqual(
            {j["id"]: j["status"] for j in self.store.jobs()},
            {second: "queued", third: "cancelled"},
        )

    def test_selection_is_validated_and_busy_removals_are_skipped(self):
        first = self.add()
        second = self.add(identifier="second")
        active = self.store.claim()
        self.store.bulk("pause", [first, second])
        self.assertEqual(
            self.store.bulk("remove", [first, second]), {"updated": [second], "skipped": [first]}
        )
        self.store.update(active["id"], status="queued")
        self.assertEqual(self.store.bulk("remove", [first])["updated"], [first])
        with self.assertRaises(ValueError):
            self.store.bulk("pause", "all")
        with self.assertRaises(ValueError):
            self.store.bulk("repair", [])
