"""DSM gateway authentication, independent of NAS credentials or network access."""

import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader(
    "dsm_gateway", str(ROOT / "packaging/synology/ui/gateway.cgi")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
gateway = importlib.util.module_from_spec(spec)
loader.exec_module(gateway)


class GatewayTests(unittest.TestCase):
    def request(
        self,
        user=None,
        admin=True,
        route="/api/jobs",
        body=None,
        response_status=200,
        response_body=b'{"ok":true}',
        **extra,
    ):
        raw = json.dumps(body).encode() if body is not None else b""
        env = {
            "QUERY_STRING": urlencode({"route": route}),
            "REQUEST_METHOD": "GET" if body is None else "POST",
            "HTTP_HOST": "nas.test:5001",
            "HTTP_ORIGIN": "https://nas.test:5001",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(raw)),
            **extra,
        }
        output = io.BytesIO()
        response = io.BytesIO(response_body)
        response.status = response_status
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(gateway, "dsm_user", return_value=user),
            patch.object(gateway, "is_administrator", return_value=admin),
            patch.object(gateway.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw))),
            patch.object(gateway.sys, "stdout", SimpleNamespace(buffer=output)),
            patch.object(gateway, "urlopen", return_value=response) as proxy,
        ):
            gateway.main()
        header, data = output.getvalue().split(b"\r\n\r\n", 1)
        self.transport_status = int(header.split()[1])
        value = json.loads(data)
        return value.get("_http_status", self.transport_status), value, proxy

    def test_backend_permission_error_survives_dsm_html_interception(self):
        status, data, _ = self.request(
            user="administrator",
            response_status=403,
            response_body=b'{"error":"Access denied to folder"}',
        )
        self.assertEqual(self.transport_status, 200)
        self.assertEqual(status, 403)
        self.assertEqual(data["error"], "Access denied to folder")

    def test_invalid_backend_response_becomes_json_error(self):
        status, data, _ = self.request(
            user="administrator", response_status=500, response_body=b"<html>Internal error</html>"
        )
        self.assertEqual(self.transport_status, 200)
        self.assertEqual(status, 502)
        self.assertIn("error", data)

    def test_absent_or_forged_session_never_reaches_backend(self):
        status, _, proxy = self.request(
            HTTP_COOKIE="id=forged; archive_station_session=old-password-session",
            HTTP_X_ARCHIVE_STATION_DSM_AUTHENTICATED="1",
        )
        self.assertEqual(status, 401)
        proxy.assert_not_called()

    def test_non_administrator_never_reaches_backend(self):
        status, _, proxy = self.request(user="guest", admin=False)
        self.assertEqual(status, 403)
        proxy.assert_not_called()

    def test_authenticated_admin_proxies_without_dsm_credentials(self):
        status, _, proxy = self.request(
            user="administrator",
            body={},
            HTTP_COOKIE="id=private-session",
            HTTP_X_SYNO_TOKEN="private-token",
        )
        self.assertEqual(status, 200)
        request = proxy.call_args.args[0]
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(headers["x-archive-station-dsm-authenticated"], "1")
        self.assertNotIn("cookie", headers)
        self.assertNotIn("x-syno-token", headers)
        self.assertEqual(request.full_url, "http://127.0.0.1:8274/api/jobs")

    def test_cross_origin_and_form_submissions_rejected(self):
        for extra, expected in [
            ({"HTTP_ORIGIN": "https://evil.test"}, 403),
            ({"CONTENT_TYPE": "application/x-www-form-urlencoded"}, 415),
        ]:
            status, _, proxy = self.request(user="administrator", body={}, **extra)
            self.assertEqual(status, expected)
            proxy.assert_not_called()

    def test_invalid_route_and_content_length(self):
        for route in [
            "http://evil.test/api/jobs",
            "//evil.test/api/jobs",
            "/health",
            "/api/jobs\r\n",
        ]:
            self.assertEqual(self.request(user="administrator", route=route)[0], 400)
        self.assertEqual(self.request(user="administrator", CONTENT_LENGTH="bad")[0], 400)
        self.assertEqual(self.request(user="administrator", CONTENT_LENGTH="65537")[0], 413)

    def test_official_authenticator_fails_closed(self):
        for result in [
            SimpleNamespace(returncode=0, stdout=b""),
            SimpleNamespace(returncode=1, stdout=b"administrator"),
            SimpleNamespace(returncode=0, stdout=b"name\nsecond-line"),
        ]:
            with patch.object(gateway.subprocess, "run", return_value=result):
                self.assertIsNone(gateway.dsm_user())
        for error in [FileNotFoundError(), subprocess.TimeoutExpired("authenticate.cgi", 5)]:
            with patch.object(gateway.subprocess, "run", side_effect=error):
                self.assertIsNone(gateway.dsm_user())
        with patch.object(
            gateway.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout=b"administrator\n"),
        ) as auth:
            self.assertEqual(gateway.dsm_user(), "administrator")
            self.assertEqual(auth.call_args.args[0], [gateway.AUTHENTICATE])
            self.assertNotIn("env", auth.call_args.kwargs)  # Preserve original CGI environment.

    def test_administrator_group_required(self):
        with (
            patch.object(gateway.pwd, "getpwnam", return_value=SimpleNamespace(pw_gid=100)),
            patch.object(gateway.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=101)),
            patch.object(gateway.os, "getgrouplist", return_value=[100, 101]),
        ):
            self.assertTrue(gateway.is_administrator("administrator"))
        with patch.object(gateway.pwd, "getpwnam", side_effect=KeyError):
            self.assertFalse(gateway.is_administrator("unknown"))


if __name__ == "__main__":
    unittest.main()
