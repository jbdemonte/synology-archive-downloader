import os
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from test_downloads import Base

from archive_station.reports import Reports


class ShutdownTests(Base):
    def test_reports_bound_final_write_and_do_not_write_concurrently(self):
        reports = Reports(self.store, self.settings)
        entered, release = threading.Event(), threading.Event()
        calls = []

        def update():
            calls.append(1)
            entered.set()
            release.wait(5)

        with patch.object(reports, "update", update):
            reports.start()
            self.assertTrue(entered.wait(2))
            try:
                started = time.monotonic()
                self.assertFalse(reports.shutdown(timeout=0.02))
                self.assertLess(time.monotonic() - started, 1)
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
                reports.thread.join(2)
            self.assertFalse(reports.thread.is_alive())
            self.assertEqual(len(calls), 2)

    def test_service_stop_kills_only_its_stuck_process(self):
        ready = self.root / "ready"
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import signal,sys,time; from pathlib import Path; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                'Path(sys.argv[2], "ready").touch(); time.sleep(120)',
                "archive_station",
                str(self.root),
            ]
        )
        try:
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists())
            (self.root / "run.pid").write_text(str(child.pid))
            tools = self.root / "bin"
            tools.mkdir()
            # Accelerate only the shell's waiting intervals, not process checks.
            sleep = tools / "sleep"
            sleep.write_text("#!/bin/sh\nexit 0\n")
            sleep.chmod(0o755)
            result = subprocess.run(
                ["sh", "packaging/synology/scripts/start-stop-status", "stop"],
                env={
                    **os.environ,
                    "SYNOPKG_PKGVAR": str(self.root),
                    "PATH": str(tools) + os.pathsep + os.environ["PATH"],
                },
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("forcibly", result.stderr)
            self.assertEqual(child.wait(2), -signal.SIGKILL)
            self.assertFalse((self.root / "run.pid").exists())
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
