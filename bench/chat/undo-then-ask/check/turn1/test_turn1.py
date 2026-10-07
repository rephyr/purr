import unittest

from dedupe import unique_photos


class Turn1(unittest.TestCase):
    def test_tags_in_the_order_first_seen(self):
        photos = [
            {"path": "a.jpg", "size": 10, "hash": "x", "tags": ["night", "cat"]},
            {"path": "b.jpg", "size": 20, "hash": "y", "tags": ["pink", "beach"]},
            {"path": "c.jpg", "size": 10, "hash": "x", "tags": ["cute", "cat"]},
        ]
        out = unique_photos(photos)
        self.assertEqual(out[0]["tags"], ["night", "cat", "cute"])
        self.assertEqual(out[1]["tags"], ["pink", "beach"])
