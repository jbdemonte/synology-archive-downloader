"""DSM desktop alerts, deduplicated on disk and retried without blocking transfers."""

import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

LOG = logging.getLogger(__name__)
COMMAND = "/usr/syno/bin/synodsmnotify"


class Notifications:
    def __init__(self, store, settings, enabled=False, send=None):
        self.store, self.settings = store, settings
        self.enabled = enabled and Path(COMMAND).exists() if send is None else True
        self.send = send or self.deliver
        self.stop = threading.Event()
        self.thread = None
        self.retry_at = 0
        with store.lock, store.db:
            store.db.execute("CREATE TABLE IF NOT EXISTS notifications (event TEXT PRIMARY KEY)")

    @staticmethod
    def deliver(kind, key):
        subprocess.run(
            [
                COMMAND,
                "-c",
                "com.archivestation.app",
                "-t",
                key,
                "@administrators",
                "ArchiveStation:notifications:title",
                f"ArchiveStation:notifications:{kind}",
            ],
            check=True,
            timeout=10,
            capture_output=True,
        )

    def events(self):
        with self.store.lock:
            jobs = self.store.db.execute(
                "SELECT j.id, j.status, j.finished_at, j.destination, EXISTS(SELECT 1 FROM files f "
                "WHERE f.job_id=j.id AND f.status='error') AS failed FROM jobs j"
            ).fetchall()
        events = {}
        for job in jobs:
            if job["status"] == "completed":
                events[f"complete:{job['id']}:{job['finished_at']}"] = "completed"
            elif job["failed"]:
                events[f"error:{job['id']}"] = "error"
            if job["status"] in {"queued", "running", "blocked"}:
                try:
                    reserve = self.settings.get().get("disk_reserve_mib", 1024) * 1024**2
                    if shutil.disk_usage(job["destination"]).free < max(reserve, 131072):
                        events[f"disk:{job['destination']}"] = "disk"
                except OSError:
                    pass
        return events

    def tick(self):
        if not self.enabled or time.monotonic() < self.retry_at:
            return
        events = self.events()
        with self.store.lock, self.store.db:
            known = {r[0] for r in self.store.db.execute("SELECT event FROM notifications")}
            self.store.db.executemany(
                "DELETE FROM notifications WHERE event=?", [(key,) for key in known - events.keys()]
            )
        for key, kind in events.items():
            if key in known:
                continue
            if self.settings.get()["notifications"]:
                try:
                    self.send(kind, key)
                except (OSError, subprocess.SubprocessError):
                    LOG.exception("DSM notification delivery failed; retrying in five minutes")
                    self.retry_at = time.monotonic() + 300
                    return
            with self.store.lock, self.store.db:
                self.store.db.execute("INSERT OR IGNORE INTO notifications VALUES (?)", (key,))

    def run(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:
                LOG.exception("Notification check failed")
            self.stop.wait(15)

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="notifications")
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=12)
