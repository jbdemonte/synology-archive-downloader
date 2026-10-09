"""Optional GitHub release checks, independent of download workers and credentials."""

import json
import logging
import re
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from . import __package_version__

LOG = logging.getLogger(__name__)
REPOSITORY = "jbdemonte/synology-archive-downloader"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100"
INTERVAL = 24 * 60 * 60


def version_key(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"v?([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})(?:-([0-9]{1,6}))?", value)
    return tuple(int(part or 0) for part in match.groups()) if match else None


def select_release(releases, include_prereleases):
    if not isinstance(releases, list):
        raise ValueError("Invalid GitHub release list")
    candidates = []
    for release in releases:
        if not isinstance(release, dict):
            continue
        tag = release.get("tag_name")
        key = version_key(tag)
        if (
            key is None
            or release.get("draft")
            or (release.get("prerelease") and not include_prereleases)
        ):
            continue
        version = tag.removeprefix("v")
        assets = release.get("assets")
        if not isinstance(assets, list) or not any(
            isinstance(asset, dict)
            and asset.get("name") == f"ArchiveStation-{version}-x86_64.spk"
            and asset.get("state") == "uploaded"
            and type(asset.get("size")) is int
            and asset["size"] > 0
            for asset in assets
        ):
            continue
        candidates.append((key, tag, bool(release.get("prerelease"))))
    if not candidates:
        return None
    _, tag, prerelease = max(candidates)
    return {"tag": tag, "prerelease": prerelease}


def fetch_releases():
    request = Request(
        API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": f"ArchiveStation/{__package_version__}",
        },
    )
    with urlopen(request, timeout=10) as response:
        content = response.read(2_000_001)
    if len(content) > 2_000_000:
        raise ValueError("GitHub release response is too large")
    return json.loads(content)


class Updates:
    def __init__(
        self,
        settings,
        data_dir,
        current=__package_version__,
        fetch=fetch_releases,
        clock=time.time,
        enabled=True,
    ):
        self.enabled = enabled
        self.settings, self.current, self.fetch, self.clock = settings, current, fetch, clock
        self.path = Path(data_dir) / "updates.json"
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.worker = self.scheduler = None
        self.generation = 0
        self.mode = self._mode()
        self.result = {"state": "idle", "checked_at": None, "release": None}
        try:
            cached = json.loads(self.path.read_text())
            checked = cached["checked_at"]
            if (
                type(checked) in {int, float}
                and 0 < checked <= clock()
                and cached["include_prereleases"] == self.mode[1]
                and type(cached["include_prereleases"]) is bool
                and cached["state"] in {"ok", "none", "error"}
                and (
                    (cached["state"] != "ok" and cached["release"] is None)
                    or (
                        cached["state"] == "ok"
                        and isinstance(cached["release"], dict)
                        and version_key(cached["release"].get("tag"))
                        and type(cached["release"].get("prerelease")) is bool
                        and (self.mode[1] or not cached["release"]["prerelease"])
                    )
                )
            ):
                self.result = {key: cached[key] for key in self.result}
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _mode(self):
        settings = self.settings.get()
        return settings.get("check_updates", False), settings.get("update_prereleases", False)

    def configure(self):
        with self.lock:
            mode = self._mode()
            if mode != self.mode:
                self.mode = mode
                self.generation += 1
                self.result = {"state": "idle", "checked_at": None, "release": None}
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    LOG.warning("Could not clear the update cache")
        self.wake.set()

    def snapshot(self):
        if not self.enabled:
            return {
                "state": "disabled",
                "current_version": self.current,
                "latest_version": None,
                "release_url": None,
                "checked_at": None,
                "prerelease": False,
                "retry_after": 0,
            }
        with self.lock:
            result = dict(self.result)
            release = result.pop("release")
            checking = bool(self.worker and self.worker.is_alive())
            state = result["state"]
            if state == "ok":
                newer = release and version_key(release["tag"]) > (
                    version_key(self.current) or (0,) * 4
                )
                state = "available" if newer else "current"
            if state == "idle" and not self.mode[0]:
                state = "disabled"
            result.update(
                state="checking" if checking else state,
                current_version=self.current,
                latest_version=release["tag"].removeprefix("v") if release else None,
                prerelease=bool(release and release["prerelease"]),
                release_url=(RELEASES_URL + "/tag/" + quote(release["tag"], safe=""))
                if release
                else None,
                retry_after=max(0, int((result["checked_at"] or 0) + 60 - self.clock())),
            )
            return result

    def request(self):
        if not self.enabled:
            return self.snapshot()
        with self.lock:
            if self.stop.is_set() or (self.worker and self.worker.is_alive()):
                return self.snapshot()
            if self.result["checked_at"] and self.clock() - self.result["checked_at"] < 60:
                return self.snapshot()
            self.worker = threading.Thread(
                target=self._check,
                args=(self.generation, self.mode[1]),
                name="release-check",
                daemon=True,
            )
            self.worker.start()
        return self.snapshot()

    def _check(self, generation, include_prereleases):
        release = None
        try:
            release = select_release(self.fetch(), include_prereleases)
            state = "ok" if release else "none"
        except HTTPError as error:
            # An unauthenticated 404 also covers private/unpublished repositories.
            state = "none" if error.code == 404 else "error"
        except Exception:
            state = "error"
            LOG.warning("GitHub release check unavailable", exc_info=True)
        with self.lock:
            if generation != self.generation or self.stop.is_set():
                return
            self.result = {"state": state, "checked_at": self.clock(), "release": release}
            cached = {**self.result, "include_prereleases": include_prereleases}
            try:
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(cached))
                temporary.replace(self.path)
            except OSError:
                LOG.warning("Could not save the update cache")

    def tick(self):
        with self.lock:
            checked = self.result["checked_at"]
            due = checked is None or self.clock() - checked >= INTERVAL
            if self.mode[0] and due:
                self.request()

    def start(self):
        if not self.enabled:
            return

        def run():
            while not self.stop.is_set():
                self.wake.clear()
                self.tick()
                self.wake.wait(60)

        self.scheduler = threading.Thread(target=run, name="release-scheduler", daemon=True)
        self.scheduler.start()

    def shutdown(self):
        self.stop.set()
        self.wake.set()
        if self.scheduler:
            self.scheduler.join(timeout=1)
        # The network worker is a daemon and never touches download state. It
        # discards its result after stop, without delaying the package shutdown.
