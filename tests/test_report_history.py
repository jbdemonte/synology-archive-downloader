import io
import json
import time
from urllib.error import HTTPError

from test_downloads import Base

from archive_station.reports import Reports
from archive_station.store import Store
from archive_station.web import WebApp


class ReportHistoryTests(Base):
    def test_errors_survive_retry_completion_restart_and_repair(self):
        job = self.add()
        row = self.store.claim()
        for attempt in (1, 2):
            self.store.update(row["id"], status="error", attempts=attempt, error="Connection reset")
        self.store.action(job, "retry")
        self.engine.transfer(self.store.claim())
        self.store.finish_jobs()
        report = Reports(self.store, self.settings)
        report.update()
        history = self.store.error_history(job)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["occurrences"], 2)
        self.assertIsNotNone(history[0]["resolved_at"])
        self.assertIsNone(self.store.files(job)["files"][0]["error"])
        self.settings.update({"language": "en"})
        report.update()
        text = (self.downloads / "test-item" / f"ArchiveStation-report-{job}.txt").read_text()
        self.assertIn("Connection reset", text)
        self.assertIn("Outcome                  : Resolved", text)
        self.store.close()
        self.store = Store(self.root / "state.sqlite3")
        self.assertEqual(self.store.jobs()[0]["incident_count"], 2)
        self.assertEqual(self.store.jobs()[0]["unresolved_incidents"], 0)
        self.store.action(job, "repair")
        self.assertEqual(self.store.error_history(job)[0]["occurrences"], 2)
        self.store.update(row["id"], status="error", attempts=1, error="Connection reset")
        self.assertEqual(self.store.jobs()[0]["incident_count"], 3)
        self.assertIsNone(self.store.error_history(job)[0]["resolved_at"])

    def test_worker_records_real_http_failure_and_keeps_terminal_error(self):
        job = self.add()
        self.client.open = lambda *args: (_ for _ in ()).throw(
            HTTPError("https://archive.org", 403, "Forbidden", {}, None)
        )
        self.engine.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not self.store.jobs()[0]["incident_count"]:
            time.sleep(0.01)
        history = self.store.error_history(job)
        self.assertEqual(len(history), 1)
        self.assertIn("autorisation", history[0]["message"])
        self.assertIsNone(history[0]["resolved_at"])
        self.assertEqual(history[0]["attempt"], 1)

    def test_legacy_error_import_is_idempotent_without_invented_dates(self):
        job = self.add()
        row = self.store.claim()
        with self.store.db:
            self.store.db.execute(
                "UPDATE files SET status='error',error='Old failure' WHERE id=?", (row["id"],)
            )
        for _ in range(2):
            self.store.close()
            self.store = Store(self.root / "state.sqlite3")
        history = self.store.error_history(job)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["occurrences"], 1)
        self.assertIsNone(history[0]["first_at"])
        self.assertIsNone(history[0]["last_at"])

    def test_low_space_incident_is_not_resolved_by_an_inflight_completion(self):
        job = self.add()
        row = self.store.claim()
        self.store.pause_for_space(job)
        self.store.pause_for_space(job)
        self.store.update(row["id"], status="completed")
        self.assertEqual(self.store.jobs()[0]["incident_count"], 1)
        self.assertEqual(self.store.jobs()[0]["unresolved_incidents"], 1)
        self.store.action(job, "resume")
        self.store.update(row["id"], status="completed")
        self.assertEqual(self.store.jobs()[0]["unresolved_incidents"], 0)

    def test_authenticated_report_reader_pages_all_incidents_as_plain_text(self):
        job = self.add()
        row = self.store.claim()
        for i in range(60):
            self.store.update(
                row["id"], status="error", error=f"Failure {i:02} <script>example</script>"
            )
        app = WebApp(self.store, self.client, self.settings, self.root / "data")

        def request(job_id, offset=0):
            result = {}

            def start(status, headers):
                result["status"] = int(status.split()[0])

            result["body"] = json.loads(
                b"".join(
                    app(
                        {
                            "REQUEST_METHOD": "GET",
                            "PATH_INFO": f"/api/jobs/{job_id}/report",
                            "QUERY_STRING": f"offset={offset}",
                            "wsgi.input": io.BytesIO(),
                            "REMOTE_ADDR": "127.0.0.1",
                        },
                        start,
                    )
                )
            )
            return result

        self.assertEqual(request(job)["status"], 401)
        app.no_auth = True
        self.assertEqual(request("missing")["status"], 404)
        first = request(job)["body"]
        self.assertGreater(first["total"], 200)
        contents = [
            request(job, offset)["body"]["content"] for offset in range(0, first["total"], 200)
        ]
        combined = "\n".join(contents)
        for i in range(60):
            self.assertIn(f"Failure {i:02} <script>example</script>", combined)
        self.assertIn("ERROR HISTORY", combined)
        self.assertEqual(first["filename"], f"ArchiveStation-report-{job}.txt")
        self.assertEqual(len(combined.splitlines()), first["total"])
