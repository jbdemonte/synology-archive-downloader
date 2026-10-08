"""Internet Archive metadata and validated download locations."""

import fnmatch
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z")
USER_AGENT = "ArchiveStation/0.2.0 (personal archive downloader)"


def parse_identifier(value):
    if not isinstance(value, str):
        raise ValueError("Saisissez une URL Archive.org.")
    value = value.strip()
    if "://" in value:
        url = urlsplit(value)
        if (
            url.scheme not in {"https", "http"}
            or url.hostname not in {"archive.org", "www.archive.org"}
            or url.username
            or url.port
        ):
            raise ValueError("L’URL doit appartenir à archive.org.")
        parts = url.path.strip("/").split("/")
        if len(parts) < 2 or parts[0] not in {"details", "download", "metadata"}:
            raise ValueError("Utilisez une URL /details/ ou /download/ d’un élément Archive.org.")
        value = unquote(parts[1])
    if not IDENTIFIER.fullmatch(value) or value in {".", ".."}:
        raise ValueError("Identifiant Archive.org invalide.")
    return value


def validate_name(name):
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or any(ord(c) < 32 for c in name)
        or any(p in {"", ".", ".."} for p in name.split("/"))
        or any(len(p.encode()) > 255 for p in name.split("/"))
    ):
        raise ValueError(f"Chemin de fichier non autorisé : {name!r}")
    return name


def safe_path(root, relative):
    """Reject traversal and existing symlinks, including links inside the destination."""
    validate_name(relative)
    root = Path(root).resolve()
    current = root
    for part in relative.split("/"):
        current /= part
        if current.is_symlink():
            raise ValueError("Un lien symbolique se trouve dans le chemin de destination.")
    if not current.resolve().is_relative_to(root):
        raise ValueError("Le chemin sort du dossier de destination.")
    return current


class ArchiveRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        host = url.hostname or ""
        if (
            url.scheme != "https"
            or url.username
            or url.port not in {None, 443}
            or not (host == "archive.org" or host.endswith(".archive.org"))
        ):
            raise ValueError("Redirection hors d’Archive.org refusée.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ArchiveClient:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}

    def open(self, identifier, name, offset=0):
        url = f"https://archive.org/download/{quote(identifier)}/{quote(name, safe='/')}"
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        return build_opener(ArchiveRedirects()).open(Request(url, headers=headers), timeout=30)

    def manifest(self, identifier, mode="all", pattern="", refresh=False):
        if mode not in {"all", "original"}:
            raise ValueError("Sélection de fichiers invalide.")
        if not isinstance(pattern, str) or len(pattern) > 200:
            raise ValueError("Filtre trop long.")
        with self.lock:
            cached = self.cache.get(identifier)
        if cached and not refresh and time.monotonic() - cached[0] < 900:
            payload = cached[1]
        else:
            request = Request(
                f"https://archive.org/metadata/{identifier}", headers={"User-Agent": USER_AGENT}
            )
            with build_opener(ArchiveRedirects()).open(request, timeout=45) as response:
                raw = response.read(64 * 1024 * 1024 + 1)
            if len(raw) > 64 * 1024 * 1024:
                raise ValueError("La liste des fichiers dépasse la limite de 64 Mo.")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not payload.get("files"):
                raise ValueError("Élément introuvable, privé ou sans fichiers téléchargeables.")
            with self.lock:
                if len(self.cache) >= 4:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[identifier] = (time.monotonic(), payload)
        files, seen, private = [], set(), 0
        for item in payload["files"]:
            if item.get("private") in {True, "true", "1"}:
                private += 1
                continue
            name = validate_name(item.get("name"))
            if mode == "original" and item.get("source") != "original":
                continue
            if pattern and not fnmatch.fnmatchcase(name, pattern):
                continue
            if name in seen:
                continue
            seen.add(name)
            size = int(item["size"]) if item.get("size") is not None else None
            if size is not None and size < 0:
                raise ValueError("Taille de fichier invalide dans les métadonnées.")
            # The files.xml 'summation' checksum describes its entries, not its own bytes.
            algorithm, digest = None, None
            if not item.get("summation"):
                for key, length in (("sha1", 40), ("md5", 32)):
                    value = item.get(key, "")
                    if isinstance(value, str) and re.fullmatch(rf"[a-fA-F0-9]{{{length}}}", value):
                        algorithm, digest = key, value.lower()
                        break
            files.append({"name": name, "size": size, "algorithm": algorithm, "digest": digest})
        if not files:
            raise ValueError("Aucun fichier public ne correspond à cette sélection.")
        # Reject file/directory collisions before starting any transfer.
        for name in seen:
            parts = name.split("/")
            if any("/".join(parts[:i]) in seen for i in range(1, len(parts))):
                raise ValueError("La liste contient un conflit entre fichier et dossier.")
        title = payload.get("metadata", {}).get("title", identifier)
        return {
            "identifier": identifier,
            "title": str(title),
            "files": files,
            "total_size": sum(f["size"] or 0 for f in files),
            "unknown_sizes": sum(f["size"] is None for f in files),
            "private_files": private,
        }
