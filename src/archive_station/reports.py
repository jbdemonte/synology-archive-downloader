"""Human-readable JSON summaries; report errors never stop download workers."""

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .archive import safe_path

LOG = logging.getLogger(__name__)


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp else None


class Reports:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.stop = threading.Event()
        self.thread = None
        self.written = {}

    def start(self):
        self.thread = threading.Thread(target=self.run, name="download-reports", daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            self.update()
            self.stop.wait(15)

    def shutdown(self):
        self.stop.set()
        if self.thread:
            self.thread.join()
        self.update()

    def update(self):
        for job in self.store.jobs():
            signature = (
                job["status"],
                job["downloaded"],
                job["completed_files"],
                job["failed_files"],
            )
            if (
                job["status"] not in {"running", "queued"}
                and self.written.get(job["id"]) == signature
            ):
                continue
            try:
                self.write(job)
                self.written[job["id"]] = signature
            except (OSError, ValueError):
                LOG.warning("Could not update report for %s", job["identifier"], exc_info=True)

    def write(self, job):
        root = self.settings.directory(job["destination"])
        # Per-job UUID keeps reports separate from archived files and earlier tasks.
        name = f"ArchiveStation-report-{job['id']}.json"
        path = safe_path(root, f"{job['identifier']}/{name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict) or existing.get("job_id") != job["id"]:
                raise ValueError("An unrelated file already uses the report name")
        now = time.time()
        end = job["finished_at"] or (
            now if job["status"] in {"queued", "running", "paused"} else None
        )
        value = {
            "application": "Archive Station",
            "report_version": 1,
            "job_id": job["id"],
            "identifier": job["identifier"],
            "title": job["title"],
            "source_url": job["source_url"] or f"https://archive.org/details/{job['identifier']}",
            "download_url": f"https://archive.org/download/{job['identifier']}",
            "destination": str(path.parent),
            "status": job["status"],
            "created_at": iso(job["created"]),
            "updated_at": iso(now),
            "finished_at": iso(job["finished_at"]),
            "duration_seconds": round(max(0, end - job["created"]), 1) if end else None,
            "duration_note": "Wall-clock time since task creation, including pauses and downtime.",
            "downloaded_bytes": job["downloaded"],
            "completed_bytes": job["completed_bytes"],
            "bytes_note": (
                "Current retained bytes, including partial files; not cumulative network traffic."
            ),
            "total_known_bytes": job["total_size"],
            "unknown_size_files": job["unknown_sizes"],
            "total_files": job["file_count"],
            "completed_files": job["completed_files"],
            "failed_files": job["failed_files"],
            "selection": {"mode": job["mode"], "pattern": job["pattern"]},
            "checksum_verification_enabled": self.settings.get()["verify_checksums"],
        }
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=".archive-station-report-",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                json.dump(value, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(0o644)
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
