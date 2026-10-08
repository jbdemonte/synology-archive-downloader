"""Bounded, resumable transfers with per-file integrity checks and retry backoff."""

import hashlib
import logging
import os
import re
import shutil
import threading
import time
from urllib.error import HTTPError

from .archive import safe_path
from .schedule import policy

LOG = logging.getLogger(__name__)
CHUNK = 64 * 1024


class Interrupted(Exception):
    pass


class Conflict(Exception):
    pass


class Engine:
    def __init__(self, store, client, settings):
        self.store, self.client, self.settings = store, client, settings
        self.stop = threading.Event()
        self.threads = []
        self.rate_lock = threading.Lock()
        self.next_chunk = 0
        self.rate_limit = 0

    def start(self):
        for index in range(8):
            thread = threading.Thread(
                target=self.worker, args=(index,), daemon=True, name=f"download-{index}"
            )
            self.threads.append(thread)
            thread.start()

    def shutdown(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=35)

    def check(self, job_id):
        if (
            self.stop.is_set()
            or not self.store.active(job_id)
            or not policy(self.settings.get())["allowed"]
        ):
            raise Interrupted()

    def throttle(self, size, job_id):
        limit = policy(self.settings.get())["limit_kib"] * 1024
        if not limit:
            return
        with self.rate_lock:
            if limit != self.rate_limit:
                self.next_chunk = 0
                self.rate_limit = limit
            now = time.monotonic()
            scheduled = max(now, self.next_chunk)
            self.next_chunk = scheduled + size / limit
        while time.monotonic() < scheduled:
            if policy(self.settings.get())["limit_kib"] * 1024 != limit:
                with self.rate_lock:
                    self.next_chunk = 0
                return
            self.check(job_id)
            self.stop.wait(min(0.2, scheduled - time.monotonic()))

    def worker(self, index):
        while not self.stop.is_set():
            if (
                index >= self.settings.get()["connections"]
                or not policy(self.settings.get())["allowed"]
            ):
                self.stop.wait(0.5)
                continue
            row = self.store.claim()
            if not row:
                self.store.finish_jobs()
                self.stop.wait(0.3)
                continue
            try:
                self.transfer(row)
            except Interrupted:
                self.store.estimates.reset(row["job_id"])
                self.store.update(row["id"], status="queued", speed=0)
            except Exception as exc:
                LOG.warning("Transfer failed for %s: %s", row["name"], exc)
                attempts = row["attempts"] + 1
                permanent = isinstance(exc, (Conflict, ValueError, PermissionError)) or (
                    isinstance(exc, HTTPError) and exc.code in {401, 403, 404}
                )
                retry = not permanent and attempts <= self.settings.get()["retries"]
                message = str(exc)
                if isinstance(exc, HTTPError) and exc.code in {401, 403}:
                    message = "Accès refusé : ce fichier nécessite une autorisation Archive.org."
                delay = min(300, 5 * 2 ** (attempts - 1))
                if isinstance(exc, HTTPError) and exc.headers:
                    try:
                        delay = max(delay, min(3600, int(exc.headers.get("Retry-After", 0))))
                    except ValueError:
                        pass
                if isinstance(exc, HTTPError):
                    exc.close()
                self.store.update(
                    row["id"],
                    status="queued" if retry else "error",
                    speed=0,
                    attempts=attempts,
                    available_at=time.time() + delay,
                    error=message,
                )
            self.store.finish_jobs()

    def verified(self, path, row):
        if row["size"] is not None and path.stat().st_size != row["size"]:
            return False
        if row["digest"] and self.settings.get()["verify_checksums"]:
            digest = hashlib.new(row["algorithm"], usedforsecurity=False)
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    self.check(row["job_id"])
                    digest.update(chunk)
            return digest.hexdigest() == row["digest"]
        return row["size"] is not None

    def complete(self, row, path):
        size = path.stat().st_size
        self.store.update(
            row["id"], status="completed", downloaded=size, size=size, speed=0, error=None
        )

    def transfer(self, row):
        self.check(row["job_id"])
        root = self.settings.directory(row["destination"], create=True)
        target = safe_path(root, f"{row['identifier']}/{row['name']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = safe_path(root, f".archive-station-parts/{row['job_id']}/{row['id']}.part")
        partial.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_file() and self.verified(target, row):
                self.complete(row, target)
                return
            raise Conflict("Un fichier différent existe déjà. Déplacez-le avant de réessayer.")
        offset = partial.stat().st_size if partial.exists() else 0
        if row["size"] is not None and offset > row["size"]:
            partial.unlink()
            offset = 0
        if partial.exists() and row["size"] == offset:
            if self.verified(partial, row):
                self.publish(partial, target, row)
                return
            partial.unlink()
            offset = 0
        if shutil.disk_usage(root).free < CHUNK * 2:
            raise OSError("Espace disque insuffisant.")
        with self.client.open(row["identifier"], row["name"], offset) as response:
            if response.status not in {200, 206}:
                raise OSError(f"Réponse HTTP inattendue : {response.status}")
            if response.status == 206:
                content_range = re.fullmatch(
                    r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
                )
                if not content_range or int(content_range[1]) != offset:
                    raise OSError("Réponse de reprise invalide (Content-Range).")
                end, total = int(content_range[2]), int(content_range[3])
                if (
                    end < offset
                    or end >= total
                    or (row["size"] is not None and total != row["size"])
                ):
                    raise OSError("La taille distante a changé pendant la reprise.")
                expected = total
            else:
                # Some servers ignore Range. Truncate rather than append the full response.
                offset = 0
                length = response.headers.get("Content-Length")
                expected = int(length) if length is not None else row["size"]
            if row["size"] is not None and expected is not None and expected != row["size"]:
                raise OSError("La taille distante ne correspond plus aux métadonnées.")
            self.store.update(row["id"], downloaded=offset, speed=0)
            received, last_bytes, last_report = offset, offset, time.monotonic()
            # Do not leave the most recent chunk in Python's userspace buffer:
            # a killed process must be able to resume from the bytes on disk.
            with partial.open("ab" if offset else "wb", buffering=0) as output:
                try:
                    while True:
                        self.check(row["job_id"])
                        chunk = response.read(CHUNK)
                        if not chunk:
                            break
                        self.throttle(len(chunk), row["job_id"])
                        written = output.write(chunk)
                        received += written
                        self.store.estimates.record(row["job_id"], written)
                        if written != len(chunk):
                            raise OSError("Incomplete disk write; transfer will resume from disk.")
                        if expected is not None and received > expected:
                            raise OSError("Le transfert dépasse la taille attendue.")
                        now = time.monotonic()
                        if now - last_report >= 0.5:
                            self.store.update(
                                row["id"],
                                downloaded=received,
                                speed=(received - last_bytes) / (now - last_report),
                            )
                            last_bytes, last_report = received, now
                finally:
                    output.flush()
                    os.fsync(output.fileno())
                    self.store.update(row["id"], downloaded=received, speed=0)
            if expected is not None and received != expected:
                raise OSError("Transfert interrompu avant la fin du fichier.")
        self.check(row["job_id"])
        # Unknown-size files can be checked against the response length or checksum.
        verification_row = {**row, "size": expected if expected is not None else received}
        if not self.verified(partial, verification_row):
            partial.unlink()
            self.store.update(row["id"], downloaded=0)
            raise OSError("Somme de contrôle incorrecte. Le fichier sera téléchargé à nouveau.")
        self.publish(partial, target, row)

    def publish(self, partial, target, row):
        self.check(row["job_id"])
        # Hard-link publication is atomic and refuses to overwrite an existing destination.
        os.link(partial, target)
        partial.unlink()
        self.complete(row, target)
