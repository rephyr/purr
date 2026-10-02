import unittest

from cache import LRUCache


class CacheTest(unittest.TestCase):
    def test_put_and_get(self):
        c = LRUCache(2)
        c.put("a", 1)
        self.assertEqual(c.get("a"), 1)
        self.assertIsNone(c.get("missing"))

    def test_update_value(self):
        c = LRUCache(2)
        c.put("a", 1)
        c.put("a", 2)
        self.assertEqual(c.get("a"), 2)

    def test_capacity_must_be_positive(self):
        with self.assertRaises(ValueError):
            LRUCache(0)


if __name__ == "__main__":
    unittest.main()
