import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from archive_station.archive import ArchiveClient, parse_identifier, safe_path, validate_name
from archive_station.config import Settings
from archive_station.engine import Conflict, Engine, Interrupted
from archive_station.store import Store
from archive_station.web import WebApp

PAYLOAD = b"archive-test-payload" * 20000


def manifest(identifier="test-item", name="folder/game.zip", data=PAYLOAD, size=True):
    return {
        "identifier": identifier,
        "title": "Test archive",
        "files": [
            {
                "name": name,
                "size": len(data) if size else None,
                "algorithm": "sha1",
                "digest": hashlib.sha1(data).hexdigest(),
            }
        ],
    }


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers if headers is not None else {"Content-Length": str(len(data))}


class Client:
    def __init__(self, data=PAYLOAD, ranges=True):
        self.data, self.ranges, self.offsets = data, ranges, []

    def open(self, identifier, name, offset=0):
        self.offsets.append(offset)
        if offset and self.ranges:
            return Response(
                self.data[offset:],
                206,
                {"Content-Range": f"bytes {offset}-{len(self.data) - 1}/{len(self.data)}"},
            )
        return Response(self.data)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.downloads = self.root / "downloads"
        self.settings = Settings(self.root / "data", self.downloads, [self.root])
        self.store = Store(self.root / "state.sqlite3")
        self.client = Client()
        self.engine = Engine(self.store, self.client, self.settings)

    def tearDown(self):
        self.engine.shutdown()
        self.store.close()
        self.temp.cleanup()

    def add(self, **kwargs):
        return self.store.add(manifest(**kwargs), "all", "", self.downloads)

    def target(self):
        return self.downloads / "test-item/folder/game.zip"

    def partial(self, row, data):
        path = self.downloads / f".archive-station-parts/{row['job_id']}/{row['id']}.part"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class DownloadTests(Base):
    def test_full_transfer_and_nested_folder(self):
        self.add()
        self.engine.transfer(self.store.claim())
        self.store.finish_jobs()
        self.assertEqual(self.target().read_bytes(), PAYLOAD)
        self.assertEqual(self.store.jobs()[0]["status"], "completed")

    def test_resume_from_partial_bytes(self):
        self.add()
        row = self.store.claim()
        self.partial(row, PAYLOAD[:50000])
        self.engine.transfer(row)
        self.assertEqual(self.client.offsets, [50000])
        self.assertEqual(self.target().read_bytes(), PAYLOAD)

    def test_ignored_range_truncates_instead_of_appending(self):
        self.client.ranges = False
        self.add()
        row = self.store.claim()
        self.partial(row, PAYLOAD[:50000])
        self.engine.transfer(row)
        self.assertEqual(self.target().read_bytes(), PAYLOAD)

    def test_invalid_content_range_keeps_partial(self):
        self.add()
        row = self.store.claim()
        partial = self.partial(row, PAYLOAD[:50000])
        with (
            patch.object(
                self.client,
                "open",
                return_value=Response(
                    PAYLOAD[50000:],
                    206,
                    {"Content-Range": f"bytes 3-{len(PAYLOAD) - 1}/{len(PAYLOAD)}"},
                ),
            ),
            self.assertRaises(OSError),
        ):
            self.engine.transfer(row)
        self.assertEqual(partial.read_bytes(), PAYLOAD[:50000])

    def test_truncated_response_can_resume(self):
        self.add()
        row = self.store.claim()
        with (
            patch.object(
                self.client,
                "open",
                return_value=Response(
                    PAYLOAD[:100000], headers={"Content-Length": str(len(PAYLOAD))}
                ),
            ),
            self.assertRaises(OSError),
        ):
            self.engine.transfer(row)
        self.engine.transfer(row)
        self.assertEqual(self.client.offsets[-1], 100000)
        self.assertEqual(self.target().read_bytes(), PAYLOAD)

    def test_existing_matching_file_is_not_downloaded(self):
        self.add()
        self.target().parent.mkdir(parents=True)
        self.target().write_bytes(PAYLOAD)
        self.engine.transfer(self.store.claim())
        self.assertEqual(self.client.offsets, [])

    def test_existing_conflicting_file_is_preserved(self):
        self.add()
        self.target().parent.mkdir(parents=True)
        self.target().write_bytes(b"my own data")
        with self.assertRaises(Conflict):
            self.engine.transfer(self.store.claim())
        self.assertEqual(self.target().read_bytes(), b"my own data")

    def test_checksum_mismatch_deletes_only_partial(self):
        self.client.data = b"x" * len(PAYLOAD)
        self.add()
        row = self.store.claim()
        with self.assertRaises(OSError):
            self.engine.transfer(row)
        self.assertFalse(self.target().exists())
        self.assertFalse(list(self.downloads.rglob("*.part")))

    def test_complete_partial_publishes_without_request(self):
        self.add()
        row = self.store.claim()
        self.partial(row, PAYLOAD)
        self.engine.transfer(row)
        self.assertEqual(self.client.offsets, [])
        self.assertEqual(self.target().read_bytes(), PAYLOAD)

    def test_unknown_size_transfer(self):
        self.add(size=False)
        self.engine.transfer(self.store.claim())
        self.assertEqual(self.store.jobs()[0]["unknown_sizes"], 0)

    def test_pause_preserves_partial(self):
        job = self.add()
        row = self.store.claim()
        partial = self.partial(row, PAYLOAD[:100])
        self.store.action(job, "pause")
        with self.assertRaises(Interrupted):
            self.engine.transfer(row)
        self.assertEqual(partial.read_bytes(), PAYLOAD[:100])

    def test_cancel_mid_transfer_preserves_partial_and_can_resume(self):
        job = self.add()
        row = self.store.claim()
        store = self.store

        class CancelAfterChunk(Response):
            def read(self, size):
                data = super().read(size)
                store.action(job, "cancel")
                return data

        with patch.object(self.client, "open", return_value=CancelAfterChunk(PAYLOAD)):
            with self.assertRaises(Interrupted):
                self.engine.transfer(row)
        self.assertEqual(self.store.jobs()[0]["status"], "cancelled")
        self.assertIsNone(self.store.claim())
        self.assertFalse(self.target().exists())
        self.assertTrue(list(self.downloads.rglob("*.part")))
        self.store.action(job, "resume")
        self.engine.transfer(row)
        self.store.finish_jobs()
        self.assertEqual(self.target().read_bytes(), PAYLOAD)
        self.assertEqual(self.store.jobs()[0]["status"], "completed")

    def test_zero_byte_file(self):
        self.client.data = b""
        self.add(data=b"")
        self.engine.transfer(self.store.claim())
        self.assertEqual(self.target().read_bytes(), b"")

    def test_worker_marks_forbidden_and_retryable_errors(self):
        self.add()
        self.client.open = lambda *args: (_ for _ in ()).throw(
            HTTPError("https://archive.org", 403, "Forbidden", {}, None)
        )
        self.engine.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and self.store.jobs()[0]["status"] != "error":
            time.sleep(0.02)
        self.assertEqual(self.store.jobs()[0]["failed_files"], 1)
        row = self.store.files(self.store.jobs()[0]["id"])["files"][0]
        self.assertIn("autorisation", row["error"])
        self.assertEqual(row["attempts"], 1)


class StateTests(Base):
    def test_restart_recovers_in_progress_but_keeps_paused_jobs(self):
        first = self.add()
        second = self.add(identifier="other-item")
        self.store.claim()
        self.store.action(second, "pause")
        self.store.close()
        self.store = Store(self.root / "state.sqlite3")
        jobs = {j["id"]: j["status"] for j in self.store.jobs()}
        self.assertEqual(jobs, {first: "queued", second: "paused"})
        self.assertEqual(self.store.claim()["job_id"], first)

    def test_duplicate_url_cannot_claim_same_destination(self):
        self.add()
        with self.assertRaises(ValueError):
            self.add()
        self.assertEqual(len(self.store.jobs()), 1)

    def test_remove_preserves_downloaded_data(self):
        job = self.add()
        self.engine.transfer(self.store.claim())
        self.store.finish_jobs()
        self.store.action(job, "remove")
        self.assertEqual(self.store.jobs(), [])
        self.assertEqual(self.target().read_bytes(), PAYLOAD)

    def test_settings_are_persistent_and_do_not_move_existing_jobs(self):
        self.add()
        new = self.root / "new-destination"
        self.settings.update({"download_dir": str(new), "connections": 5})
        self.assertEqual(self.store.jobs()[0]["destination"], str(self.downloads))
        restored = Settings(self.root / "data", self.downloads, [self.root])
        self.assertEqual(restored.get()["connections"], 5)
        with self.assertRaises(ValueError):
            restored.update({"connections": 100})

    def test_folder_picker_lists_locked_shares_and_reports_permissions(self):
        locked = self.root / "Download"
        readonly = self.root / "Roms"
        for folder in [locked, readonly, self.root / "@system", self.root / ".hidden"]:
            folder.mkdir()
        (self.root / "link").symlink_to(locked)
        (self.root / "file.txt").write_text("not a directory")
        actual_access = os.access

        def access(path, mode):
            if Path(path) == locked:
                return False
            if Path(path) == readonly:
                return not mode & os.W_OK
            return actual_access(path, mode)

        with patch("archive_station.config.os.access", side_effect=access):
            folders = {f["name"]: f for f in self.settings.folders(str(self.root))["folders"]}
            self.assertIn("Download", folders)
            self.assertFalse(folders["Download"]["readable"])
            self.assertFalse(folders["Download"]["writable"])
            self.assertTrue(folders["Roms"]["readable"])
            self.assertFalse(folders["Roms"]["writable"])
            self.assertFalse(self.settings.folders(str(readonly))["writable"])
            for name in ["@system", ".hidden", "link", "file.txt"]:
                self.assertNotIn(name, folders)

    def test_create_folder_preserves_existing_content_and_checks_paths(self):
        created = self.settings.create_folder(str(self.root), "Mes archives")
        target = Path(created["path"])
        self.assertTrue(target.is_dir())
        self.assertEqual(target, self.root / "Mes archives")
        marker = target / "keep.txt"
        marker.write_text("preserve")
        with self.assertRaises(ValueError):
            self.settings.create_folder(str(self.root), "Mes archives")
        self.assertEqual(marker.read_text(), "preserve")
        for name in [
            "",
            "..",
            "../escape",
            "a/b",
            "a\\b",
            ".hidden",
            "@system",
            " bad ",
            "x\x00",
            "é" * 128,
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.settings.create_folder(str(self.root), name)
        with self.assertRaises(ValueError):
            self.settings.create_folder(str(self.root.parent), "outside")
        (self.root / "link").symlink_to(target)
        with self.assertRaises(ValueError):
            self.settings.create_folder(str(self.root / "link"), "child")

    def test_create_folder_refuses_read_only_parent(self):
        with patch("archive_station.config.os.access", return_value=False):
            with self.assertRaises(PermissionError):
                self.settings.create_folder(str(self.root), "forbidden")
        self.assertFalse((self.root / "forbidden").exists())

    def test_large_activity_prioritizes_live_files_and_real_queue_order(self):
        data = manifest()
        data["files"] = [
            {**data["files"][0], "name": f"nested/roms/game-{42000 - i:05}.zip"}
            for i in range(42000)
        ]
        job = self.store.add(data, "all", "", self.downloads)
        with self.store.db:
            self.store.db.execute("UPDATE files SET status='completed' WHERE id<=40000")
            self.store.db.execute("UPDATE files SET status='downloading' WHERE id IN (41998,41999)")
            self.store.db.execute("UPDATE files SET status='error',error='Denied' WHERE id=40001")
            self.store.db.execute(
                "UPDATE files SET available_at=? WHERE id=40002", (time.time() + 3600,)
            )
        activity = self.store.activity(job)
        self.assertEqual([row["id"] for row in activity["active"]], [41998, 41999])
        self.assertEqual(
            activity["counts"], {"completed": 40000, "downloading": 2, "error": 1, "queued": 1997}
        )
        self.assertEqual(len(activity["queued"]), 10)
        self.assertEqual(activity["queued"][0]["id"], self.store.claim()["id"])
        self.assertEqual(activity["queued"][0]["id"], 40003)
        self.assertEqual(activity["errors"][0]["error"], "Denied")
        self.assertLess(len(json.dumps(activity)), 10000)
        self.store.update(41998, status="completed")
        after = self.store.activity(job)
        self.assertEqual(after["counts"]["completed"], 40001)
        self.assertEqual([row["id"] for row in after["active"]], [40003, 41999])
        completed = self.store.files(job, offset=99999, status="completed")
        self.assertEqual(completed["total"], 40001)
        self.assertEqual(completed["offset"], 40000)
        self.assertTrue(all(row["status"] == "completed" for row in completed["files"]))
        with self.assertRaises(KeyError):
            self.store.activity("missing")

    def test_activity_keeps_paused_queue_and_orders_delayed_retries(self):
        data = manifest()
        data["files"] = [{**data["files"][0], "name": f"game-{i}.zip"} for i in range(3)]
        job = self.store.add(data, "all", "", self.downloads, paused=True)
        self.store.update(1, available_at=time.time() + 200)
        self.store.update(2, available_at=time.time() + 100)
        self.assertEqual([row["id"] for row in self.store.activity(job)["queued"]], [3, 2, 1])
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.jobs()[0]["status"], "paused")
        self.store.update(2, status="error")
        self.assertEqual(self.store.activity(job)["counts"]["error"], 1)
        self.assertEqual(len(self.store.files(job, status="error")["files"]), 1)

    def test_tree_aggregates_folders_and_paginates(self):
        data = manifest()
        data["files"] = [
            {**data["files"][0], "name": f"roms/sub/game-{i:05}.zip"} for i in range(1001)
        ]
        job = self.store.add(data, "all", "", self.downloads)
        tree = self.store.tree(job)
        self.assertEqual(tree["total"], 1)
        self.assertEqual(tree["children"][0]["file_count"], 1001)
        leaf = self.store.tree(job, "roms/sub", offset=100, limit=50)
        self.assertEqual(leaf["total"], 1001)
        self.assertEqual(len(leaf["children"]), 50)
        self.assertEqual(leaf["children"][0]["name"], "game-00100.zip")


class ValidationTests(Base):
    def test_archive_urls(self):
        for value in [
            "test-item",
            "https://archive.org/details/test-item",
            "https://archive.org/download/test-item/folder/a.zip",
        ]:
            self.assertEqual(parse_identifier(value), "test-item")
        for value in [
            "https://evil.test/download/id",
            "https://archive.org.evil.test/download/id",
            "https://archive.org/download/%2E%2E",
            "../a",
            "https://user@archive.org/download/id",
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_identifier(value)

    def test_path_traversal_and_symlinks(self):
        for value in ["../bad", "/absolute", "a/../b", "a//b", "a\\b", "a\x00b"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_name(value)
        (self.root / "link").symlink_to(self.root / "outside")
        with self.assertRaises(ValueError):
            safe_path(self.root, "link/payload")
        with self.assertRaises(ValueError):
            self.settings.directory("/etc")

    def test_metadata_filter_private_and_summation(self):
        client = ArchiveClient()
        payload = {
            "metadata": {"title": "Item"},
            "files": [
                {"name": "a.zip", "size": "2", "source": "original", "sha1": "a" * 40},
                {"name": "b.zip", "size": "3", "private": "true"},
                {"name": "meta.xml", "source": "original", "summation": "md5", "md5": "b" * 32},
                {"name": "thumb.jpg", "source": "derivative", "size": "5"},
            ],
        }
        client.cache["test"] = (time.monotonic(), payload)
        all_files = client.manifest("test")
        self.assertEqual(all_files["private_files"], 1)
        self.assertEqual(len(all_files["files"]), 3)
        self.assertIsNone(all_files["files"][1]["digest"])
        self.assertEqual(len(client.manifest("test", "original", "*.zip")["files"]), 1)


class ApiTests(Base):
    def setUp(self):
        super().setUp()
        self.app = WebApp(self.store, self.client, self.settings, self.root / "data")
        self.app.auth.set_password("test-password-1234")

    def request(
        self,
        path,
        body=None,
        cookie="",
        origin="http://localhost:8274",
        content_type="application/json",
        extra_env=None,
    ):
        env = {
            "REQUEST_METHOD": "GET" if body is None else "POST",
            "PATH_INFO": path,
            "HTTP_HOST": "localhost:8274",
            "HTTP_ORIGIN": origin,
            "HTTP_COOKIE": cookie,
            "CONTENT_TYPE": content_type,
            "REMOTE_ADDR": "127.0.0.1",
        }
        raw = json.dumps(body).encode() if body is not None else b""
        env.update({"CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)})
        env.update(extra_env or {})
        result = {}

        def start(status, headers):
            result.update(status=int(status.split()[0]), headers=dict(headers))

        result["body"] = json.loads(b"".join(self.app(env, start)))
        return result

    def test_auth_session_and_csrf(self):
        self.assertEqual(self.request("/api/jobs")["status"], 401)
        login = self.request("/api/login", {"password": "test-password-1234"})
        cookie = login["headers"]["Set-Cookie"].split(";")[0]
        self.assertEqual(self.request("/api/jobs", cookie=cookie)["status"], 200)
        self.assertEqual(
            self.request("/api/settings", {}, cookie, "https://evil.test")["status"], 403
        )
        self.assertEqual(
            self.request("/api/settings", {}, cookie, content_type="text/plain")["status"], 415
        )
        self.request("/api/logout", {}, cookie)
        self.assertEqual(self.request("/api/jobs", cookie=cookie)["status"], 401)

    def test_no_mutation_from_get(self):
        self.app.no_auth = True
        job = self.add()
        self.assertEqual(self.request(f"/api/jobs/{job}/pause")["status"], 404)
        self.assertEqual(self.store.jobs()[0]["status"], "queued")

    def test_dsm_requires_local_gateway_and_disables_password_routes(self):
        self.app.dsm_auth = True
        self.assertEqual(self.request("/api/auth")["body"]["mode"], "dsm")
        self.assertEqual(self.request("/api/jobs")["status"], 401)
        marker = {"HTTP_X_ARCHIVE_STATION_DSM_AUTHENTICATED": "1"}
        self.assertEqual(self.request("/api/jobs", extra_env=marker)["status"], 200)
        remote = {**marker, "REMOTE_ADDR": "192.168.0.20"}
        self.assertEqual(self.request("/api/jobs", extra_env=remote)["status"], 401)
        for path in ["/api/login", "/api/logout", "/api/password"]:
            self.assertEqual(self.request(path, {}, extra_env=marker)["status"], 403)
        self.assertEqual(
            self.request("/api/settings", {}, origin="https://other.test", extra_env=marker)[
                "status"
            ],
            403,
        )

    def test_invalid_json_shape(self):
        self.app.no_auth = True
        self.assertEqual(self.request("/api/settings", [1, 2])["status"], 400)

    def test_folder_creation_requires_authenticated_same_origin_request(self):
        body = {"parent": str(self.root), "name": "Archives"}
        self.assertEqual(self.request("/api/folders", body)["status"], 401)
        self.assertFalse((self.root / "Archives").exists())
        self.app.no_auth = True
        self.assertEqual(
            self.request("/api/folders", body, origin="https://evil.test")["status"], 403
        )
        result = self.request("/api/folders", body)
        self.assertEqual(result["status"], 201)
        self.assertTrue(Path(result["body"]["path"]).is_dir())


if __name__ == "__main__":
    unittest.main()
