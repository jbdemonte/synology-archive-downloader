from datetime import datetime
from unittest.mock import patch

from test_downloads import Base

from archive_station.engine import Interrupted
from archive_station.schedule import policy


class ScheduleTests(Base):
    def settings_for_night(self):
        return self.settings.update(
            {
                "schedule_enabled": True,
                "schedule_days": [0],
                "schedule_start": 22 * 60,
                "schedule_end": 6 * 60,
            }
        )

    def test_overnight_and_boundaries(self):
        s = self.settings_for_night()
        for at, allowed in [
            (datetime(2026, 10, 5, 21, 59), False),
            (datetime(2026, 10, 5, 22), True),
            (datetime(2026, 10, 6, 5, 59), True),
            (datetime(2026, 10, 6, 6), False),
            (datetime(2026, 10, 6, 22), False),
        ]:
            self.assertEqual(policy(s, at)["allowed"], allowed, at)
        s.update(schedule_start=0, schedule_end=0)
        self.assertTrue(policy(s, datetime(2026, 10, 5, 12))["allowed"])
        self.assertFalse(policy(s, datetime(2026, 10, 6, 0))["allowed"])

    def test_live_policy_stops_transfer_without_changing_manual_pause(self):
        job = self.add()
        row = self.store.claim()
        self.partial(row, b"partial")
        with patch("archive_station.engine.policy", return_value={"allowed": False}):
            with self.assertRaises(Interrupted):
                self.engine.transfer(row)
        self.assertEqual(self.partial(row, b"partial").read_bytes(), b"partial")
        self.store.action(job, "pause")
        self.assertFalse(self.store.active(job))
        self.assertIsNone(self.store.claim())

    def test_alternate_limit_and_disabled_schedule(self):
        s = self.settings_for_night()
        s.update(schedule_outside="limited", schedule_limit_kib=500, speed_limit_kib=1000)
        self.assertEqual(
            policy(s, datetime(2026, 10, 5, 10)),
            {"allowed": True, "limit_kib": 500, "outside": True},
        )
        self.assertEqual(policy(s, datetime(2026, 10, 5, 23))["limit_kib"], 1000)
        s["schedule_enabled"] = False
        self.assertFalse(policy(s)["outside"])
        for change in [
            {"schedule_days": [7]},
            {"schedule_start": 1440},
            {"schedule_limit_kib": 0},
            {"schedule_days": []},
        ]:
            with self.assertRaises(ValueError):
                self.settings.update(change)
