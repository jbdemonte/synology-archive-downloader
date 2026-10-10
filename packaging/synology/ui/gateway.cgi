#!@PYTHON@
"""Authenticate with DSM before proxying to the loopback-only application."""

import grp
import json
import os
import pwd
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

AUTHENTICATE = "/usr/syno/synoman/webman/modules/authenticate.cgi"
AUTHENTICATE_FALLBACK = "/usr/syno/synoman/webman/authenticate.cgi"


class DSMAuthenticationUnavailable(Exception):
    """DSM's authenticator could not run; this is not an expired session."""


def reply(status, value):
    # DSM intercepts HTTP 403/404/5xx and substitutes its HTML error page.
    # Carry API errors in JSON, as native DSM APIs do, without changing nginx.
    transport_status = 200 if status in {403, 404, 500, 502, 503, 504} else status
    if transport_status != status:
        value = {**value, "_http_status": status}
    body = json.dumps(value, ensure_ascii=True).encode()
    headers = (
        f"Status: {transport_status}\r\nContent-Type: application/json; charset=utf-8\r\n"
        "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n\r\n"
    )
    sys.stdout.buffer.write(headers.encode() + body)


def dsm_user():
    # Retain the original CGI environment: DSM validates its session cookie,
    # client address and (when enabled) the X-SYNO-TOKEN header itself.
    try:
        for executable in (AUTHENTICATE, AUTHENTICATE_FALLBACK):
            try:
                result = subprocess.run(
                    [executable],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
                break
            except FileNotFoundError:
                # If the modules/ alias is absent, try the canonical binary.
                # Never retry an authentication denial.
                if executable == AUTHENTICATE_FALLBACK:
                    raise
        user = result.stdout.decode("utf-8").strip()
        if result.returncode or not user or len(user) > 256:
            return None
        if any(ord(char) < 32 for char in user):
            return None
        return user
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DSMAuthenticationUnavailable from error
    except UnicodeError:
        return None


def is_administrator(user):
    try:
        account = pwd.getpwnam(user)
        admin = grp.getgrnam("administrators")
        return admin.gr_gid in os.getgrouplist(user, account.pw_gid)
    except (KeyError, OSError):
        return False


def main():
    route = parse_qs(os.environ.get("QUERY_STRING", "")).get("route", [""])[0]
    try:
        url = urlsplit(route)
    except ValueError:
        reply(400, {"error": "Route invalide."})
        return
    if (
        url.scheme
        or url.netloc
        or url.fragment
        or not url.path.startswith("/api/")
        or any(ord(char) < 32 for char in route)
    ):
        reply(400, {"error": "Route invalide."})
        return
    method = os.environ.get("REQUEST_METHOD", "GET")
    if method not in {"GET", "POST"}:
        reply(405, {"error": "Méthode non autorisée."})
        return
    try:
        user = dsm_user()
    except DSMAuthenticationUnavailable:
        reply(
            503,
            {
                "error": "La vérification de la session DSM est indisponible. "
                "Réessayez dans quelques instants.",
                "code": "dsm_auth_unavailable",
                "mode": "dsm",
            },
        )
        return
    if not user:
        reply(
            401,
            {
                "error": "Session DSM expirée. Reconnectez-vous à DSM, "
                "puis rouvrez Archive Station.",
                "mode": "dsm",
            },
        )
        return
    if not is_administrator(user):
        reply(
            403, {"error": "Archive Station nécessite un compte administrateur DSM.", "mode": "dsm"}
        )
        return
    try:
        length = int(os.environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        reply(400, {"error": "Taille de requête invalide."})
        return
    if not 0 <= length <= 65536:
        reply(413, {"error": "Requête trop volumineuse."})
        return
    host = os.environ.get("HTTP_HOST", "localhost")
    content_type = os.environ.get("CONTENT_TYPE", "")
    origin = os.environ.get("HTTP_ORIGIN", "")
    # Check before forwarding; a session cookie alone must never authorize a
    # cross-origin form submission. The backend repeats these checks.
    if method == "POST":
        if content_type.split(";")[0] != "application/json":
            reply(415, {"error": "Content-Type application/json requis."})
            return
        if origin and urlsplit(origin).netloc != host:
            reply(403, {"error": "Origine non autorisée."})
            return
    headers = {
        "Host": host,
        "Content-Type": content_type,
        "X-Archive-Station-DSM-Authenticated": "1",
    }
    if origin:
        headers["Origin"] = origin
    # Never forward DSM credentials, tokens or any client-provided auth marker.
    data = sys.stdin.buffer.read(length) if method == "POST" else None
    request = Request("http://127.0.0.1:8274" + route, data=data, headers=headers, method=method)
    try:
        try:
            response = urlopen(request, timeout=90)
        except HTTPError as error:
            response = error
        with response:
            body = response.read(4 * 1024 * 1024)
            try:
                value = json.loads(body)
            except (ValueError, UnicodeError):
                reply(
                    502,
                    {"error": "Réponse du service indisponible. Réessayez dans quelques instants."},
                )
                return
            reply(response.status, value)
    except (URLError, TimeoutError, OSError):
        reply(503, {"error": "Archive Station n’est pas démarré. Ouvrez le Centre de paquets."})


if __name__ == "__main__":
    main()
