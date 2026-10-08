"""Capacity estimates and a configurable free-space reserve."""

import os
import shutil


def capacity(store, settings, destination, required):
    if type(required) is not int or not 0 <= required <= 2**63 - 1:
        raise ValueError("Invalid size")
    path = settings.directory(destination)
    while not path.exists():
        path = path.parent
    disk = shutil.disk_usage(path)
    device = path.stat().st_dev
    committed = 0
    with store.lock:
        rows = store.db.execute(
            "SELECT j.destination, SUM(MAX(COALESCE(f.size,0)-f.downloaded,0)) AS remaining "
            "FROM jobs j JOIN files f ON j.id=f.job_id WHERE j.status IN ('queued','running') "
            "AND f.status IN ('queued','downloading') GROUP BY j.destination"
        ).fetchall()
    for row in rows:
        try:
            if os.stat(row["destination"]).st_dev == device:
                committed += row["remaining"] or 0
        except OSError:
            pass
    reserve = settings.get()["disk_reserve_mib"] * 1024**2
    available = max(0, disk.free - reserve - committed)
    return {
        "free": disk.free,
        "reserve": reserve,
        "committed": committed,
        "required": required,
        "available": available,
        "fits": required <= available,
    }
