import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from archive_station.estimates import Estimates
from archive_station.history import TrafficHistory


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "traffic.json"
        self.now = 1_800_000_000
        self.history = TrafficHistory(self.path, clock=lambda: self.now)

    def restarted(self):
        return TrafficHistory(self.path, clock=lambda: self.now)

    def test_graph_survives_restart_and_downtime_does_not_inflate_traffic(self):
        self.history.record(9000)
        self.history.record(3000)
        self.history.save()
        self.now += 60
        graph = self.restarted().graph()
        self.assertEqual(graph["values"][-2:], [400, 0])
        self.assertEqual(sum(graph["values"]) * graph["period_seconds"], 12000)
        self.now += 7200
        self.assertEqual(sum(self.restarted().graph()["values"]), 0)
        wide = self.restarted().graph(21600)
        self.assertEqual(sum(wide["values"]) * wide["period_seconds"], 12000)

    def test_every_window_has_120_complete_intervals_and_preserves_byte_totals(self):
        # One day of traffic without any browser requesting a graph.
        for _ in range(2880):
            self.history.record(3000)
            self.now += 30
        self.history.record(999999)  # Current interval is not complete yet.
        for window, period in [(3600, 30), (21600, 180), (43200, 360), (86400, 720)]:
            graph = self.history.graph(window)
            self.assertEqual(graph["values"], [100] * 120)
            self.assertEqual(graph["period_seconds"], period)
            self.assertEqual(graph["window_seconds"], window)
            self.assertEqual(graph["end_time"], self.now)
        for invalid in [True, "3600", 0, -1, 2**63, 3600.0]:
            with self.assertRaises(ValueError):
                self.history.graph(invalid)

    def test_retention_and_file_size_are_bounded_without_polling(self):
        for _ in range(6000):
            self.history.record(1000)
            self.now += 30
        self.assertLessEqual(len(self.history.buckets), 2881)
        self.history.save()
        self.assertLess(self.path.stat().st_size, 256000)
        self.now += 86460
        restored = self.restarted()
        self.assertEqual(sum(restored.graph(86400)["values"]), 0)
        self.assertEqual(len(restored.buckets), 0)

    def test_checkpoint_is_atomic_and_retryable_without_blocking_recording(self):
        self.history.record(3000)
        self.history.save()
        previous = self.path.read_bytes()
        self.history.record(3000)
        with patch("archive_station.history.os.fsync", side_effect=OSError("disk error")):
            with self.assertLogs("archive_station.history", level="WARNING"):
                self.history.save()
        self.assertEqual(self.path.read_bytes(), previous)
        self.history.save()
        self.now += 30
        self.assertEqual(self.restarted().graph()["values"][-1], 200)
        with patch.object(Path, "open", side_effect=AssertionError("Idle disk write")):
            self.history.save()

    def test_corrupt_cache_is_ignored_and_partial_checkpoint_does_not_replace_it(self):
        for content in [
            "{incomplete",
            json.dumps({"version": 1, "buckets": [[1, 3000]]}),
            json.dumps({"version": 1, "buckets": [[self.now, -1]]}),
            json.dumps({"version": 1, "buckets": [[self.now, 3000], [self.now, 1]]}),
            json.dumps({"version": 2, "buckets": []}),
            "x" * 256001,
        ]:
            self.path.write_text(content)
            with self.assertLogs("archive_station.history", level="WARNING"):
                self.assertEqual(len(self.restarted().buckets), 0)
        self.path.unlink()
        self.history.record(3000)
        self.history.save()
        self.path.with_suffix(".json.tmp").write_text("unfinished")
        self.now += 30
        self.assertEqual(self.restarted().graph()["values"][-1], 100)

    def test_wall_clock_corrections_keep_order_and_eta_uses_its_own_clock(self):
        monotonic = [1000]
        estimates = Estimates(
            clock=lambda: monotonic[0],
            history_path=self.path,
            wall_clock=lambda: self.now,
        )
        estimates.start("one")
        estimates.record("one", 3000)
        self.now -= 3600
        monotonic[0] += 30
        estimates.record("one", 3000)
        result = estimates.snapshot(
            dict(
                id="one",
                status="running",
                remaining_known_bytes=6000,
                unknown_sizes=0,
                failed_files=0,
            )
        )
        self.assertEqual(result["average_speed"], 200)
        self.now += 30
        self.assertEqual(sum(estimates.graph()["values"]), 100)
        self.assertEqual(len(estimates.traffic.buckets), 1)

    def test_periodic_and_final_save_work_without_browser_requests(self):
        self.history.SAVE_INTERVAL = 0.01
        self.history.start()
        try:
            self.history.record(3000)
            deadline = time.monotonic() + 2
            while not self.path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(self.path.exists())
            self.history.record(3000)
            self.assertTrue(self.history.shutdown())
            self.now += 30
            self.assertEqual(self.restarted().graph()["values"][-1], 200)
        finally:
            self.history.shutdown()

    def test_stuck_checkpoint_keeps_recording_responsive_and_shutdown_bounded(self):
        entered, release = threading.Event(), threading.Event()
        original = Path.replace

        def slow_replace(path, target):
            entered.set()
            release.wait(3)
            return original(path, target)

        self.history.SAVE_INTERVAL = 0.01
        self.history.record(3000)
        with patch.object(Path, "replace", slow_replace):
            self.history.start()
            try:
                self.assertTrue(entered.wait(2))
                started = time.monotonic()
                self.history.record(3000)
                self.assertEqual(len(self.history.graph()["values"]), 120)
                self.assertFalse(self.history.shutdown(timeout=0.02))
                self.assertLess(time.monotonic() - started, 0.5)
            finally:
                release.set()
                self.history.thread.join(2)
        self.now += 30
        self.assertEqual(self.restarted().graph()["values"][-1], 200)
