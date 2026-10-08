import json
import re
import unittest
from html import unescape
from pathlib import Path

from archive_station.config import LANGUAGES

STATIC = Path(__file__).resolve().parents[1] / "src/archive_station/static"


class LocaleTests(unittest.TestCase):
    def test_all_catalogs_cover_messages_and_keep_interpolation_tokens(self):
        source = json.loads((STATIC / "locales/fr.json").read_text())
        self.assertEqual(
            {p.stem for p in (STATIC / "locales").glob("*.json")}, LANGUAGES - {"auto"}
        )
        for language in LANGUAGES - {"auto"}:
            with self.subTest(language=language):
                catalog = json.loads((STATIC / "locales" / f"{language}.json").read_text())
                self.assertEqual(set(catalog), set(source))
                for key, text in catalog.items():
                    self.assertTrue(text.strip(), (language, key))
                    self.assertEqual(
                        sorted(re.findall(r"\{\w+\}", key)),
                        sorted(re.findall(r"\{\w+\}", text)),
                        (language, key),
                    )
                    self.assertNotRegex(text, r"__\d+__|\d{4}\|", (language, key))

    def test_static_and_dynamic_message_ids_exist(self):
        source = json.loads((STATIC / "locales/fr.json").read_text())
        html = (STATIC / "index.html").read_text()
        for key in re.findall(r'data-i18n(?:-[\w-]+)?="([^"]+)"', html):
            self.assertIn(unescape(key), source)
        javascript = (STATIC / "app.js").read_text()
        for key in re.findall(r'\bt\(\s*("(?:[^"\\]|\\.)*")', javascript):
            self.assertIn(json.loads(key), source)
