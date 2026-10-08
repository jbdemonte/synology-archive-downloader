#!/usr/bin/env python3
"""Download pinned SPK inputs and verify their SHA-256 before publishing the cache."""

import hashlib
import json
import shutil
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
lock = json.loads((ROOT / "packaging/synology/runtime-lock.json").read_text())
cache = ROOT / "build/cache"
cache.mkdir(parents=True, exist_ok=True)
for key, filename in [
    ("python", "python-runtime.tar.gz"),
    ("waitress", lock["waitress"]["filename"]),
]:
    entry = lock[key]
    target = cache / filename
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == entry["sha256"]:
        print(f"Verified cached {filename}")
        continue
    temporary = target.with_suffix(".download")
    try:
        print(f"Downloading {filename}…", flush=True)
        with urlopen(entry["url"], timeout=120) as source, temporary.open("wb") as output:
            shutil.copyfileobj(source, output)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Checksum mismatch: {filename}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
