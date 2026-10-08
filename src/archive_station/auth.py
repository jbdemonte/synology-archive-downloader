"""Single-administrator login for the independent NAS application."""

import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path


class Auth:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "auth.json"
        self.sessions = {}
        self.failures = {}
        self.lock = threading.RLock()

    def set_password(self, password):
        if not isinstance(password, str) or len(password) < 12:
            raise ValueError("Le mot de passe doit contenir au moins 12 caractères.")
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000).hex()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"salt": salt, "digest": digest}))
        temporary.chmod(0o600)
        temporary.replace(self.path)
        with self.lock:
            self.sessions.clear()

    def login(self, password, address):
        if not isinstance(password, str) or len(password) > 1024:
            return None
        with self.lock:
            now = time.monotonic()
            self.failures = {k: v for k, v in self.failures.items() if now - v[1] < 60}
            tries, _ = self.failures.get(address, (0, now))
            if tries >= 5 or not self.path.exists():
                return None
            data = json.loads(self.path.read_text())
            digest = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), data["salt"].encode(), 600_000
            ).hex()
            if not hmac.compare_digest(digest, data["digest"]):
                self.failures[address] = (tries + 1, now)
                return None
            self.failures.pop(address, None)
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            if len(self.sessions) >= 64:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + 86400
            return token

    def valid(self, token):
        with self.lock:
            return self.sessions.get(token, 0) > time.monotonic()

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)
