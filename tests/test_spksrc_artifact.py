"""Check the built artifact: ARCHIVE_STATION_SPK=... python3 -m unittest discover -s tests."""

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(
    os.environ.get("ARCHIVE_STATION_SPK"),
    "Set ARCHIVE_STATION_SPK to test a built community package",
)
class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tarfile.open(os.environ["ARCHIVE_STATION_SPK"]) as archive:
            cls.package = {p.name: archive.extractfile(p).read() for p in archive if p.isfile()}
        with tarfile.open(fileobj=io.BytesIO(cls.package["package.tgz"])) as archive:
            cls.payload = {
                p.name.removeprefix("./"): archive.extractfile(p).read()
                for p in archive
                if p.isfile()
            }
            cls.modes = {p.name.removeprefix("./"): p.mode for p in archive}

    def test_package_identity_dependency_and_conflict(self):
        info = self.package["INFO"].decode()
        for value in (
            'package="archivestation"',
            'arch="noarch"',
            'install_dep_packages="python312"',
            'install_conflict_packages="ArchiveStation"',
        ):
            self.assertIn(value, info)
        privilege = json.loads(self.package["conf/privilege"])
        self.assertEqual(privilege["defaults"]["run-as"], "package")
        self.assertEqual(privilege["username"], "sc-archivestation")
        resource = json.loads(self.package["conf/resource"])
        self.assertNotIn("port-config", resource)
        self.assertEqual(
            resource["data-share"]["shares"][0]["permission"]["rw"], ["sc-archivestation"]
        )

    def test_dsm_gateway_and_styles_are_scoped(self):
        config = json.loads(self.payload["ui/config"])[".url"]["com.archivestation.app"]
        self.assertFalse(config["allUsers"])
        self.assertIn("/3rdparty/archivestation/web/index.html?v=1.0.1-2", config["url"])
        gateway = self.payload["ui/gateway.cgi"].decode()
        self.assertTrue(
            gateway.startswith("#!/var/packages/archivestation/target/env/bin/python3\n")
        )
        self.assertTrue(self.modes["ui/gateway.cgi"] & 0o111)
        self.assertIn("127.0.0.1:8274", gateway)
        self.assertNotIn(b"{", self.payload["ui/style.css"])
        self.assertNotIn(b"/3rdparty/ArchiveStation/", self.payload["ui/web/app.js"])

    def test_permissions_and_notification_identity(self):
        self.assertIn(b'id="service-user"', self.payload["ui/web/index.html"])
        catalogs = [
            p for p in self.payload if p.startswith("ui/web/locales/") and p.endswith(".json")
        ]
        self.assertEqual(len(catalogs), 27)
        for p in catalogs:
            catalog = json.loads(self.payload[p])
            messages = [value for key, value in catalog.items() if "{account}" in key]
            self.assertTrue(messages, p)
            self.assertTrue(all("{account}" in value for value in messages), p)
        script = self.package["scripts/start-stop-status"]
        self.assertIn(b"ARCHIVE_STATION_PACKAGE_ID=archivestation", script)
        self.assertIn(b"ARCHIVE_STATION_SERVICE_USER=sc-archivestation", script)
        source = Path(__file__).resolve().parents[1] / "src/archive_station"
        for name in ("updates.py", "notifications.py", "static/app.js", "static/style.css"):
            self.assertEqual(
                self.payload["app/archive_station/" + name], (source / name).read_bytes()
            )
        self.assertEqual(self.payload["ui/web/app.js"], (source / "static/app.js").read_bytes())
        self.assertEqual(
            self.payload["ui/web/style.css"], (source / "static/style.css").read_bytes()
        )

    def test_no_interpreter_is_embedded_and_waitress_is_pinned(self):
        self.assertFalse(any(p.startswith(("python/", "env/")) for p in self.payload))
        self.assertEqual(
            self.payload["share/wheelhouse/requirements.txt"].strip(), b"waitress==3.0.2"
        )
        self.assertIn(
            b"ARCHIVE_STATION_PACKAGE_CENTER=1", self.package["scripts/start-stop-status"]
        )

    def test_package_center_mode_never_queries_github_even_manually(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "archive_station"
            app.mkdir()
            for name in ("__init__.py", "VERSION", "updates.py"):
                (app / name).write_bytes(self.payload[f"app/archive_station/{name}"])
            # A leftover upstream cache must not advertise its different package.
            (root / "updates.json").write_text(
                json.dumps(
                    {
                        "state": "ok",
                        "checked_at": 1,
                        "include_prereleases": False,
                        "release": {"tag": "v99.0.0-1", "prerelease": False},
                    }
                )
            )
            code = """
from pathlib import Path
from types import SimpleNamespace
from archive_station.updates import Updates
def forbidden():
    raise AssertionError("GitHub must not be queried")
u = Updates(SimpleNamespace(get=lambda: {"check_updates": True}), Path.cwd(),
            fetch=forbidden, enabled=False)
u.start()
u.tick()
assert u.request()["state"] == "disabled"
assert u.snapshot()["release_url"] is None
assert u.worker is None and u.scheduler is None
u.shutdown()
"""
            subprocess.run(
                [sys.executable, "-c", code],
                cwd=root,
                env={**os.environ, "PYTHONPATH": str(root), "ARCHIVE_STATION_PACKAGE_CENTER": "1"},
                check=True,
            )


if __name__ == "__main__":
    unittest.main()
