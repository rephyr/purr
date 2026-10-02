import unittest

from dedupe import unique_photos


class DedupeTest(unittest.TestCase):
    def test_merges_copies(self):
        photos = [
            {"path": "a.jpg", "size": 10, "hash": "x", "tags": ["cat"]},
            {"path": "b.jpg", "size": 20, "hash": "y", "tags": []},
            {"path": "c.jpg", "size": 10, "hash": "x", "tags": ["cute", "cat"]},
        ]
        out = unique_photos(photos)
        self.assertEqual([p["path"] for p in out], ["a.jpg", "b.jpg"])
        self.assertEqual(out[0]["copies"], 2)
        self.assertEqual(out[0]["tags"], ["cat", "cute"])


if __name__ == "__main__":
    unittest.main()
