"""Command-line entry point used by DSM, Docker and local development."""

import argparse
import fcntl
import logging
import os
import secrets
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .archive import ArchiveClient
from .auth import Auth
from .config import Settings
from .engine import Engine
from .notifications import Notifications
from .reports import Reports
from .store import Store
from .web import WebApp


def main():
    parser = argparse.ArgumentParser(description="Archive Station")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8274")))
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "data"))
    parser.add_argument("--download-dir", default=os.environ.get("DOWNLOAD_DIR", "downloads"))
    parser.add_argument("--allow-root", action="append")
    parser.add_argument("--set-password-stdin", action="store_true")
    access = parser.add_mutually_exclusive_group()
    access.add_argument("--no-auth", action="store_true", help="Local development on loopback only")
    access.add_argument("--dsm-auth", action="store_true", help="Require the local DSM gateway")
    args = parser.parse_args()
    data = Path(args.data_dir).resolve()
    data.mkdir(parents=True, exist_ok=True)
    if args.set_password_stdin:
        Auth(data).set_password(sys.stdin.read().rstrip("\r\n"))
        return
    if (args.no_auth or args.dsm_auth) and args.host not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("--no-auth and --dsm-auth are only allowed on loopback")
    lock = (data / "process.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("Another Archive Station process already uses this data directory")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(data / "archive-station.log", maxBytes=2_000_000, backupCount=3),
        ],
    )
    if not (args.no_auth or args.dsm_auth) and not Auth(data).path.exists():
        password = secrets.token_urlsafe(18)
        Auth(data).set_password(password)
        initial = data / "initial-password.txt"
        initial.write_text(password + "\n")
        initial.chmod(0o600)
        logging.info("Initial password written to %s", initial)
    roots = args.allow_root or os.environ.get("ALLOWED_ROOTS", "").split(os.pathsep)
    roots = [root for root in roots if root] or [str(Path(args.download_dir).resolve())]
    settings = Settings(data, args.download_dir, roots)
    store = Store(data / "archive-station.sqlite3")
    client = ArchiveClient()
    engine = Engine(store, client, settings)
    reports = Reports(store, settings)
    notifications = Notifications(store, settings, enabled=args.dsm_auth)
    app = WebApp(store, client, settings, data, no_auth=args.no_auth, dsm_auth=args.dsm_auth)
    from waitress import create_server

    server = create_server(
        app,
        host=args.host,
        port=args.port,
        threads=8,
        max_request_body_size=65536,
        expose_tracebacks=False,
    )

    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    engine.start()
    reports.start()
    notifications.start()
    logging.info("Archive Station listening on http://%s:%s", args.host, args.port)
    try:
        server.run()
    finally:
        server.close()
        engine.shutdown()
        reports.shutdown()
        notifications.shutdown()
        store.close()
        lock.close()


if __name__ == "__main__":
    main()
