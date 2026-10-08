"""Exercise live settings, real process loss, and persistent download summaries."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time

from test_downloads import PAYLOAD, Base, Response, manifest

from archive_station.engine import CHUNK, Engine
from archive_station.reports import Reports
from archive_station.store import Store


class ResilienceTests(Base):
    def wait_until(self, predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("Timed out waiting for transfer state")

    def test_concurrency_changes_from_three_to_five_without_restart(self):
        data = manifest()
        data["files"] = [{**data["files"][0], "name": f"file-{i}.zip"} for i in range(12)]
        self.store.add(data, "all", "", self.downloads)
        release = threading.Event()
        opened = []

        class SlowResponse(Response):
            def read(self, size):
                release.wait(8)
                return super().read(size)

        def open_file(identifier, name, offset=0):
            opened.append(name)
            return SlowResponse(PAYLOAD)

        self.client.open = open_file
        self.engine.start()
        try:
            self.wait_until(lambda: len(opened) == 3)
            self.settings.update({"connections": 5})
            self.wait_until(lambda: len(opened) == 5)
            self.assertEqual(self.store.jobs()[0]["active_files"], 5)
            self.settings.update({"connections": 1})
            time.sleep(0.6)
            self.assertEqual(len(opened), 5)  # Existing streams are allowed to finish.
        finally:
            release.set()
        self.wait_until(lambda: self.store.jobs()[0]["status"] == "completed")

    def test_process_kill_resumes_partial_without_publishing_incomplete_file(self):
        job = self.add()
        self.store.close()
        # The child runs the actual engine and SQLite store, blocking after its
        # first chunk so SIGKILL is deterministic and cannot run cleanup handlers.
        script = r"""
import sys, time
from pathlib import Path
from archive_station.config import Settings
from archive_station.engine import Engine
from archive_station.store import Store
from test_downloads import PAYLOAD, Response
root = Path(sys.argv[1])
store = Store(root / 'state.sqlite3')
settings = Settings(root / 'data', root / 'downloads', [root])
class Stream(Response):
    count = 0
    def read(self, size):
        self.count += 1
        if self.count == 2:
            (root / 'ready').touch()
            time.sleep(60)
        return super().read(size)
class Client:
    def open(self, *args): return Stream(PAYLOAD)
Engine(store, Client(), settings).transfer(store.claim())
"""
        child = subprocess.Popen(
            [sys.executable, "-c", script, str(self.root)],
            env={**os.environ, "PYTHONPATH": os.pathsep.join(["src", "tests"])},
        )
        try:
            self.wait_until(lambda: (self.root / "ready").exists())
            partial = next(self.downloads.rglob("*.part"))
            self.assertEqual(partial.stat().st_size, CHUNK)
            self.assertFalse(self.target().exists())
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            self.store = Store(self.root / "state.sqlite3")
            self.engine = Engine(self.store, self.client, self.settings)
        self.assertEqual(self.store.jobs()[0]["id"], job)
        self.engine.transfer(self.store.claim())
        self.assertEqual(self.client.offsets, [CHUNK])
        self.assertEqual(self.target().read_bytes(), PAYLOAD)

    def test_report_tracks_partial_then_completion_and_is_atomic(self):
        job = self.store.add(
            manifest(),
            "all",
            "",
            self.downloads,
            source_url="https://archive.org/download/test-item",
        )
        reports = Reports(self.store, self.settings)
        row = self.store.claim()
        self.partial(row, PAYLOAD[:100])
        self.store.update(row["id"], downloaded=100)
        reports.update()
        path = self.downloads / "test-item" / f"ArchiveStation-report-{job}.json"
        report = json.loads(path.read_text())
        self.assertEqual(report["downloaded_bytes"], 100)
        self.assertEqual(report["completed_bytes"], 0)
        self.engine.transfer(row)
        self.store.finish_jobs()
        reports.update()
        report = json.loads(path.read_text())
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["source_url"], "https://archive.org/download/test-item")
        self.assertEqual(report["completed_bytes"], len(PAYLOAD))
        self.assertIsNotNone(report["finished_at"])
        self.assertGreaterEqual(report["duration_seconds"], 0)
        self.assertEqual(list(path.parent.glob(".archive-station-report-*")), [])
        path.write_text('{"user": "do not overwrite"}')
        with self.assertRaises(ValueError):
            reports.write(self.store.jobs()[0])
        self.assertEqual(json.loads(path.read_text()), {"user": "do not overwrite"})

    def test_old_database_migrates_without_changing_download_progress(self):
        old = self.root / "legacy.sqlite3"
        with sqlite3.connect(old) as connection:
            connection.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, identifier TEXT NOT NULL "
                "UNIQUE, title TEXT NOT NULL, status TEXT NOT NULL, created REAL "
                "NOT NULL, mode TEXT NOT NULL, pattern TEXT NOT NULL, destination "
                "TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO jobs VALUES ('legacy','legacy','Legacy','paused',1000,'all','',?)",
                (str(self.downloads),),
            )
        migrated = Store(old)
        try:
            self.assertEqual(migrated.db.execute("SELECT status FROM jobs").fetchone()[0], "paused")
            self.assertIn(
                "source_url", {row[1] for row in migrated.db.execute("PRAGMA table_info(jobs)")}
            )
        finally:
            migrated.close()
