"""Isolated UI fixture server. No external requests or real downloads."""

import argparse
import time
from pathlib import Path

from waitress import serve

from archive_station.archive import ArchiveClient
from archive_station.config import Settings
from archive_station.store import Store
from archive_station.web import WebApp

parser = argparse.ArgumentParser()
parser.add_argument("--data-dir", required=True)
args = parser.parse_args()
root = Path(args.data_dir).resolve()
root.mkdir(parents=True, exist_ok=True)
for name in ["state.sqlite3", "state.sqlite3-wal", "state.sqlite3-shm"]:
    (root / name).unlink(missing_ok=True)
(root / "downloads").mkdir(exist_ok=True)
client = ArchiveClient()

for identifier in ["demo-one", "demo-two"]:
    client.cache[identifier] = (
        time.monotonic(),
        {
            "metadata": {"title": identifier},
            "files": [
                {
                    "name": f"roms/game-{i:03}.zip",
                    "size": str((i + 1) * 123456),
                    "source": "original",
                }
                for i in range(150)
            ]
            + [{"name": "readme.txt", "size": "1042", "source": "original"}],
        },
    )
store = Store(root / "state.sqlite3")
settings = Settings(root / "data", root / "downloads", [root])
app = WebApp(store, client, settings, root / "data", no_auth=True)
serve(app, host="127.0.0.1", port=8275, threads=4)
