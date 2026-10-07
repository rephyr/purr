import unittest

from dedupe import unique_photos

PHOTOS = [
    {"path": "a.jpg", "size": 10, "hash": "x", "tags": ["cat"]},
    {"path": "b.jpg", "size": 20, "hash": "y", "tags": []},
    {"path": "c.jpg", "size": 10, "hash": "x", "tags": ["cute", "cat"]},
]


class Turn1(unittest.TestCase):
    def test_min_copies(self):
        self.assertEqual([p["path"] for p in unique_photos(PHOTOS, min_copies=2)], ["a.jpg"])
        self.assertEqual([p["path"] for p in unique_photos(PHOTOS)], ["a.jpg", "b.jpg"])
