import unittest

from cache import LRUCache


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Check(unittest.TestCase):
    def test_never_holds_more_than_capacity(self):
        c = LRUCache(3)
        for n in range(10):
            c.put(n, n)
            self.assertLessEqual(len(c), 3)
        self.assertEqual([k for k in range(10) if k in c], [7, 8, 9])

    def test_get_makes_an_item_recent(self):
        c = LRUCache(2)
        c.put("a", 1)
        c.put("b", 2)
        c.get("a")
        c.put("c", 3)  # b is now the least recently used
        self.assertIn("a", c)
        self.assertNotIn("b", c)

    def test_put_on_an_existing_key_makes_it_recent(self):
        c = LRUCache(2)
        c.put("a", 1)
        c.put("b", 2)
        c.put("a", 10)
        c.put("c", 3)
        self.assertEqual(c.get("a"), 10)
        self.assertNotIn("b", c)

    def test_storing_again_restarts_the_ttl(self):
        clock = Clock()
        c = LRUCache(5, ttl=10, clock=clock)
        c.put("a", 1)
        clock.now = 8
        c.put("a", 2)
        clock.now = 15
        self.assertEqual(c.get("a"), 2)
