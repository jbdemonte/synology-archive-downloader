"""SQLite state, shared by the API and a bounded set of download workers."""

import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .estimates import Estimates


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.estimates = Estimates()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, identifier TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
                mode TEXT NOT NULL, pattern TEXT NOT NULL, destination TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                name TEXT NOT NULL, size INTEGER, algorithm TEXT, digest TEXT,
                status TEXT NOT NULL DEFAULT 'queued', downloaded INTEGER NOT NULL DEFAULT 0,
                speed REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                available_at REAL NOT NULL DEFAULT 0, error TEXT,
                UNIQUE(job_id, name)
            );
            CREATE TABLE IF NOT EXISTS known_files (
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                name TEXT NOT NULL, PRIMARY KEY(job_id,name)
            );
            CREATE INDEX IF NOT EXISTS file_queue ON files(job_id, status, available_at, id);
            CREATE TABLE IF NOT EXISTS incidents (
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                file_id INTEGER NOT NULL, name TEXT NOT NULL, message TEXT NOT NULL,
                first_at REAL, last_at REAL, occurrences INTEGER NOT NULL DEFAULT 1,
                attempt INTEGER NOT NULL DEFAULT 0, resolved_at REAL,
                PRIMARY KEY(job_id,file_id,message)
            );
        """)
        with self.lock, self.db:
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}
            for name, definition in {
                "source_url": "TEXT",
                "finished_at": "REAL",
                "hold_reason": "TEXT",
                "manifest_revision": "INTEGER NOT NULL DEFAULT 0",
                "priority": "INTEGER NOT NULL DEFAULT 0",
                "queue_order": "REAL NOT NULL DEFAULT 0",
                "error_history_since": "REAL",
            }.items():
                if name not in columns:
                    self.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            self.db.execute(
                "UPDATE jobs SET error_history_since=? WHERE error_history_since IS NULL",
                (time.time(),),
            )
            # Older versions only kept the latest error, with no timestamp.
            self.db.execute(
                "INSERT OR IGNORE INTO incidents(job_id,file_id,name,message,attempt) "
                "SELECT job_id,id,name,substr(error,1,2048),attempts FROM files "
                "WHERE error IS NOT NULL AND error<>''"
            )
            file_columns = {row[1] for row in self.db.execute("PRAGMA table_info(files)")}
            self.db.execute("INSERT OR IGNORE INTO known_files SELECT job_id,name FROM files")
            if "reset_partial" not in file_columns:
                self.db.execute(
                    "ALTER TABLE files ADD COLUMN reset_partial INTEGER NOT NULL DEFAULT 0"
                )
            if "repair" not in file_columns:
                self.db.execute("ALTER TABLE files ADD COLUMN repair INTEGER NOT NULL DEFAULT 0")
            if "priority" not in file_columns:
                self.db.execute("ALTER TABLE files ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS file_priority ON files(job_id,status,priority DESC,id)"
            )
            self.db.execute("UPDATE files SET status='queued', speed=0 WHERE status='downloading'")
            self.db.execute("UPDATE jobs SET status='queued' WHERE status='running'")

    def close(self):
        with self.lock:
            self.db.close()

    def add(self, manifest, mode, pattern, destination, paused=False, source_url=None):
        job_id = uuid.uuid4().hex
        with self.lock, self.db:
            try:
                self.db.execute(
                    "INSERT INTO jobs(id,identifier,title,status,created,mode,pattern,destination,"
                    "source_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        manifest["identifier"],
                        manifest["title"],
                        "paused" if paused else "queued",
                        time.time(),
                        mode,
                        pattern,
                        str(destination),
                        source_url or f"https://archive.org/details/{manifest['identifier']}",
                    ),
                )
                self.db.execute(
                    "UPDATE jobs SET error_history_since=created, "
                    "queue_order=(SELECT COALESCE(MAX(queue_order),0)+1 FROM jobs) "
                    "WHERE id=?",
                    (job_id,),
                )
                self.db.executemany(
                    "INSERT INTO files(job_id,name,size,algorithm,digest) VALUES (?,?,?,?,?)",
                    [
                        (job_id, f["name"], f["size"], f["algorithm"], f["digest"])
                        for f in manifest["files"]
                    ],
                )
                self.db.executemany(
                    "INSERT OR IGNORE INTO known_files VALUES (?,?)",
                    [
                        (job_id, name)
                        for name in manifest.get(
                            "known_names", [f["name"] for f in manifest["files"]]
                        )
                    ],
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Cet élément est déjà dans la liste des téléchargements.") from exc
        return job_id

    def jobs(self):
        with self.lock:
            jobs = [
                dict(row)
                for row in self.db.execute("""
                SELECT j.*, COUNT(f.id) AS file_count,
                    COALESCE(SUM(f.size),0) AS total_size,
                    COALESCE(SUM(f.downloaded),0) AS downloaded,
                    COALESCE(SUM(CASE WHEN f.size IS NOT NULL
                        THEN MAX(f.size-f.downloaded,0) ELSE 0 END),0) AS remaining_known_bytes,
                    COALESCE(SUM(CASE WHEN j.status IN ('running','queued')
                        THEN f.speed ELSE 0 END),0) AS speed,
                    SUM(f.status='completed') AS completed_files,
                    SUM(f.status='downloading') AS active_files,
                    COALESCE(SUM(CASE WHEN f.status='completed' THEN f.downloaded ELSE 0 END),0)
                        AS completed_bytes,
                    SUM(f.status='error') AS failed_files,
                    SUM(f.size IS NULL) AS unknown_sizes,
                    (SELECT COALESCE(SUM(i.occurrences),0) FROM incidents i WHERE i.job_id=j.id)
                        AS incident_count,
                    (SELECT COALESCE(SUM(i.occurrences),0) FROM incidents i
                        WHERE i.job_id=j.id AND i.resolved_at IS NULL) AS unresolved_incidents
                FROM jobs j JOIN files f ON f.job_id=j.id
                GROUP BY j.id ORDER BY j.priority DESC,j.queue_order,j.created
            """)
            ]
            for job in jobs:
                job.update(self.estimates.snapshot(job))
            return jobs

    def error_history(self, job_id):
        with self.lock:
            return [
                dict(row)
                for row in self.db.execute(
                    "SELECT i.*,f.status AS file_status FROM incidents i "
                    "LEFT JOIN files f ON f.id=i.file_id AND f.job_id=i.job_id "
                    "WHERE i.job_id=? ORDER BY i.first_at,i.file_id,i.message",
                    (job_id,),
                )
            ]

    def _record_incident(self, job_id, file_id, name, message, attempt=0):
        # Called under the store transaction; no writes on successful chunks.
        now = time.time()
        self.db.execute(
            "INSERT INTO incidents(job_id,file_id,name,message,first_at,last_at,attempt) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(job_id,file_id,message) DO UPDATE SET "
            "last_at=excluded.last_at,occurrences=incidents.occurrences+1, "
            "attempt=excluded.attempt,resolved_at=NULL",
            (job_id, file_id, name, str(message)[:2048], now, now, attempt),
        )

    def files(self, job_id, offset=0, limit=100, query="", status=""):
        with self.lock:
            if not self.db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
                raise KeyError("Téléchargement introuvable.")
            where, args = "job_id=?", [job_id]
            if query:
                where += " AND instr(lower(name), lower(?))>0"
                args.append(query)
            if status:
                where += " AND status=?"
                args.append(status)
            total = self.db.execute(f"SELECT COUNT(*) FROM files WHERE {where}", args).fetchone()[0]
            offset = min(offset, max(0, (total - 1) // limit * limit))
            rows = self.db.execute(
                f"SELECT * FROM files WHERE {where} ORDER BY id LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
            return {"files": [dict(row) for row in rows], "total": total, "offset": offset}

    def activity(self, job_id):
        """Bounded live view across all directories, ordered like the worker queue."""
        with self.lock:
            if not self.db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
                raise KeyError("Téléchargement introuvable.")
            counts = dict.fromkeys(("downloading", "queued", "completed", "error"), 0)
            counts.update(
                dict(
                    self.db.execute(
                        "SELECT status,COUNT(*) FROM files WHERE job_id=? GROUP BY status",
                        (job_id,),
                    )
                )
            )

            def rows(status, limit, condition="", args=(), order="priority DESC,id"):
                return [
                    dict(row)
                    for row in self.db.execute(
                        "SELECT id,name,size,downloaded,speed,status,error,priority FROM files "
                        f"WHERE job_id=? AND status=? {condition} ORDER BY {order} LIMIT ?",
                        (job_id, status, *args, limit),
                    )
                ]

            now = time.time()
            queued = rows("queued", 10, "AND available_at<=?", (now,))
            # Delayed retries follow ready files, never hide the next eligible transfer.
            queued += rows(
                "queued",
                10 - len(queued),
                "AND available_at>?",
                (now,),
                "available_at,priority DESC,id",
            )
            return {
                "active": rows("downloading", 200),
                "queued": queued,
                "errors": rows("error", 5),
                "counts": counts,
            }

    def action(self, job_id, action):
        with self.lock, self.db:
            job = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise KeyError("Téléchargement introuvable.")
            if action == "pause":
                if job["status"] in {"queued", "running"}:
                    self.db.execute(
                        "UPDATE jobs SET status='paused', hold_reason=NULL WHERE id=?", (job_id,)
                    )
            elif action in {"resume", "retry"}:
                if job["status"] != "completed":
                    self.db.execute(
                        "UPDATE files SET status='queued', attempts=0, available_at=0, "
                        "error=NULL WHERE job_id=? AND status='error'",
                        (job_id,),
                    )
                    self.db.execute(
                        "UPDATE jobs SET status='queued', finished_at=NULL, hold_reason=NULL "
                        "WHERE id=?",
                        (job_id,),
                    )
            elif action == "repair":
                if (
                    job["status"] in {"queued", "running"}
                    or self.db.execute(
                        "SELECT 1 FROM files WHERE job_id=? AND status='downloading'", (job_id,)
                    ).fetchone()
                ):
                    raise ValueError("Mettez la tâche en pause et attendez l’arrêt des transferts.")
                self.db.execute(
                    "UPDATE files SET status='queued', downloaded=0, speed=0, "
                    "attempts=0, available_at=0, error=NULL, repair=1 WHERE job_id=?",
                    (job_id,),
                )
                self.db.execute(
                    "UPDATE jobs SET status='queued', finished_at=NULL, "
                    "hold_reason=NULL WHERE id=?",
                    (job_id,),
                )
                self.estimates.reset(job_id)
                self.estimates.start(job_id)
            elif action == "cancel":
                self.db.execute(
                    "UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?",
                    (time.time(), job_id),
                )
            elif action == "remove":
                if (
                    job["status"] in {"queued", "running"}
                    or self.db.execute(
                        "SELECT 1 FROM files WHERE job_id=? AND status='downloading'", (job_id,)
                    ).fetchone()
                ):
                    raise ValueError("Mettez la tâche en pause et attendez l’arrêt des transferts.")
                self.db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            else:
                raise ValueError("Action inconnue.")
            if action in {"pause", "cancel", "remove"}:
                self.estimates.reset(job_id)
            elif job["status"] in {"paused", "cancelled", "error"}:
                self.estimates.reset(job_id)
                self.estimates.start(job_id)

    def apply_refresh(self, job_id, manifest, paused=False):
        with self.lock, self.db:
            job = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise KeyError("Téléchargement introuvable.")
            if job["manifest_revision"] != manifest["revision"]:
                raise ValueError("L’analyse a expiré. Analyser les URL à nouveau.")
            if (
                job["status"] in {"queued", "running"}
                or self.db.execute(
                    "SELECT 1 FROM files WHERE job_id=? AND status='downloading'", (job_id,)
                ).fetchone()
            ):
                raise ValueError("Mettez la tâche en pause et attendez l’arrêt des transferts.")
            for file in manifest["files"]:
                if file["change"] == "new":
                    self.db.execute(
                        "INSERT INTO files(job_id,name,size,algorithm,digest) VALUES (?,?,?,?,?)",
                        (job_id, file["name"], file["size"], file["algorithm"], file["digest"]),
                    )
                else:
                    self.db.execute(
                        "UPDATE files SET size=?,algorithm=?,digest=?, status='queued', "
                        "downloaded=0,speed=0,attempts=0,available_at=0,error=NULL, "
                        "repair=1,reset_partial=1 WHERE job_id=? AND name=?",
                        (file["size"], file["algorithm"], file["digest"], job_id, file["name"]),
                    )
                self.db.execute(
                    "INSERT OR IGNORE INTO known_files VALUES (?,?)", (job_id, file["name"])
                )
            self.db.execute(
                "UPDATE jobs SET manifest_revision=manifest_revision+1, "
                "status=?,finished_at=NULL,hold_reason=NULL WHERE id=?",
                ("paused" if paused else "queued", job_id),
            )
            self.estimates.reset(job_id)

    def bulk(self, action, ids=None):
        global_actions = {
            "pause_all": ("pause", {"queued", "running"}),
            "resume_all": ("resume", {"paused"}),
            "remove_completed": ("remove", {"completed"}),
        }
        with self.lock:
            if action in global_actions:
                action, states = global_actions[action]
                ids = [
                    row["id"]
                    for row in self.db.execute("SELECT id,status FROM jobs")
                    if row["status"] in states
                ]
            elif action not in {"pause", "resume", "retry", "cancel", "remove"}:
                raise ValueError("Action inconnue.")
            elif (
                not isinstance(ids, list)
                or len(ids) > 500
                or any(not isinstance(item, str) for item in ids)
            ):
                raise ValueError("Invalid task selection")
            updated, skipped = [], []
            for job_id in dict.fromkeys(ids):
                job = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                if (
                    not job
                    or (action == "pause" and job["status"] not in {"queued", "running"})
                    or (action in {"resume", "retry"} and job["status"] == "completed")
                    or (action == "cancel" and job["status"] in {"completed", "cancelled"})
                ):
                    skipped.append(job_id)
                    continue
                try:
                    self.action(job_id, action)
                    updated.append(job_id)
                except ValueError:
                    skipped.append(job_id)
            return {"updated": updated, "skipped": skipped}

    def pause_for_space(self, job_id):
        with self.lock, self.db:
            changed = self.db.execute(
                "UPDATE jobs SET status='paused', hold_reason='disk' WHERE id=? "
                "AND status IN ('queued','running')",
                (job_id,),
            )
            if changed.rowcount:
                self._record_incident(job_id, 0, "", "Espace disque insuffisant.")
            self.estimates.reset(job_id)

    def prioritize(self, job_id, priority=None, file_id=None, move=None):
        if priority is not None and (type(priority) is not int or priority not in {-1, 0, 1}):
            raise ValueError("Invalid priority")
        with self.lock, self.db:
            job = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise KeyError("Téléchargement introuvable.")
            if file_id is not None:
                if type(file_id) is not int or priority is None or move is not None:
                    raise ValueError("Invalid file priority")
                changed = self.db.execute(
                    "UPDATE files SET priority=? WHERE job_id=? AND id=? AND status='queued'",
                    (priority, job_id, file_id),
                )
                if not changed.rowcount:
                    raise ValueError("Ce fichier n’est plus en attente.")
            elif move is not None:
                if move not in {"up", "down"}:
                    raise ValueError("Invalid queue movement")
                peers = [
                    r[0]
                    for r in self.db.execute(
                        "SELECT id FROM jobs WHERE priority=? ORDER BY queue_order,created",
                        (job["priority"],),
                    )
                ]
                index = peers.index(job_id)
                other = index + (-1 if move == "up" else 1)
                if 0 <= other < len(peers):
                    peers[index], peers[other] = peers[other], peers[index]
                    self.db.executemany(
                        "UPDATE jobs SET queue_order=? WHERE id=?", list(enumerate(peers))
                    )
            elif priority is not None:
                self.db.execute("UPDATE jobs SET priority=? WHERE id=?", (priority, job_id))
            else:
                raise ValueError("Missing priority")

    def claim(self):
        with self.lock, self.db:
            row = self.db.execute(
                """
                SELECT f.*, j.identifier, j.destination FROM jobs j JOIN files f ON j.id=f.job_id
                WHERE j.status IN ('queued','running') AND f.status='queued' AND f.available_at<=?
                ORDER BY j.priority DESC,j.queue_order,j.created,f.priority DESC,f.id LIMIT 1
            """,
                (time.time(),),
            ).fetchone()
            if not row:
                return None
            self.db.execute(
                "UPDATE files SET status='downloading', error=NULL WHERE id=?", (row["id"],)
            )
            self.db.execute("UPDATE jobs SET status='running' WHERE id=?", (row["job_id"],))
            self.estimates.start(row["job_id"])
            return dict(row)

    def active(self, job_id):
        with self.lock:
            row = self.db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            return row and row[0] in {"queued", "running"}

    def destination_locked(self):
        """Keep the default fixed until queued jobs and in-flight workers stop."""
        with self.lock:
            return (
                self.db.execute("""
                SELECT 1 FROM jobs j WHERE j.status IN ('queued','running') OR EXISTS (
                    SELECT 1 FROM files f WHERE f.job_id=j.id AND f.status='downloading'
                ) LIMIT 1
            """).fetchone()
                is not None
            )

    def update(self, file_id, **values):
        allowed = {
            "status",
            "downloaded",
            "speed",
            "attempts",
            "available_at",
            "error",
            "size",
            "repair",
            "reset_partial",
        }
        if not values.keys() <= allowed:
            raise ValueError("Unknown file field")
        with self.lock, self.db:
            fields = ", ".join(f"{key}=?" for key in values)
            self.db.execute(f"UPDATE files SET {fields} WHERE id=?", [*values.values(), file_id])
            if values.get("error") or values.get("status") == "completed":
                row = self.db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
                if row and values.get("error"):
                    self._record_incident(
                        row["job_id"], file_id, row["name"], values["error"], row["attempts"]
                    )
                elif row:
                    self.db.execute(
                        "UPDATE incidents SET resolved_at=? WHERE job_id=? "
                        "AND (file_id=? OR (file_id=0 AND EXISTS(SELECT 1 FROM jobs "
                        "WHERE id=incidents.job_id AND hold_reason IS NULL))) "
                        "AND resolved_at IS NULL",
                        (time.time(), row["job_id"], file_id),
                    )

    def finish_jobs(self):
        with self.lock, self.db:
            changed = self.db.execute(
                """
                UPDATE jobs SET status=CASE WHEN EXISTS (
                    SELECT 1 FROM files WHERE job_id=jobs.id AND status='error'
                ) THEN 'error' ELSE 'completed' END, finished_at=?
                WHERE status IN ('running','queued') AND NOT EXISTS (
                    SELECT 1 FROM files WHERE job_id=jobs.id AND status IN ('queued','downloading')
                )
            """,
                (time.time(),),
            )
            if changed.rowcount:
                for row in self.db.execute(
                    "SELECT id FROM jobs WHERE status IN ('completed','error')"
                ):
                    self.estimates.reset(row[0])

    def tree(self, job_id, prefix="", offset=0, limit=100):
        """Aggregate one directory level; never send an entire large item to the browser."""
        if prefix:
            prefix = prefix.rstrip("/") + "/"
        groups = {}
        with self.lock:
            if not self.db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
                raise KeyError("Téléchargement introuvable.")
            rows = self.db.execute(
                "SELECT id,name,size,downloaded,speed,status,error,priority FROM files "
                "WHERE job_id=? AND substr(name,1,?)=? ORDER BY name",
                (job_id, len(prefix), prefix),
            )
            for row in rows:
                rest = row["name"][len(prefix) :]
                name, separator, _ = rest.partition("/")
                key = (bool(separator), name)
                if not separator:
                    groups[key] = {**dict(row), "name": name, "path": row["name"], "kind": "file"}
                else:
                    folder = groups.setdefault(
                        key,
                        {
                            "name": name,
                            "path": prefix + name,
                            "kind": "folder",
                            "size": 0,
                            "downloaded": 0,
                            "speed": 0,
                            "file_count": 0,
                            "completed_files": 0,
                            "failed_files": 0,
                            "active_files": 0,
                            "status": "queued",
                        },
                    )
                    folder["size"] += row["size"] or 0
                    folder["downloaded"] += row["downloaded"]
                    folder["speed"] += row["speed"]
                    folder["file_count"] += 1
                    folder["completed_files"] += row["status"] == "completed"
                    folder["failed_files"] += row["status"] == "error"
                    folder["active_files"] += row["status"] == "downloading"
        children = sorted(groups.values(), key=lambda r: (r["kind"] != "folder", r["name"].lower()))
        for row in children:
            if row["kind"] == "folder":
                row["status"] = (
                    "completed"
                    if row["completed_files"] == row["file_count"]
                    else "downloading"
                    if row["active_files"]
                    else "error"
                    if row["failed_files"]
                    else "queued"
                )
        return {
            "children": children[offset : offset + limit],
            "total": len(children),
            "prefix": prefix,
        }
