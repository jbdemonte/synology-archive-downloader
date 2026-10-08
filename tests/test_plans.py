import unittest

from archive_station.plans import Plans


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.plans = Plans(clock=lambda: self.now)
        self.manifest = {
            "identifier": "demo",
            "title": "Demo",
            "files": [
                {"name": f"games/game-{i:05}.zip", "size": i, "algorithm": None, "digest": None}
                for i in range(42000)
            ]
            + [{"name": "readme.txt", "size": None, "algorithm": None, "digest": None}],
        }
        self.token = self.plans.create(self.manifest, "all", "")

    def test_large_tree_folder_selection_and_pattern(self):
        self.assertEqual(len(self.plans.view(self.token)["children"]), 2)
        page = self.plans.view(self.token, "games", 40000)
        self.assertEqual(len(page["children"]), 100)
        self.assertEqual(page["total"], 42000)
        self.plans.select(self.token, selected=False)
        self.plans.select(self.token, pattern="*.zip")
        self.plans.select(self.token, "games/game-00000.zip", False)
        folder = self.plans.view(self.token)["children"][0]
        self.assertEqual(folder["selected_count"], 41999)
        chosen, _, _ = self.plans.selection(self.token, "demo")
        self.assertEqual(len(chosen["files"]), 41999)
        self.assertEqual(chosen["unknown_sizes"], 0)
        self.plans.select(self.token, "games/", False)
        with self.assertRaisesRegex(ValueError, "au moins"):
            self.plans.selection(self.token, "demo")

    def test_expiration_and_identifier_binding(self):
        with self.assertRaises(ValueError):
            self.plans.selection(self.token, "other")
        self.now = 3601
        with self.assertRaisesRegex(ValueError, "expiré"):
            self.plans.view(self.token)
