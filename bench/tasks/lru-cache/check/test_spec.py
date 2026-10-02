"""Only the clear prompt says these: expiry exactly at ttl, len() skipping expired items."""
import unittest

from cache import LRUCache
from _bench_check.test_check import Clock

class Spec(unittest.TestCase):
    def test_expires_exactly_at_ttl(self):
        clock = Clock()
        c = LRUCache(5, ttl=10, clock=clock)
        c.put("a", 1)
        clock.now = 9.99
        self.assertEqual(c.get("a"), 1)
        clock.now = 10.0
        self.assertIsNone(c.get("a"))
        self.assertNotIn("a", c)

    def test_len_skips_expired(self):
        clock = Clock()
        c = LRUCache(5, ttl=10, clock=clock)
        c.put("a", 1)
        clock.now = 5
        c.put("b", 2)
        clock.now = 12
        self.assertEqual(len(c), 1)
        self.assertEqual(c.get("b"), 2)
