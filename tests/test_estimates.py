"""ETA uses recent network traffic, never retained files or a browser's lifetime."""

import unittest

from test_downloads import PAYLOAD, Base, manifest

from archive_station.estimates import Estimates
from archive_station.store import Store


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.estimates = Estimates(clock=lambda: self.now)
        self.job = dict(
            id="one",
            status="running",
            remaining_known_bytes=6000,
            unknown_sizes=0,
            failed_files=0,
        )
        self.estimates.start("one")

    def snapshot(self, **changes):
        return self.estimates.snapshot({**self.job, **changes})

    def test_warmup_parallel_transfers_and_uneven_api_polling(self):
        self.now += 10
        self.estimates.record("one", 1000)
        self.estimates.record("one", 2000)  # Another worker, same job.
        self.assertEqual(self.snapshot()["eta_state"], "measuring")
        self.assertIsNone(self.snapshot()["eta_seconds"])
        self.now += 20
        result = self.snapshot()
        self.assertEqual(result["average_speed"], 100)
        self.assertEqual(result["eta_seconds"], 60)
        # No open UI or polling is needed to accumulate a history.
        self.now += 20
        self.estimates.record("one", 2000)
        self.assertEqual(self.snapshot()["eta_seconds"], 60)

    def test_window_drops_old_bursts_and_history_is_bounded(self):
        self.estimates.record("one", 1000000)
        for _ in range(1200):
            self.now += 1
            self.estimates.record("one", 100)
        result = self.snapshot()
        self.assertEqual(result["average_window_seconds"], 300)
        self.assertAlmostEqual(result["average_speed"], 100, delta=1)
        self.assertEqual(result["eta_seconds"], 60)
        self.assertLessEqual(len(self.estimates.history["one"]["buckets"]), 301)

    def test_stall_hides_eta_then_resumes_with_idle_time_in_average(self):
        self.now += 30
        self.estimates.record("one", 3000)
        self.assertEqual(self.snapshot()["eta_seconds"], 60)
        self.now += 60
        self.assertEqual(self.snapshot()["eta_state"], "stalled")
        self.assertIsNone(self.snapshot()["eta_seconds"])
        self.estimates.record("one", 1500)
        self.assertEqual(self.snapshot()["eta_seconds"], 120)
        self.now += 400
        self.assertEqual(self.snapshot()["average_speed"], 0)

    def test_unknown_sizes_and_terminal_states_never_promise_completion(self):
        self.now += 30
        self.estimates.record("one", 3000)
        self.assertTrue(self.snapshot(unknown_sizes=1)["eta_lower_bound"])
        result = self.snapshot(remaining_known_bytes=0, unknown_sizes=1)
        self.assertEqual(result["eta_state"], "unknown")
        self.assertIsNone(result["eta_seconds"])
        self.assertEqual(self.snapshot(remaining_known_bytes=0)["eta_state"], "verifying")
        self.assertIsNone(self.snapshot(failed_files=1)["eta_seconds"])
        for status in ("completed", "paused", "cancelled", "error"):
            self.assertIsNone(self.snapshot(status=status)["eta_seconds"])

    def test_new_job_does_not_inherit_another_job_rate(self):
        self.estimates.record("one", 10000)
        self.now += 30
        self.assertEqual(self.snapshot(id="two")["eta_state"], "queued")
        self.estimates.start("two")
        self.assertEqual(self.snapshot(id="two")["average_speed"], 0)

    def test_reset_ignores_inflight_bytes_and_rewarms(self):
        self.estimates.record("one", 10000)
        self.estimates.reset("one")
        self.estimates.record("one", 10000)
        self.now += 600
        self.estimates.start("one")
        self.estimates.record("one", 500)
        self.now += 10
        result = self.snapshot()
        self.assertEqual(result["eta_state"], "measuring")
        self.assertEqual(result["average_speed"], 50)


class EstimateIntegrationTests(Base):
    def setUp(self):
        super().setUp()
        self.now = 1000.0
        self.store.estimates = Estimates(clock=lambda: self.now)

    def test_resumed_partial_counts_only_new_network_bytes(self):
        self.add()
        row = self.store.claim()
        self.partial(row, PAYLOAD[:10000])
        self.engine.transfer(row)
        self.now += 30
        result = self.store.jobs()[0]
        self.assertEqual(result["downloaded"], len(PAYLOAD))
        self.assertEqual(result["average_speed"], (len(PAYLOAD) - 10000) / 30)
        self.store.finish_jobs()
        self.assertEqual(self.store.estimates.history, {})

    def test_existing_verified_file_is_not_network_traffic(self):
        self.add()
        self.target().parent.mkdir(parents=True)
        self.target().write_bytes(PAYLOAD)
        self.engine.transfer(self.store.claim())
        self.now += 30
        self.assertEqual(self.store.jobs()[0]["average_speed"], 0)

    def test_unknown_file_bytes_do_not_reduce_known_remaining_bytes(self):
        data = manifest(data=b"a" * 1000)
        data["files"] += manifest(name="unknown.bin", size=False)["files"]
        self.store.add(data, "all", "", self.downloads)
        known = self.store.claim()
        unknown = self.store.claim()
        self.store.update(known["id"], downloaded=100)
        self.store.update(unknown["id"], downloaded=9999)
        result = self.store.jobs()[0]
        self.assertEqual(result["remaining_known_bytes"], 900)
        self.assertTrue(result["eta_lower_bound"])

    def test_pause_restart_and_resume_preserve_data_but_reset_estimate(self):
        job = self.add()
        row = self.store.claim()
        self.store.update(row["id"], downloaded=3000)
        self.store.estimates.record(job, 3000)
        self.now += 30
        self.assertIsNotNone(self.store.jobs()[0]["eta_seconds"])
        self.store.action(job, "pause")
        self.assertIsNone(self.store.jobs()[0]["eta_seconds"])
        self.store.action(job, "resume")
        self.assertEqual(self.store.jobs()[0]["eta_state"], "measuring")
        self.store.close()
        self.store = Store(self.root / "state.sqlite3")
        result = self.store.jobs()[0]
        self.assertEqual(result["downloaded"], 3000)
        self.assertIsNone(result["eta_seconds"])
        self.assertEqual(result["average_speed"], 0)


class GraphTests(unittest.TestCase):
    def test_graph_uses_fixed_windows_and_keeps_paused_history(self):
        now = [1000]
        estimates = Estimates(clock=lambda: now[0], wall_clock=lambda: now[0])
        estimates.start("one")
        estimates.record("one", 3000)
        estimates.reset("one")
        now[0] = 1030
        graph = estimates.graph()
        values = graph["values"]
        self.assertEqual(graph["window_seconds"], 3600)
        self.assertEqual(graph["period_seconds"], 30)
        self.assertEqual(len(values), 120)
        self.assertEqual(values[-1], 100)
        self.assertEqual(sum(values), 100)
        # Data older than the ETA's five-minute window stays in the graph.
        now[0] = 1400
        self.assertEqual(sum(estimates.graph()["values"]), 100)
        now[0] = 4620
        self.assertEqual(sum(estimates.graph()["values"]), 0)

    def test_graph_averages_only_the_last_hour_and_eta_stays_on_five_minutes(self):
        now = [0]
        estimates = Estimates(clock=lambda: now[0], wall_clock=lambda: now[0])
        estimates.start("one")
        for second in range(7200):
            now[0] = second
            estimates.record("one", 9000 if second < 3600 else 3000)
        now[0] = 7200
        self.assertEqual(estimates.graph()["values"], [3000] * 120)
        job = dict(
            id="one", status="running", remaining_known_bytes=6000, unknown_sizes=0, failed_files=0
        )
        self.assertEqual(estimates.snapshot(job)["average_window_seconds"], 300)
        self.assertLessEqual(len(estimates.history["one"]["buckets"]), 301)

    def test_graph_memory_is_bounded_without_browser_polling(self):
        now = [0]
        estimates = Estimates(clock=lambda: now[0], wall_clock=lambda: now[0])
        estimates.start("one")
        for second in range(10000):
            now[0] = second
            estimates.record("one", 1000)
        self.assertLessEqual(len(estimates.traffic.buckets), 2881)
