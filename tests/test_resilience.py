"""Exercise live settings, real process loss, and persistent download summaries."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from test_downloads import PAYLOAD, Base, Response, manifest

from archive_station.engine import CHUNK, Engine
from archive_station.reports import Reports, duration, render_report, size
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
        self.settings.update({"language": "en"})
        reports = Reports(self.store, self.settings)
        row = self.store.claim()
        self.partial(row, PAYLOAD[:100])
        self.store.update(row["id"], downloaded=100)
        reports.update()
        path = self.downloads / "test-item" / f"ArchiveStation-report-{job}.txt"
        report = path.read_text(encoding="utf-8")
        self.assertIn("Data downloaded          : 100 bytes", report)
        self.assertIn("Completed file data      : 0 bytes", report)
        self.assertIn("Files completed          : 0 / 1", report)
        self.engine.transfer(row)
        self.store.finish_jobs()
        reports.update()
        report = path.read_text(encoding="utf-8")
        self.assertIn("Status                   : Completed", report)
        self.assertIn("https://archive.org/download/test-item", report)
        self.assertIn("Completed file data      : " + size(len(PAYLOAD)), report)
        self.assertNotIn("Task finished            : Not available", report)
        self.assertIn("Duration includes pauses and NAS downtime.", report)
        self.assertEqual(list(path.parent.glob(".archive-station-report-*")), [])
        path.write_text("Personal notes, do not overwrite", encoding="utf-8")
        with self.assertRaises(ValueError):
            reports.write(self.store.jobs()[0])
        self.assertEqual(path.read_text(), "Personal notes, do not overwrite")

    def test_idle_running_report_is_unchanged_until_state_or_settings_change(self):
        self.add()
        row = self.store.claim()
        reports = Reports(self.store, self.settings)
        with patch.object(reports, "write", wraps=reports.write) as write:
            reports.update()
            reports.update()
            self.assertEqual(write.call_count, 1)
            self.store.update(row["id"], downloaded=100)
            reports.update()
            self.assertEqual(write.call_count, 2)
            self.settings.update({"verify_checksums": False})
            reports.update()
            self.assertEqual(write.call_count, 3)

    def test_report_legacy_json_migration_preserves_unrelated_files(self):
        job = self.add()
        root = self.downloads / "test-item"
        root.mkdir(parents=True)
        legacy = root / f"ArchiveStation-report-{job}.json"
        legacy.write_text(
            json.dumps({"application": "Archive Station", "report_version": 1, "job_id": job})
        )
        reports = Reports(self.store, self.settings)
        reports.update()
        self.assertTrue((root / f"ArchiveStation-report-{job}.txt").exists())
        self.assertFalse(legacy.exists())
        for content in ['{"personal": "notes"}', "not JSON"]:
            legacy.write_text(content)
            reports.write(self.store.jobs()[0])
            self.assertEqual(legacy.read_text(), content)
        legacy.unlink()
        external = self.root / "external.json"
        external.write_text(
            json.dumps({"application": "Archive Station", "report_version": 1, "job_id": job})
        )
        legacy.symlink_to(external)
        reports.write(self.store.jobs()[0])
        self.assertTrue(legacy.is_symlink())
        self.assertTrue(external.exists())

    def test_readable_report_formats_duration_sizes_and_french_language(self):
        self.add()
        job = self.store.jobs()[0]
        with patch("archive_station.reports.dsm_language", return_value="fre"):
            report = render_report(job, self.settings.get(), job["created"] + 90061)
        self.assertIn("RAPPORT DE TÉLÉCHARGEMENT", report)
        self.assertIn("1 j 01 h 01 min 01 s", report)
        self.assertIn("En attente", report)
        self.assertIn("UTC", report)
        self.assertIn("0 octets", report)
        self.assertEqual(size(1024**3), "1.00 GiB (1 073 741 824 bytes)")
        self.assertEqual(duration(-1), "0 d 00 h 00 min 00 s")
        with patch("archive_station.reports.dsm_language", return_value="def"):
            self.settings.update({"report_language": "fr"})
            self.assertIn(
                "RAPPORT DE TÉLÉCHARGEMENT", render_report(job, self.settings.get(), job["created"])
            )
        self.settings.update({"language": "en"})
        self.assertIn("DOWNLOAD REPORT", render_report(job, self.settings.get(), job["created"]))

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
