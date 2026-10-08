"""Compare fresh metadata without altering a task until its selection is approved."""


def difference(store, job_id, manifest):
    with store.lock:
        job = store.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise KeyError("Téléchargement introuvable.")
        old = {
            r["name"]: dict(r)
            for r in store.db.execute("SELECT * FROM files WHERE job_id=?", (job_id,))
        }
        known = {
            r[0] for r in store.db.execute("SELECT name FROM known_files WHERE job_id=?", (job_id,))
        }
    candidates = []
    remote_names = set()
    for file in manifest["files"]:
        name = file["name"]
        remote_names.add(name)
        previous = old.get(name)
        if previous:
            changed = (file["size"] is not None and previous["size"] != file["size"]) or (
                file["digest"]
                and (
                    previous["digest"] != file["digest"]
                    or previous["algorithm"] != file["algorithm"]
                )
            )
            if changed:
                candidates.append({**file, "change": "changed"})
        elif name not in known:
            candidates.append({**file, "change": "new"})
    return {
        **manifest,
        "files": candidates,
        "revision": job["manifest_revision"],
        "added_count": sum(f["change"] == "new" for f in candidates),
        "changed_count": sum(f["change"] == "changed" for f in candidates),
        "absent_count": len(old.keys() - remote_names),
    }
