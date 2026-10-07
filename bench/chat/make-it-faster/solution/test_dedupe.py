import unittest

from dedupe import unique_photos

PHOTOS = [
    {"path": "a.jpg", "size": 10, "hash": "x", "tags": ["cat"]},
    {"path": "b.jpg", "size": 20, "hash": "y", "tags": []},
    {"path": "c.jpg", "size": 10, "hash": "x", "tags": ["cute", "cat"]},
]


class DedupeTest(unittest.TestCase):
    def test_merges_copies(self):
        out = unique_photos(PHOTOS)
        self.assertEqual([p["path"] for p in out], ["a.jpg", "b.jpg"])
        self.assertEqual(out[0]["copies"], 2)
        self.assertEqual(out[0]["tags"], ["cat", "cute"])

    def test_min_copies(self):
        self.assertEqual([p["path"] for p in unique_photos(PHOTOS, min_copies=2)], ["a.jpg"])
        self.assertEqual(unique_photos(PHOTOS, min_copies=3), [])


if __name__ == "__main__":
    unittest.main()
