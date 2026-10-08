"""A bounded day of traffic samples, checkpointed independently of download workers."""

import json
import logging
import math
import os
import threading
import time
from collections import deque
from pathlib import Path

LOG = logging.getLogger(__name__)


class TrafficHistory:
    WINDOWS = (3600, 21600, 43200, 86400)
    RETENTION = 86400
    PERIOD = 30
    SAVE_INTERVAL = 30

    def __init__(self, path=None, clock=time.time):
        self.path = Path(path) if path is not None else None
        self.clock = clock
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.buckets = deque()
        self.revision = self.saved_revision = 0
        if self.path is not None:
            try:
                with self.path.open("rb") as source:
                    content = source.read(256_001)
                if len(content) > 256_000:
                    raise ValueError("History file too large")
                data = json.loads(content)
                rows = data["buckets"]
                if data["version"] != 1 or not isinstance(rows, list) or len(rows) > 2881:
                    raise ValueError("Invalid history format")
                previous = -1
                for row in rows:
                    if (
                        not isinstance(row, list)
                        or len(row) != 2
                        or any(type(value) is not int for value in row)
                        or not previous < row[0]
                        or row[0] % self.PERIOD
                        or not 0 <= row[1] <= 10**18
                    ):
                        raise ValueError("Invalid history sample")
                    previous = row[0]
                self.buckets = deque(rows)
                self._prune(self.clock())
            except FileNotFoundError:
                pass
            except (OSError, ValueError, KeyError, TypeError):
                LOG.warning("Ignoring unreadable transfer history", exc_info=True)

    def _prune(self, now):
        current = math.floor(now / self.PERIOD) * self.PERIOD
        cutoff = current - self.RETENTION
        while self.buckets and self.buckets[0][0] < cutoff:
            self.buckets.popleft()
        # Wall-clock corrections must not create unordered or future samples.
        while self.buckets and self.buckets[-1][0] > current:
            self.buckets.pop()

    def record(self, size):
        if size <= 0:
            return
        with self.lock:
            now = self.clock()
            self._prune(now)
            bucket = math.floor(now / self.PERIOD) * self.PERIOD
            if self.buckets and self.buckets[-1][0] == bucket:
                self.buckets[-1][1] += size
            else:
                self.buckets.append([bucket, size])
            self.revision += 1

    def graph(self, window=3600):
        if type(window) is not int or window not in self.WINDOWS:
            raise ValueError("Paramètre inconnu.")
        with self.lock:
            now = self.clock()
            self._prune(now)
            # Exclude the open bucket: each point represents a complete interval,
            # never an unfinished interval padded with zeroes.
            end = math.floor(now / self.PERIOD) * self.PERIOD
            start = end - window
            period = window // 120
            values = [0] * 120
            for timestamp, size in self.buckets:
                index = (timestamp - start) // period
                if 0 <= index < len(values):
                    values[index] += size
            return {
                "values": [size / period for size in values],
                "period_seconds": period,
                "window_seconds": window,
                "end_time": end,
            }

    def save(self):
        if self.path is None:
            return
        with self.write_lock:
            with self.lock:
                if self.revision == self.saved_revision:
                    return
                self._prune(self.clock())
                revision = self.revision
                data = {"version": 1, "buckets": [list(row) for row in self.buckets]}
            # No download or UI lock is held during filesystem access.
            try:
                temporary = self.path.with_suffix(self.path.suffix + ".tmp")
                with temporary.open("w") as target:
                    json.dump(data, target, separators=(",", ":"))
                    target.flush()
                    os.fsync(target.fileno())
                temporary.replace(self.path)
            except OSError:
                LOG.warning("Could not checkpoint transfer history", exc_info=True)
                return
            with self.lock:
                self.saved_revision = revision

    def start(self):
        def run():
            while not self.stop.wait(self.SAVE_INTERVAL):
                self.save()
            self.save()

        self.thread = threading.Thread(target=run, name="transfer-history", daemon=True)
        self.thread.start()

    def shutdown(self, timeout=2):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=timeout)
        return not (self.thread and self.thread.is_alive())
