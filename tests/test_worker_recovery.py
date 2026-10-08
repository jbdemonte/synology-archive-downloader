import sqlite3
from unittest.mock import patch
from urllib.error import HTTPError

from test_downloads import PAYLOAD, Base, Response


class WorkerRecoveryTests(Base):
    def run_worker(self):
        # Advance retry iterations immediately while keeping a finite failure bound.
        waits = []

        def wait(seconds):
            waits.append(seconds)
            if self.store.jobs()[0]["status"] == "completed" or len(waits) > 10:
                self.engine.stop.set()

        with patch.object(self.engine.stop, "wait", side_effect=wait):
            self.engine.worker(0)
        self.assertEqual(self.store.jobs()[0]["status"], "completed")
        self.assertEqual(self.target().read_bytes(), PAYLOAD)
        return waits

    def test_claim_and_finish_exceptions_do_not_lose_worker(self):
        self.add()
        claim = self.store.claim
        finish = self.store.finish_jobs
        calls = [0, 0]

        def flaky(index, function):
            def call():
                calls[index] += 1
                if calls[index] == 1:
                    raise sqlite3.OperationalError("database or disk is full")
                return function()

            return call

        with (
            patch.object(self.store, "claim", flaky(0, claim)),
            patch.object(self.store, "finish_jobs", flaky(1, finish)),
        ):
            waits = self.run_worker()
        self.assertEqual(waits.count(5), 2)

    def test_error_update_and_repeated_release_failures_preserve_claim(self):
        self.add()
        update, release = self.store.update, self.store.release
        releases = []

        def failed_update(file_id, **values):
            if values.get("error"):
                raise sqlite3.OperationalError("disk full while recording failure")
            return update(file_id, **values)

        def failed_release(file_id):
            releases.append(file_id)
            if len(releases) < 3:
                raise sqlite3.OperationalError("still unavailable")
            return release(file_id)

        with (
            patch.object(self.store, "update", failed_update),
            patch.object(self.store, "release", failed_release),
            patch.object(
                self.client, "open", side_effect=[OSError("network reset"), Response(PAYLOAD)]
            ),
        ):
            self.run_worker()
        self.assertEqual(len(releases), 3)
        self.assertFalse(self.store.destination_locked())

    def test_release_does_not_requeue_a_completed_file(self):
        self.add()
        row = self.store.claim()
        self.engine.transfer(row)
        self.store.release(row["id"])
        self.assertEqual(self.store.files(row["job_id"])["files"][0]["status"], "completed")

    def test_unknown_size_complete_partial_restarts_after_416_and_verifies(self):
        self.add(size=False)
        row = self.store.claim()
        self.partial(row, PAYLOAD)
        error = HTTPError(
            "https://archive.org",
            416,
            "Range Not Satisfiable",
            {"Content-Range": f"bytes */{len(PAYLOAD)}"},
            None,
        )
        with patch.object(self.client, "open", side_effect=[error, Response(PAYLOAD)]) as opened:
            self.engine.transfer(row)
        self.assertEqual([call.args[2] for call in opened.call_args_list], [len(PAYLOAD), 0])
        self.assertEqual(self.target().read_bytes(), PAYLOAD)
        self.assertFalse(list(self.downloads.rglob("*.part")))

    def test_416_fallback_does_not_publish_corrupt_content_or_loop(self):
        self.add(size=False)
        row = self.store.claim()
        self.partial(row, PAYLOAD)
        error = HTTPError("https://archive.org", 416, "Range Not Satisfiable", {}, None)
        with (
            patch.object(self.client, "open", side_effect=[error, Response(b"bad data")]),
            self.assertRaises(OSError),
        ):
            self.engine.transfer(row)
        self.assertFalse(self.target().exists())
        with (
            patch.object(self.client, "open", side_effect=error) as opened,
            self.assertRaises(HTTPError),
        ):
            self.engine.transfer(row)
        self.assertEqual(opened.call_count, 1)
