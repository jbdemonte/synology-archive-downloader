"""Bounded server-side selections: large manifests never travel in POST bodies."""

import fnmatch
import secrets
import threading
import time
from collections import OrderedDict


class Plans:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.items = OrderedDict()

    def create(self, manifest, mode, pattern):
        if len(manifest["files"]) > 500_000:
            raise ValueError("Archive trop volumineuse. Affiner le filtre.")
        with self.lock:
            while self.items and (
                len(self.items) >= 20
                or sum(len(p["manifest"]["files"]) for p in self.items.values())
                + len(manifest["files"])
                > 500_000
            ):
                self.items.popitem(last=False)
            token = secrets.token_urlsafe(24)
            self.items[token] = {
                "manifest": manifest,
                "selected": set(range(len(manifest["files"]))),
                "touched": self.clock(),
                "mode": mode,
                "pattern": pattern,
            }
            return token

    def _get(self, token):
        plan = self.items.get(token)
        if plan is None or self.clock() - plan["touched"] > 3600:
            self.items.pop(token, None)
            raise ValueError("L’analyse a expiré. Analyser les URL à nouveau.")
        plan["touched"] = self.clock()
        self.items.move_to_end(token)
        return plan

    def selection(self, token, identifier):
        with self.lock:
            plan = self._get(token)
            manifest = plan["manifest"]
            if manifest["identifier"] != identifier:
                raise ValueError("Sélection de fichiers invalide.")
            files = [f for i, f in enumerate(manifest["files"]) if i in plan["selected"]]
            if not files:
                raise ValueError("Sélectionner au moins un fichier.")
            return (
                {
                    **manifest,
                    "files": files,
                    "total_size": sum(f["size"] or 0 for f in files),
                    "unknown_sizes": sum(f["size"] is None for f in files),
                },
                plan["mode"],
                plan["pattern"],
            )

    def select(self, token, target="", selected=True, pattern=""):
        if type(selected) is not bool or not isinstance(target, str):
            raise ValueError("Sélection de fichiers invalide.")
        if not isinstance(pattern, str) or len(pattern) > 200:
            raise ValueError("Filtre trop long.")
        with self.lock:
            plan = self._get(token)
            for index, item in enumerate(plan["manifest"]["files"]):
                name = item["name"]
                matches = (
                    not target
                    or name == target
                    or (target.endswith("/") and name.startswith(target))
                )
                if matches and (not pattern or fnmatch.fnmatchcase(name, pattern)):
                    if selected:
                        plan["selected"].add(index)
                    else:
                        plan["selected"].discard(index)

    def view(self, token, prefix="", offset=0, limit=100):
        prefix = prefix.rstrip("/") + "/" if prefix else ""
        with self.lock:
            plan = self._get(token)
            groups = {}
            count = total_size = unknown = 0
            for index, item in enumerate(plan["manifest"]["files"]):
                chosen = index in plan["selected"]
                count += chosen
                total_size += (item["size"] or 0) if chosen else 0
                unknown += chosen and item["size"] is None
                if not item["name"].startswith(prefix):
                    continue
                rest = item["name"][len(prefix) :]
                name, separator, _ = rest.partition("/")
                path = prefix + name + ("/" if separator else "")
                group = groups.setdefault(
                    path,
                    {
                        "name": name,
                        "path": path,
                        "kind": "folder" if separator else "file",
                        "file_count": 0,
                        "selected_count": 0,
                        "size": 0,
                        "unknown_sizes": 0,
                    },
                )
                group["file_count"] += 1
                group["selected_count"] += chosen
                group["size"] += item["size"] or 0
                group["unknown_sizes"] += item["size"] is None
            rows = sorted(groups.values(), key=lambda f: (f["kind"] != "folder", f["name"].lower()))
            offset = min(max(0, offset), max(0, (len(rows) - 1) // limit * limit))
            return {
                "children": rows[offset : offset + limit],
                "total": len(rows),
                "offset": offset,
                "prefix": prefix,
                "selected_count": count,
                "selected_size": total_size,
                "unknown_sizes": unknown,
                "file_count": len(plan["manifest"]["files"]),
            }
