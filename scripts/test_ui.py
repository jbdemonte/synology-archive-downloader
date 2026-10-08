#!/usr/bin/env python3
"""Run UI smoke tests with an isolated fixture server, then remove test state."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="archive-station-ui-") as data:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "tests/serve_ui.py"), "--data-dir", data], env=env
    )
    try:
        for _ in range(50):
            if server.poll() is not None:
                raise RuntimeError("Fixture server failed to start; is port 8275 already in use?")
            try:
                with urlopen("http://127.0.0.1:8275/health", timeout=1):
                    break
            except URLError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Fixture server did not become ready")
        command = (
            ["node", "scripts/screenshots.mjs"]
            if "--screenshots" in sys.argv
            else ["npm", "run", "test:ui"]
        )
        subprocess.run(command, cwd=ROOT, env=env, check=True)
    finally:
        server.terminate()
        server.wait(timeout=10)
