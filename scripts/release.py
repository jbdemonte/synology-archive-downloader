#!/usr/bin/env python3
"""Prepare a tested, traceable GitHub release bundle without publishing it."""

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "jbdemonte/synology-archive-downloader"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def preflight(root, version):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-[1-9][0-9]*", version):
        raise ValueError("Use a DSM package version such as 0.2.0-6 (without the v prefix).")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Commit or stash changes and untracked files before making a release.")
    commit = git(root, "rev-parse", "HEAD")
    tag = f"v{version}"
    if git(root, "tag", "--list", tag) and git(root, "rev-list", "-n", "1", tag) != commit:
        raise ValueError(f"{tag} already points to another commit; choose a new version.")
    source = (root / "src/archive_station/__init__.py").read_text()
    match = re.search(r'__version__ = "([^"]+)"', source)
    if not match or match[1] != version.split("-")[0]:
        raise ValueError("The package version must match archive_station.__version__.")
    notes = root / "docs/releases" / f"{version}.md"
    if not notes.is_file() or not notes.read_text().startswith(f"# Archive Station {version}\n"):
        raise ValueError(f"Add release notes with the matching title in {notes.relative_to(root)}.")
    return commit


def export_source(root, commit, version, output):
    """Git supplies only committed files, never local downloads or credentials."""
    prefix = f"ArchiveStation-{version}-source/"
    with tempfile.TemporaryFile() as raw:
        subprocess.run(
            ["git", "-C", str(root), "archive", "--format=tar", f"--prefix={prefix}", commit],
            stdout=raw,
            check=True,
        )
        raw.seek(0)
        with output.open("wb") as target:
            with gzip.GzipFile(filename="", fileobj=target, mode="wb", mtime=0) as compressed:
                shutil.copyfileobj(raw, compressed)
    return prefix.rstrip("/")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_checksums(directory):
    files = sorted(path for path in directory.iterdir() if path.name != "SHA256SUMS")
    (directory / "SHA256SUMS").write_text(
        "".join(f"{digest(path)}  {path.name}\n" for path in files)
    )


def verify_bundle(directory):
    expected = set()
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        checksum, name = line.split("  ", 1)
        if Path(name).name != name or not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise ValueError("Invalid release checksum manifest.")
        path = directory / name
        if path.is_symlink() or not path.is_file() or digest(path) != checksum:
            raise ValueError(f"Release checksum mismatch: {name}")
        expected.add(name)
    if not expected or expected != {p.name for p in directory.iterdir()} - {"SHA256SUMS"}:
        raise ValueError("Release bundle contains missing or unexpected files.")


def validate_package(path, version, source):
    with tarfile.open(path) as package:
        info = package.extractfile("INFO").read().decode()
        if f'version="{version}"' not in info or 'arch="x86_64"' not in info:
            raise ValueError("Built package has an unexpected version or architecture.")
        for name in ["LICENSE", "conf/privilege", "conf/resource", "scripts/start-stop-status"]:
            if not package.extractfile(name).read():
                raise ValueError(f"Missing package content: {name}")
        with tarfile.open(fileobj=package.extractfile("package.tgz"), mode="r|gz") as payload:
            installed_version = None
            required = {
                "app/archive_station/static/app.js": source / "src/archive_station/static/app.js",
                "ui/web/app.js": source / "src/archive_station/static/app.js",
                "ui/web/style.css": source / "src/archive_station/static/style.css",
            }
            for member in payload:
                if member.name == "app/archive_station/VERSION":
                    installed_version = payload.extractfile(member).read().decode().strip()
                if member.name in required:
                    if payload.extractfile(member).read() != required.pop(member.name).read_bytes():
                        raise ValueError(f"Package source mismatch: {member.name}")
            if required:
                raise ValueError("The package is missing application assets.")
            if installed_version != version:
                raise ValueError("The installed update-check version differs from the package.")


def browser_environment(root):
    env = os.environ.copy()
    if env.get("CHROMIUM_PATH"):
        if not Path(env["CHROMIUM_PATH"]).is_file():
            raise ValueError("CHROMIUM_PATH does not point to an executable file.")
        return env
    default = subprocess.check_output(
        ["node", "-e", "process.stdout.write(require('playwright').chromium.executablePath())"],
        cwd=root,
        text=True,
    )
    if Path(default).is_file():
        return env
    # Reuse a full Chromium installed by another Playwright version on this Mac.
    cache = Path.home() / "Library/Caches/ms-playwright"
    candidates = list(cache.glob("chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"))
    candidates += list(cache.glob("chromium-*/chrome-mac-*/Chromium.app/Contents/MacOS/Chromium"))
    if candidates:
        env["CHROMIUM_PATH"] = str(max(candidates, key=lambda p: p.stat().st_mtime))
        print("Using installed Chromium:", env["CHROMIUM_PATH"], flush=True)
        return env
    raise ValueError("Install Chromium with npx playwright install chromium, or set CHROMIUM_PATH.")


def publication_guide(version, commit):
    directory = f"dist/releases/{version}"
    spk = f"ArchiveStation-{version}-x86_64.spk"
    assets = [spk, spk + ".sha256", f"ArchiveStation-{version}-source.tar.gz", "SHA256SUMS"]
    assets += ["BUILD-INFO.txt", "INSTALL.md", "RELEASE_NOTES.md", "PUBLISH.md"]
    uploads = " \\\n  ".join(f"{directory}/{name}" for name in assets)
    return f"""# Publish Archive Station {version}

This bundle was prepared locally. These commands perform the GitHub publication steps.
Run them from the repository root after reviewing the release notes and assets.

The repository must be public for the community to download the release. Review the
repository and its Git history before changing visibility in GitHub repository Settings.
The source archive contains the exact committed tree, including its README and screenshots.

## Create the tag and draft

Push the release commit and an annotated tag without moving an existing tag:

```sh
git push origin main
git tag -a v{version} {commit} -m "Archive Station {version}"
git push origin refs/tags/v{version}
gh release create v{version} \\
  --repo {REPOSITORY} --verify-tag --draft --prerelease \\
  --title "Archive Station {version} (community preview)" \\
  --notes-file {directory}/RELEASE_NOTES.md \\
  {uploads}
```

If the tag already exists at the recorded commit, reuse it. Never force-update a published tag.
Review the draft in GitHub Releases, then click **Publish release**. The initial release
is a community preview: only DS918+ with DSM 7.1.1 has been tested on hardware.

## SynoCommunity

GitHub releases can be installed manually through DSM Package Center. They do not
automatically appear at packages.synocommunity.com. Inclusion requires a packaging
contribution to SynoCommunity/spksrc and maintainer acceptance; see docs/RELEASING.md.
"""


def prepare(root, version):
    commit = preflight(root, version)
    destination = root / "dist/releases" / version
    if destination.exists():
        verify_bundle(destination)
        if f"Commit: {commit}\n" not in (destination / "BUILD-INFO.txt").read_text():
            raise ValueError(
                "A bundle for another commit already uses this version. Use a new version."
            )
        print(f"Verified existing release bundle: {destination}")
        return destination
    for path in [".venv/bin/python", ".venv/bin/ruff", "node_modules/playwright"]:
        if not (root / path).exists():
            raise ValueError("Development dependencies are missing. Run make dev-deps first.")
    env = browser_environment(root)
    # Nested make runs must not inherit an outer parallel jobserver or variable
    # overrides: validation, tests, then packaging are deliberately sequential.
    for name in ["MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "SOURCE_DATE_EPOCH"]:
        env.pop(name, None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".release-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        bundle = stage / "bundle"
        bundle.mkdir()
        source_archive = bundle / f"ArchiveStation-{version}-source.tar.gz"
        prefix = export_source(root, commit, version, source_archive)
        with tarfile.open(source_archive) as archive:
            archive.extractall(stage, filter="data")
        source = stage / prefix
        (source / ".venv").symlink_to(root / ".venv", target_is_directory=True)
        (source / "node_modules").symlink_to(root / "node_modules", target_is_directory=True)
        (root / "build/cache").mkdir(parents=True, exist_ok=True)
        (source / "build").mkdir()
        (source / "build/cache").symlink_to(root / "build/cache", target_is_directory=True)
        for target in ["check", "test-ui", "build"]:
            print(f"\nRelease {version}: make {target} from commit {commit[:12]}", flush=True)
            subprocess.run(
                ["make", target, f"VERSION={version}", f"PYTHON={sys.executable}"],
                cwd=source,
                env=env,
                check=True,
            )
        spk = f"ArchiveStation-{version}-x86_64.spk"
        validate_package(source / "dist" / spk, version, source)
        for name in [spk, spk + ".sha256"]:
            shutil.copy2(source / "dist" / name, bundle / name)
        notes = (source / "docs/releases" / f"{version}.md").read_text()
        notes += f"\nBuilt from commit `{commit}`. See `BUILD-INFO.txt` and `SHA256SUMS`.\n"
        (bundle / "RELEASE_NOTES.md").write_text(notes)
        install = (source / "docs/releases/INSTALL.template.md").read_text()
        (bundle / "INSTALL.md").write_text(install.replace("@VERSION@", version))
        (bundle / "PUBLISH.md").write_text(publication_guide(version, commit))
        lock = json.loads((source / "packaging/synology/runtime-lock.json").read_text())
        (bundle / "BUILD-INFO.txt").write_text(
            f"Archive Station release bundle\nVersion: {version}\nCommit: {commit}\n"
            f"Tag: v{version}\nArchitecture: x86_64\n"
            f"Commit date: {git(root, 'show', '-s', '--format=%cI', commit)}\n"
            f"Python runtime SHA-256: {lock['python']['sha256']}\n"
            f"Waitress wheel SHA-256: {lock['waitress']['sha256']}\n"
            "Checks passed: make check; make test-ui; make build; package content validation\n"
            "Hardware validation is separate; see RELEASE_NOTES.md.\n"
        )
        write_checksums(bundle)
        verify_bundle(bundle)
        if preflight(root, version) != commit:
            raise ValueError(
                "The repository changed during release preparation; rerun make release."
            )
        bundle.rename(destination)
    print(f"\nRelease ready: {destination}\nPublication instructions: {destination / 'PUBLISH.md'}")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 12):
            raise ValueError("Release preparation requires Python 3.12 or newer.")
        prepare(ROOT, args.version)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release failed: {error}\n")
