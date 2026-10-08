"""Recent in-memory ETA samples, separate from the persistent global traffic history."""

import math
import threading
import time
from collections import deque

from .history import TrafficHistory


class Estimates:
    WINDOW = 300
    WARMUP = 30
    STALLED = 60

    def __init__(self, clock=time.monotonic, history_path=None, wall_clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.history = {}
        self.traffic = TrafficHistory(history_path, wall_clock)

    def start(self, job_id):
        with self.lock:
            if job_id not in self.history or self.history[job_id]["last_byte"] is None:
                self.history[job_id] = {
                    "started": self.clock(),
                    "last_byte": None,
                    "buckets": deque(),
                }

    def reset(self, job_id):
        with self.lock:
            self.history.pop(job_id, None)

    @staticmethod
    def _prune(buckets, now, window):
        # One-second resolution: at most window + 1 buckets, regardless of file
        # count or throughput. The boundary bucket can include <1 extra second.
        cutoff = math.floor(now - window)
        while buckets and buckets[0][0] < cutoff:
            buckets.popleft()

    def record(self, job_id, size):
        if size <= 0:
            return
        self.traffic.record(size)
        with self.lock:
            history = self.history.get(job_id)
            if history is None:
                return  # A chunk already in flight must not undo a pause/reset.
            now = self.clock()
            buckets = history["buckets"]
            self._prune(buckets, now, self.WINDOW)
            second = math.floor(now)
            if buckets and buckets[-1][0] == second:
                buckets[-1][1] += size
            else:
                buckets.append([second, size])
            history["last_byte"] = now

    def graph(self, window=3600):
        return self.traffic.graph(window)

    def snapshot(self, job):
        result = {
            "average_speed": 0,
            "average_window_seconds": 0,
            "eta_seconds": None,
            "eta_state": job["status"],
            "eta_lower_bound": bool(job["unknown_sizes"]),
        }
        if job["status"] not in {"running", "queued"}:
            return result
        with self.lock:
            history = self.history.get(job["id"])
            if history is None:
                result["eta_state"] = "queued"
                return result
            now = self.clock()
            elapsed = now - history["started"]
            window = min(self.WINDOW, elapsed)
            self._prune(history["buckets"], now, self.WINDOW)
            speed = sum(size for _, size in history["buckets"]) / window if window > 0 else 0
            result.update(average_speed=speed, average_window_seconds=window)
            last_byte = history["last_byte"]
            if job["failed_files"]:
                state = "error"
            elif now - (last_byte if last_byte is not None else history["started"]) >= self.STALLED:
                state = "stalled"
            elif elapsed < self.WARMUP or not speed:
                state = "measuring"
            elif job["remaining_known_bytes"] <= 0:
                state = "unknown" if job["unknown_sizes"] else "verifying"
            else:
                state = "ready"
                result["eta_seconds"] = math.ceil(job["remaining_known_bytes"] / speed)
            result["eta_state"] = state
            return result
