from pathlib import Path
from unittest.mock import patch

from test_downloads import Base


class LocationTests(Base):
    def test_file_station_uses_share_path_and_preserves_unicode(self):
        with (
            patch.object(self.settings, "directory", side_effect=lambda v: Path(v)),
            patch("pathlib.Path.is_dir", return_value=True),
            patch("archive_station.config.os.access", return_value=True),
        ):
            result = self.settings.file_station_location("/volume2/Download/Jeux & été", "demo")
        self.assertEqual(result["file_station_path"], "/Download/Jeux & été/demo")

    def test_missing_item_opens_parent_and_local_install_is_rejected(self):
        with (
            patch.object(self.settings, "directory", side_effect=lambda v: Path(v)),
            patch("pathlib.Path.is_dir", side_effect=[False, True]),
            patch("archive_station.config.os.access", return_value=True),
        ):
            result = self.settings.file_station_location("/volume1/Download", "demo")
        self.assertEqual(result["file_station_path"], "/Download")
        self.downloads.mkdir()
        with self.assertRaises(ValueError):
            self.settings.file_station_location(str(self.downloads), "demo")
