#!/usr/bin/env python3
"""Stage the common DSM payload without a Python runtime or third-party wheels."""

import argparse
import json
import re
import shutil
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging/synology"


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


def stage(destination, version, package_id="ArchiveStation", python_relative="python/bin/python3"):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", package_id):
        raise ValueError("Invalid package ID")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-[1-9][0-9]*", version):
        raise ValueError("Invalid DSM package version")
    relative = PurePosixPath(python_relative)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
        or not re.fullmatch(r"[A-Za-z0-9_./-]+", python_relative)
    ):
        raise ValueError("Python must be a relative path inside the package")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        ROOT / "src/archive_station",
        destination / "app/archive_station",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )
    (destination / "app/archive_station/VERSION").write_text(version + "\n")
    shutil.copytree(
        PACKAGING / "ui",
        destination / "ui",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )
    # DSM loads the outer style.css globally; application styles stay in the iframe.
    shutil.copytree(ROOT / "src/archive_station/static", destination / "ui/web", dirs_exist_ok=True)
    shutil.copy2(ROOT / "LICENSE", destination / "LICENSE")
    notification_texts(destination / "ui")
    config_path = destination / "ui/config"
    config = json.loads(config_path.read_text())
    config[".url"]["com.archivestation.app"]["url"] = (
        f"/webman/3rdparty/{package_id}/web/index.html?v={version}"
    )
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    gateway = destination / "ui/gateway.cgi"
    template = gateway.read_text()
    assert template.startswith("#!@PYTHON@\n"), "Missing gateway interpreter template"
    gateway.write_text(
        template.replace("@PYTHON@", f"/var/packages/{package_id}/target/{relative}", 1)
    )
    gateway.chmod(0o755)
    html_path = destination / "ui/web/index.html"
    html = html_path.read_text()
    for asset in ["app.js", "i18n.js", "style.css", "icon.png"]:
        html = html.replace(f'"{asset}"', f'"{asset}?v={version}"')
    html_path.write_text(html)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--package-id", default="ArchiveStation")
    parser.add_argument("--python-relative", default="python/bin/python3")
    args = parser.parse_args()
    stage(args.destination, args.version, args.package_id, args.python_relative)
