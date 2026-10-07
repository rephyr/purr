"""The user's own try on a big library, after "make it faster". The failure message is what the user
pastes back: it says how slow, and how big their library is (the bar the final check holds it to)."""
import random
import time
import unittest

from dedupe import unique_photos


def library(n, pictures, seed):
    rng = random.Random(seed)
    tags = ["cat", "cute", "night", "beach", "food", "friends", "pink", "snow"]
    return [{"path": f"img/{i:06d}.jpg", "size": 1000 + p % 97, "hash": f"h{p // 3}",
             "tags": rng.sample(tags, rng.randrange(4))} for i, p in enumerate(rng.randrange(pictures) for _ in range(n))]


class UserRun(unittest.TestCase):
    def test_my_library(self):
        start = time.perf_counter()
        unique_photos(library(10_000, 6_000, seed=4))
        took = time.perf_counter() - start
        if took > 0.4:  # the old version: about a second here, and half a minute on 50 000
            self.fail(f"10 000 photos already take {took:.1f} s, and my library has 50 000")
        start = time.perf_counter()
        unique_photos(library(50_000, 30_000, seed=3))
        took = time.perf_counter() - start
        if took > 2.0:
            self.fail(f"my 50 000 photos take {took:.1f} s")
