#!/usr/bin/env python3
"""Build an offline-installable DSM 7 x86_64 package using verified runtime archives."""

import argparse
import base64
import gzip
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging/synology"


def check(path, expected):
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch: {path}")


def icon(size):
    return (PACKAGING / f"ui/images/archive_station_{size}.png").read_bytes()


def pack(source, destination, compressed=False):
    with destination.open("wb") as output:
        stream = (
            gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) if compressed else output
        )
        with tarfile.open(fileobj=stream, mode="w", format=tarfile.GNU_FORMAT) as tar:
            for path in sorted(source.rglob("*")):
                info = tar.gettarinfo(str(path), str(path.relative_to(source)))
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mtime = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
                if info.isfile():
                    with path.open("rb") as data:
                        tar.addfile(info, data)
                else:
                    tar.addfile(info)
        if compressed:
            stream.close()


def notification_texts(ui):
    """DSM requires package i18n keys, preloaded even when its window is closed."""
    codes = {
        "en": "enu",
        "fr": "fre",
        "de": "ger",
        "es": "spn",
        "it": "ita",
        "pt": "ptg",
        "pt-BR": "ptb",
        "nl": "nld",
        "da": "dan",
        "sv": "sve",
        "nb": "nor",
        "fi": "fin",
        "pl": "plk",
        "cs": "csy",
        "hu": "hun",
        "tr": "trk",
        "ru": "rus",
        "ja": "jpn",
        "ko": "krn",
        "zh-Hans": "chs",
        "zh-Hant": "cht",
        "th": "tha",
        "ro": "rom",
        "uk": "ukr",
        "el": "ell",
        "vi": "vit",
        "id": "ind",
    }
    keys = {
        "completed": "Téléchargements terminés",
        "error": "Téléchargements à vérifier",
        "disk": "Espace disque insuffisant.",
    }
    for code, dsm in codes.items():
        catalog = json.loads(
            (ROOT / "src/archive_station/static/locales" / f"{code}.json").read_text()
        )
        path = ui / "texts" / dsm / "strings"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["[notifications]", 'title="Archive Station"']
        lines += [
            f"{key}={json.dumps(catalog[value], ensure_ascii=False)}" for key, value in keys.items()
        ]
        path.write_text("\n".join(lines) + "\n")


def build(runtime, wheel, output, version):
    lock = json.loads((PACKAGING / "runtime-lock.json").read_text())
    check(runtime, lock["python"]["sha256"])
    check(wheel, lock["waitress"]["sha256"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="archive-station-build-") as temporary:
        stage = Path(temporary)
        payload, package = stage / "payload", stage / "package"
        payload.mkdir()
        package.mkdir()
        with tarfile.open(runtime) as archive:
            archive.extractall(
                payload,
                members=[
                    m
                    for m in archive.getmembers()
                    if not m.name.startswith("python/share/terminfo/")
                ],
                filter="data",
            )
        # Remove build-only caches while retaining all runtime libraries and license texts.
        for directory in list(payload.rglob("__pycache__")):
            shutil.rmtree(directory)
        shutil.copytree(
            ROOT / "src/archive_station",
            payload / "app/archive_station",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(payload / "vendor")
        shutil.copytree(PACKAGING / "runtime-licenses", payload / "python/licenses")
        shutil.copytree(
            PACKAGING / "ui", payload / "ui", ignore=shutil.ignore_patterns("__pycache__")
        )
        # DSM automatically loads ui/style.css into its desktop. Keep application
        # resources below web/, exclusively loaded by the application iframe.
        shutil.copytree(ROOT / "src/archive_station/static", payload / "ui/web")
        notification_texts(payload / "ui")
        config_path = payload / "ui/config"
        config_path.write_text(config_path.read_text().replace("@VERSION@", version))
        html_path = payload / "ui/web/index.html"
        html = html_path.read_text()
        for asset in ["app.js", "i18n.js", "style.css", "icon.png"]:
            html = html.replace(f'"{asset}"', f'"{asset}?v={version}"')
        html_path.write_text(html)
        (payload / "ui/images").mkdir(exist_ok=True)
        for size in [16, 24, 32, 48, 64, 72, 256]:
            (payload / f"ui/images/archive_station_{size}.png").write_bytes(icon(size))
        for name in ["conf", "scripts", "WIZARD_UIFILES"]:
            shutil.copytree(PACKAGING / name, package / name)
        for path in (package / "scripts").iterdir():
            path.chmod(0o755)
        (payload / "ui/gateway.cgi").chmod(0o755)
        shutil.copy2(ROOT / "LICENSE", package / "LICENSE")
        shutil.copy2(ROOT / "LICENSE", payload / "LICENSE")
        shutil.copy2(PACKAGING / "THIRD_PARTY_NOTICES.txt", payload / "THIRD_PARTY_NOTICES.txt")
        size = sum(p.stat().st_size for p in payload.rglob("*") if p.is_file())
        info = (PACKAGING / "INFO.template").read_text().replace("@VERSION@", version)
        info = info.replace("@EXTRACTSIZE@", str((size + 1023) // 1024))
        for filename, size, field in [
            ("PACKAGE_ICON.PNG", 64, "package_icon"),
            ("PACKAGE_ICON_256.PNG", 256, "package_icon_256"),
        ]:
            data = icon(size)
            (package / filename).write_bytes(data)
            info += f'{field}="{base64.b64encode(data).decode()}"\n'
        (package / "INFO").write_text(info)
        pack(payload, package / "package.tgz", compressed=True)
        pack(package, output)
    output.with_suffix(".spk.sha256").write_text(
        f"{hashlib.sha256(output.read_bytes()).hexdigest()}  {output.name}\n"
    )
    print(f"Built {output} ({output.stat().st_size / 1024**2:.1f} MiB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--waitress-wheel", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "dist/ArchiveStation-0.2.0-4-x86_64.spk"
    )
    parser.add_argument("--version", default="0.2.0-4")
    args = parser.parse_args()
    build(args.runtime, args.waitress_wheel, args.output, args.version)
