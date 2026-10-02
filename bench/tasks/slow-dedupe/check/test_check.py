import copy
import random
import time
import unittest

from dedupe import unique_photos


def slow_reference(photos):
    """The original, slow version: what the answer must match."""
    result = []
    for photo in photos:
        found = None
        for kept in result:
            if kept["size"] == photo["size"] and kept["hash"] == photo["hash"]:
                found = kept
                break
        if found:
            found["copies"] += 1
            for tag in photo["tags"]:
                if tag not in found["tags"]:
                    found["tags"].append(tag)
            found["tags"].sort()
        else:
            result.append({"path": photo["path"], "size": photo["size"], "hash": photo["hash"],
                           "copies": 1, "tags": sorted(set(photo["tags"]))})
    return result


def library(n, pictures, seed):
    rng = random.Random(seed)
    tags = ["cat", "cute", "night", "beach", "food", "friends", "pink", "snow"]
    out = []
    for i in range(n):
        p = rng.randrange(pictures)
        # the same hash with a different size is a different picture
        out.append({"path": f"img/{i:06d}.jpg", "size": 1000 + p % 97, "hash": f"h{p // 3}",
                    "tags": rng.sample(tags, rng.randrange(4)) + (["cat"] if rng.random() < 0.2 else [])})
    return out


class Check(unittest.TestCase):
    def test_same_answer_as_before(self):
        photos = library(3000, 1200, seed=1)
        self.assertEqual(unique_photos(copy.deepcopy(photos)), slow_reference(copy.deepcopy(photos)))

    def test_input_is_left_alone(self):
        photos = library(500, 100, seed=2)
        before = copy.deepcopy(photos)
        unique_photos(photos)
        self.assertEqual(photos, before)

    def test_fast_on_a_big_library(self):
        small = library(5_000, 3_000, seed=4)  # the old version takes seconds here: give up early
        start = time.perf_counter()
        unique_photos(small)
        if time.perf_counter() - start > 0.5:
            self.fail("still far too slow (5 000 photos took over half a second)")
        photos = library(50_000, 30_000, seed=3)
        start = time.perf_counter()
        out = unique_photos(photos)
        self.assertLess(time.perf_counter() - start, 2.0)
        self.assertEqual(sum(p["copies"] for p in out), 50_000)
