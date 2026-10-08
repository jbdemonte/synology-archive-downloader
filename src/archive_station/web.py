"""Small same-origin JSON API and static French UI, served by Waitress."""

import json
import logging
import mimetypes
import shutil
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .archive import parse_identifier
from .auth import Auth
from .config import LANGUAGES, dsm_language
from .plans import Plans

LOG = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class WebApp:
    def __init__(self, store, client, settings, data_dir, no_auth=False, dsm_auth=False):
        self.store, self.client, self.settings = store, client, settings
        self.auth = Auth(data_dir)
        self.no_auth = no_auth
        self.dsm_auth = dsm_auth
        self.plans = Plans()

    def __call__(self, env, start_response):
        headers = []
        try:
            status, value, content_type, headers = self.dispatch(env)
        except KeyError as exc:
            status, value, content_type = 404, {"error": str(exc.args[0])}, "application/json"
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            status, value, content_type = 400, {"error": str(exc)}, "application/json"
        except PermissionError:
            status, value, content_type = (
                403,
                {
                    "error": "Accès au dossier refusé. Vérifiez les "
                    "permissions de l’utilisateur système ArchiveStation dans DSM."
                },
                "application/json",
            )
        except (URLError, TimeoutError) as exc:
            status, value, content_type = (
                502,
                {"error": f"Archive.org est indisponible : {exc}"},
                "application/json",
            )
        except Exception:
            LOG.exception("Unhandled request error")
            status, value, content_type = (
                500,
                {"error": "Erreur interne. Consultez le journal du paquet."},
                "application/json",
            )
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        headers += [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "same-origin"),
            ("X-Frame-Options", "SAMEORIGIN"),
            (
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; script-src 'self'; "
                "connect-src 'self'; frame-ancestors 'self'",
            ),
        ]
        start_response(f"{status} {HTTPStatus(status).phrase}", headers)
        return [body]

    def dispatch(self, env):
        method, path = env["REQUEST_METHOD"], env.get("PATH_INFO", "/")
        query = {key: values[0] for key, values in parse_qs(env.get("QUERY_STRING", "")).items()}
        headers = []

        def response(value, status=200):
            return (status, value, "application/json", headers)

        if path == "/health" and method == "GET":
            return response({"status": "ok", "version": __version__})
        if method == "GET" and path in {"/", "/app.js", "/i18n.js", "/style.css", "/icon.png"}:
            file = STATIC / ("index.html" if path == "/" else path[1:])
            return 200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "text/plain", []
        if method == "GET" and path in {f"/locales/{code}.json" for code in LANGUAGES - {"auto"}}:
            return 200, (STATIC / path[1:]).read_bytes(), "application/json", []
        if method not in {"GET", "POST"}:
            return response({"error": "Méthode non autorisée."}, 405)
        body = {}
        if method == "POST":
            # Requiring JSON prevents cross-origin form submissions; no CORS is enabled.
            if env.get("CONTENT_TYPE", "").split(";")[0] != "application/json":
                return response({"error": "Content-Type application/json requis."}, 415)
            origin = env.get("HTTP_ORIGIN")
            if origin and urlsplit(origin).netloc != env.get("HTTP_HOST"):
                return response({"error": "Origine non autorisée."}, 403)
            length = int(env.get("CONTENT_LENGTH") or 0)
            if length < 0 or length > 65536:
                return response({"error": "Requête trop volumineuse."}, 413)
            body = json.loads(env["wsgi.input"].read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("Un objet JSON est requis.")
        cookie = SimpleCookie()
        cookie.load(env.get("HTTP_COOKIE", ""))
        token = (
            cookie["archive_station_session"].value if "archive_station_session" in cookie else ""
        )
        authenticated = self.no_auth or self.auth.valid(token)
        if self.dsm_auth:
            authenticated = (
                env.get("REMOTE_ADDR") in {"127.0.0.1", "::1"}
                and env.get("HTTP_X_ARCHIVE_STATION_DSM_AUTHENTICATED") == "1"
            )
        if path == "/api/auth" and method == "GET":
            return response(
                {
                    "authenticated": authenticated,
                    "configured": self.dsm_auth or self.auth.path.exists(),
                    "mode": "dsm" if self.dsm_auth else "local" if self.no_auth else "password",
                }
            )
        if self.dsm_auth and path in {"/api/login", "/api/logout", "/api/password"}:
            return response({"error": "La connexion est gérée par DSM."}, 403)
        if path == "/api/login" and method == "POST":
            token = self.auth.login(body.get("password", ""), env.get("REMOTE_ADDR", ""))
            if not token:
                return response(
                    {
                        "error": "Mot de passe incorrect ou trop de tentatives. "
                        "Patientez une minute si nécessaire."
                    },
                    401,
                )
            secure = "; Secure" if env.get("wsgi.url_scheme") == "https" else ""
            headers.append(
                (
                    "Set-Cookie",
                    f"archive_station_session={token}; HttpOnly; SameSite=Strict; Path=/; "
                    f"Max-Age=86400{secure}",
                )
            )
            return response({"ok": True})
        if not authenticated:
            return response({"error": "Connexion requise."}, 401)
        if path == "/api/logout" and method == "POST":
            self.auth.logout(token)
            headers.append(
                (
                    "Set-Cookie",
                    "archive_station_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict",
                )
            )
            return response({"ok": True})
        if path == "/api/settings":
            if method == "POST":
                # Claims, task additions and resumes share this lock, so no new
                # transfer can start between the check and saving the setting.
                with self.store.lock:
                    current = self.settings.get()
                    if (
                        "download_dir" in body
                        and body["download_dir"] != current["download_dir"]
                        and self.store.destination_locked()
                    ):
                        return response(
                            {
                                "error": "Mettre les téléchargements en pause "
                                "et attendre leur arrêt pour modifier la destination par défaut."
                            },
                            409,
                        )
                    return response(self.settings.update(body))
            settings = self.settings.get()
            try:
                disk = shutil.disk_usage(settings["download_dir"])
                storage = {"free": disk.free, "total": disk.total}
            except OSError:
                storage = None
            return response(
                {
                    **settings,
                    "storage": storage,
                    "version": __version__,
                    "dsm_language": dsm_language() if self.dsm_auth else "",
                    "destination_locked": self.store.destination_locked(),
                }
            )
        if path == "/api/password" and method == "POST":
            self.auth.set_password(body.get("password"))
            return response({"ok": True})
        if path == "/api/folders" and method == "GET":
            return response(self.settings.folders(query.get("path")))
        if path == "/api/folders" and method == "POST":
            return response(self.settings.create_folder(body.get("parent"), body.get("name")), 201)
        if path in {"/api/inspect", "/api/jobs"} and method == "POST":
            identifier = parse_identifier(body.get("url"))
            mode, pattern = body.get("mode", "all"), body.get("pattern", "")
            if path == "/api/jobs" and body.get("plan_id"):
                manifest, mode, pattern = self.plans.selection(body["plan_id"], identifier)
            else:
                manifest = self.client.manifest(identifier, mode, pattern)
            if path == "/api/inspect":
                return response(
                    {k: v for k, v in manifest.items() if k != "files"}
                    | {
                        "file_count": len(manifest["files"]),
                        "sample": [f["name"] for f in manifest["files"][:5]],
                        "plan_id": self.plans.create(manifest, mode, pattern),
                    }
                )
            destination = self.settings.directory(
                body.get("destination") or self.settings.get()["download_dir"], create=True
            )
            if type(body.get("paused", False)) is not bool:
                raise ValueError("Option de démarrage invalide.")
            job_id = self.store.add(
                manifest,
                mode,
                pattern,
                destination,
                body.get("paused", False),
                source_url=body.get("url", "").strip(),
            )
            return response({"id": job_id, "identifier": identifier}, 201)
        if path == "/api/jobs" and method == "GET":
            return response({"jobs": self.store.jobs()})
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "plans"]:
            if method == "POST":
                self.plans.select(
                    parts[2],
                    body.get("target", ""),
                    body.get("selected", True),
                    body.get("pattern", ""),
                )
            return response(
                self.plans.view(
                    parts[2], query.get("prefix", ""), max(0, int(query.get("offset", 0)))
                )
            )
        if len(parts) == 4 and parts[:2] == ["api", "jobs"]:
            job_id, action = parts[2:]
            if method == "GET" and action == "activity":
                return response(self.store.activity(job_id))
            if method == "GET" and action in {"tree", "files"}:
                offset = max(0, int(query.get("offset", 0)))
                limit = min(200, max(1, int(query.get("limit", 100))))
                if action == "tree":
                    return response(self.store.tree(job_id, query.get("prefix", ""), offset, limit))
                return response(
                    self.store.files(
                        job_id, offset, limit, query.get("q", ""), query.get("status", "")
                    )
                )
            if method == "POST":
                self.store.action(job_id, action)
                return response({"ok": True})
        return response({"error": "Page introuvable."}, 404)
