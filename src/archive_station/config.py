"""Persist user settings separately from deployment configuration."""

import json
import os
import re
import threading
from pathlib import Path

from .schedule import validate as validate_schedule

LANGUAGES = {
    "auto",
    "en",
    "fr",
    "es",
    "pt",
    "pt-BR",
    "de",
    "it",
    "pl",
    "nl",
    "tr",
    "id",
    "cs",
    "ro",
    "hu",
    "sv",
    "da",
    "nb",
    "fi",
    "ja",
    "ko",
    "zh-Hans",
    "zh-Hant",
    "ru",
    "th",
    "uk",
    "el",
    "vi",
}


def dsm_language():
    try:
        for line in Path("/etc/synoinfo.conf").read_text().splitlines():
            if line.startswith("language="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return ""


class Settings:
    def __init__(self, data_dir, download_dir, allowed_roots):
        self.path = Path(data_dir) / "settings.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.roots = [Path(root).resolve() for root in allowed_roots]
        self.lock = threading.RLock()
        self.values = {
            "download_dir": str(Path(download_dir).resolve()),
            "connections": 3,
            "speed_limit_kib": 0,
            "retries": 4,
            "verify_checksums": True,
            "notifications": True,
            "disk_reserve_mib": 1024,
            "schedule_enabled": False,
            "schedule_days": list(range(7)),
            "schedule_start": 0,
            "schedule_end": 0,
            "schedule_outside": "pause",
            "schedule_limit_kib": 1024,
            "language": "auto",
            "report_language": "auto",
        }
        if self.path.exists():
            self.values.update(json.loads(self.path.read_text()))

    def get(self):
        with self.lock:
            return dict(self.values)

    def directory(self, value, create=False):
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError("Le dossier doit être un chemin absolu.")
        path = Path(value)
        if any(part.is_symlink() for part in [path, *path.parents]):
            raise ValueError("Les liens symboliques ne sont pas autorisés.")
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in self.roots):
            raise ValueError("Choisissez un dossier dans un volume autorisé.")
        if create:
            path.mkdir(parents=True, exist_ok=True)
            if not os.access(path, os.W_OK | os.X_OK):
                raise ValueError(
                    "Dossier non accessible en écriture. Vérifiez les permissions DSM."
                )
        return path

    def update(self, values):
        if not isinstance(values, dict) or values.keys() - self.values.keys():
            raise ValueError("Paramètre inconnu.")
        with self.lock:
            new = {**self.values, **values}
            validate_schedule(new)
            for key, minimum, maximum in (
                ("connections", 1, 8),
                ("disk_reserve_mib", 0, 1_000_000_000),
                ("schedule_limit_kib", 1, 1_000_000),
                ("retries", 0, 10),
                ("speed_limit_kib", 0, 1_000_000),
            ):
                if type(new[key]) is not int or not minimum <= new[key] <= maximum:
                    raise ValueError(f"{key} doit être compris entre {minimum} et {maximum}.")
            if type(new["notifications"]) is not bool:
                raise ValueError("Invalid notification setting")
            if type(new["verify_checksums"]) is not bool:
                raise ValueError("Option de vérification invalide.")
            for key in ("language", "report_language"):
                if not isinstance(new[key], str) or new[key] not in LANGUAGES:
                    raise ValueError("Langue non prise en charge.")
            new["download_dir"] = str(self.directory(new["download_dir"], create=True))
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(new, indent=2))
            temporary.replace(self.path)
            self.values = new
        return self.get()

    def file_station_location(self, destination, identifier):
        root = self.directory(destination)
        target = self.directory(str(root / identifier))
        if not target.is_dir():
            target = root
        if not target.is_dir() or not os.access(target, os.R_OK | os.X_OK):
            raise PermissionError("Folder not readable")
        match = re.fullmatch(r"/volume[0-9]+(/.*)?", str(target))
        if not match:
            raise ValueError("Cette action nécessite DSM.")
        return {"path": str(target), "file_station_path": match[1] or "/"}

    def create_folder(self, parent, name):
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or name.startswith((".", "@"))
            or any(char in name for char in "/\\")
            or any(ord(char) < 32 for char in name)
            or len(name.encode("utf-8")) > 255
        ):
            raise ValueError(
                "Utilisez un nom de dossier sans / ni \\, "
                "sans espace au début ou à la fin, et ne commençant pas par . ou @."
            )
        directory = self.directory(parent)
        if not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
            raise PermissionError("Le dossier parent nécessite la lecture et l’écriture.")
        target = self.directory(str(directory / name))
        try:
            target.mkdir()
        except FileExistsError as error:
            raise ValueError("Un fichier ou dossier porte déjà ce nom.") from error
        return {"path": str(target)}

    def folders(self, value=None):
        def entry(path, name=None):
            return {
                "name": name or path.name,
                "path": str(path),
                "readable": os.access(path, os.R_OK | os.X_OK),
                "writable": os.access(path, os.W_OK | os.X_OK),
            }

        if not value:
            return {
                "path": "",
                "parent": None,
                "writable": False,
                "folders": [entry(p, str(p)) for p in self.roots],
            }
        path = self.directory(value)
        folders = []
        for child in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
            if child.name.startswith((".", "@")) or child.is_symlink():
                continue
            if child.is_dir():
                folders.append(entry(child))
        parent = str(path.parent) if path not in self.roots else ""
        return {
            "path": str(path),
            "parent": parent,
            "writable": os.access(path, os.W_OK | os.X_OK),
            "folders": folders,
        }
