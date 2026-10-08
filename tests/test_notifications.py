from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_downloads import Base

from archive_station.notifications import Notifications


class NotificationTests(Base):
    def test_completion_persists_deduplication_and_errors_reset(self):
        job = self.add()
        send = Mock()
        notifier = Notifications(self.store, self.settings, send=send)
        row = self.store.claim()
        self.store.update(row["id"], status="error")
        notifier.tick()
        notifier.tick()
        self.assertEqual(send.call_count, 1)
        restarted = Notifications(self.store, self.settings, send=send)
        restarted.tick()
        self.assertEqual(send.call_count, 1)
        self.store.action(job, "retry")
        restarted.tick()
        self.store.update(row["id"], status="completed")
        self.store.finish_jobs()
        restarted.tick()
        self.assertEqual(send.call_args.args[0], "completed")
        self.assertEqual(send.call_count, 2)

    def test_disabled_notifications_do_not_replay_old_events(self):
        self.add()
        row = self.store.claim()
        self.store.update(row["id"], status="error")
        self.settings.update({"notifications": False})
        send = Mock()
        notifier = Notifications(self.store, self.settings, send=send)
        notifier.tick()
        self.settings.update({"notifications": True})
        notifier.tick()
        send.assert_not_called()

    def test_low_disk_deduplicates_and_delivery_failure_is_retried(self):
        self.add()
        send = Mock(side_effect=OSError("no service"))
        notifier = Notifications(self.store, self.settings, send=send)
        with patch(
            "archive_station.notifications.shutil.disk_usage", return_value=SimpleNamespace(free=1)
        ):
            with self.assertLogs("archive_station.notifications", level="ERROR"):
                notifier.tick()
            notifier.tick()
            self.assertEqual(send.call_count, 1)
            notifier.retry_at = 0
            send.side_effect = None
            notifier.tick()
            notifier.tick()
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args.args[0], "disk")
