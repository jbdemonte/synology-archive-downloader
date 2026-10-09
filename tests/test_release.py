import io
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.release import (
    export_source,
    git,
    preflight,
    publication_guide,
    release_assets,
    validate_package,
    verify_bundle,
    write_checksums,
)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.version = "0.2.0-6"
        self.write(".gitignore", ".env\n.local/\ndist/\n")
        self.write("src/archive_station/__init__.py", '__version__ = "0.2.0"\n')
        self.write("docs/releases/0.2.0-6.md", "# Archive Station 0.2.0-6\n\nRelease notes.\n")
        self.write("src/archive_station/static/app.js", "// Committed application\n")
        self.write("src/archive_station/static/style.css", "body {}\n")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.commit()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def commit(self):
        git(self.root, "add", ".")
        git(
            self.root,
            "-c",
            "user.name=Release Test",
            "-c",
            "user.email=release@example.invalid",
            "commit",
            "-qm",
            "Fixture",
        )
        return git(self.root, "rev-parse", "HEAD")

    def test_dirty_and_untracked_sources_cannot_be_released(self):
        self.assertEqual(preflight(self.root, self.version), git(self.root, "rev-parse", "HEAD"))
        path = self.write("untracked.py", "print('not committed')\n")
        with self.assertRaisesRegex(ValueError, "Commit or stash"):
            preflight(self.root, self.version)
        path.unlink()
        self.write("src/archive_station/static/app.js", "// Unsaved source\n")
        with self.assertRaisesRegex(ValueError, "Commit or stash"):
            preflight(self.root, self.version)

    def test_version_notes_and_existing_tag_must_match_source(self):
        for version in ["../outside", "v0.2.0-6", "0.2.0", "0.2.0-0", "0.2.0-6;id"]:
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "DSM package"):
                preflight(self.root, version)
        with self.assertRaisesRegex(ValueError, "__version__"):
            preflight(self.root, "0.3.0-1")
        with self.assertRaisesRegex(ValueError, "release notes"):
            preflight(self.root, "0.2.0-7")
        git(self.root, "tag", "v0.2.0-6")
        preflight(self.root, self.version)
        self.write("README.md", "New content\n")
        self.commit()
        with self.assertRaisesRegex(ValueError, "another commit"):
            preflight(self.root, self.version)

    def test_source_export_is_repeatable_and_excludes_ignored_private_files(self):
        self.write(".env", "PRIVATE=do-not-publish\n")
        self.write("src/archive_station/.env", "PRIVATE=do-not-publish\n")
        self.write(".local/private.md", "NAS configuration\n")
        self.write("dist/old.spk", "Old package\n")
        commit = preflight(self.root, self.version)
        output = self.root / "dist/source.tar.gz"
        prefix = export_source(self.root, commit, self.version, output)
        original = output.read_bytes()
        export_source(self.root, commit, self.version, output)
        self.assertEqual(original, output.read_bytes())
        with tarfile.open(output) as archive:
            names = archive.getnames()
            self.assertIn(prefix + "/src/archive_station/static/app.js", names)
            self.assertFalse(
                any(".env" in name or ".local" in name or "/dist" in name for name in names)
            )
            self.assertEqual(
                archive.extractfile(prefix + "/src/archive_station/static/app.js").read(),
                b"// Committed application\n",
            )

    def bundle(self):
        directory = self.root / "dist"
        for name in release_assets(self.version):
            if name != "SHA256SUMS":
                self.write(f"dist/{name}", name)
        self.write("dist/PUBLISH.md", publication_guide(self.version, "a" * 40))
        write_checksums(directory, self.version)
        return directory

    def test_bundle_checksums_detect_corruption_extra_files_and_unsafe_names(self):
        directory = self.bundle()
        verify_bundle(directory, self.version)
        spk = f"ArchiveStation-{self.version}-x86_64.spk"
        self.write(f"dist/{spk}", "corrupted")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            verify_bundle(directory, self.version)
        self.write(f"dist/{spk}", spk)
        self.write("dist/unexpected.txt", "unexpected")
        with self.assertRaisesRegex(ValueError, "unexpected files"):
            verify_bundle(directory, self.version)
        self.write("dist/SHA256SUMS", "0" * 64 + "  ../outside\n")
        with self.assertRaisesRegex(ValueError, "Invalid release"):
            verify_bundle(directory, self.version)

    def test_downloaded_assets_verify_without_the_local_publication_guide(self):
        directory = self.bundle()
        guide = (directory / "PUBLISH.md").read_text()
        shell = guide.split("```sh\n", 1)[1].split("```", 1)[0]
        command = "gh release create " + shell.split("gh release create ", 1)[1]
        arguments = shlex.split(command.replace("\\\n", ""))
        paths = [argument for argument in arguments if argument.startswith("dist/releases/")]
        names = {Path(path).name for path in paths}
        self.assertEqual(
            names,
            {
                "ArchiveStation-0.2.0-6-x86_64.spk",
                "ArchiveStation-0.2.0-6-x86_64.spk.sha256",
                "ArchiveStation-0.2.0-6-source.tar.gz",
                "SHA256SUMS",
                "BUILD-INFO.txt",
                "INSTALL.md",
                "RELEASE_NOTES.md",
            },
        )
        downloaded = self.root / "downloaded"
        downloaded.mkdir()
        for name in names:
            shutil.copyfile(directory / name, downloaded / name)
        verify_bundle(downloaded, self.version)
        manifest = (downloaded / "SHA256SUMS").read_text()
        self.assertNotIn("PUBLISH.md", manifest)
        self.assertEqual(
            {line.split("  ", 1)[1] for line in manifest.splitlines()}, names - {"SHA256SUMS"}
        )
        # Maintainer notes can be edited without changing any public checksum.
        self.write("dist/PUBLISH.md", "Local publication checklist\n")
        verify_bundle(directory, self.version)

    def test_manifest_must_cover_all_public_assets_exactly_once(self):
        directory = self.bundle()
        lines = (directory / "SHA256SUMS").read_text().splitlines(keepends=True)
        # A missing asset must not be accepted even when its manifest entry is removed too.
        missing = lines[0].split("  ", 1)[1].strip()
        (directory / missing).unlink()
        self.write("dist/SHA256SUMS", "".join(lines[1:]))
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            verify_bundle(directory, self.version)
        self.write(f"dist/{missing}", missing)
        self.write("dist/SHA256SUMS", "".join(lines + lines[:1]))
        with self.assertRaisesRegex(ValueError, "Invalid release"):
            verify_bundle(directory, self.version)
        self.write("dist/SHA256SUMS", "".join(lines) + "0" * 64 + "  PUBLISH.md\n")
        with self.assertRaisesRegex(ValueError, "Invalid release"):
            verify_bundle(directory, self.version)

    def package(
        self, version="0.2.0-6", script=b"// Committed application\n", app_version="0.2.0-6"
    ):
        def add(archive, name, content):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))

        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            add(archive, "app/archive_station/VERSION", app_version.encode())
            add(archive, "app/archive_station/static/app.js", script)
            add(archive, "ui/web/app.js", script)
            add(archive, "ui/web/style.css", b"body {}\n")
        path = self.root / "test.spk"
        with tarfile.open(path, mode="w") as archive:
            add(archive, "INFO", f'version="{version}"\narch="x86_64"\n'.encode())
            add(archive, "package.tgz", payload.getvalue())
            for name in ["LICENSE", "conf/privilege", "conf/resource", "scripts/start-stop-status"]:
                add(archive, name, b"Required content")
        return path

    def test_package_validation_detects_wrong_version_and_stale_interface(self):
        validate_package(self.package(), self.version, self.root)
        with self.assertRaisesRegex(ValueError, "version or architecture"):
            validate_package(self.package(version="0.2.0-5"), self.version, self.root)
        with self.assertRaisesRegex(ValueError, "source mismatch"):
            validate_package(self.package(script=b"Old script"), self.version, self.root)
        with self.assertRaisesRegex(ValueError, "update-check version"):
            validate_package(self.package(app_version="0.2.0"), self.version, self.root)

    def test_publication_instructions_pin_the_tag_and_attach_public_assets(self):
        text = publication_guide(self.version, "a" * 40)
        self.assertIn("git tag -a v0.2.0-6 " + "a" * 40, text)
        self.assertIn("--verify-tag --draft --prerelease", text)
        self.assertIn("--notes-file dist/releases/0.2.0-6/RELEASE_NOTES.md", text)
        self.assertIn("dist/releases/0.2.0-6/ArchiveStation-0.2.0-6-x86_64.spk", text)
        self.assertNotIn("\n+", text)


if __name__ == "__main__":
    unittest.main()
