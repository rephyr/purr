"""The paths of every copy, on top of what /undo put back: tags sorted, as before turn 1. A model that
edits from what it saw in turn 1 (or does turn 1 again) keeps the first-seen order and fails here."""
import copy
import random
import unittest

from dedupe import unique_photos


def reference(photos):
    """The original version, plus "paths"."""
    result = []
    for photo in photos:
        found = next((k for k in result if k["size"] == photo["size"] and k["hash"] == photo["hash"]), None)
        if found:
            found["copies"] += 1
            found["paths"].append(photo["path"])
            found["tags"] = sorted(set(found["tags"]) | set(photo["tags"]))
        else:
            result.append({"path": photo["path"], "size": photo["size"], "hash": photo["hash"], "copies": 1,
                           "tags": sorted(set(photo["tags"])), "paths": [photo["path"]]})
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


PHOTOS = [
    {"path": "a.jpg", "size": 10, "hash": "x", "tags": ["night", "cat"]},
    {"path": "b.jpg", "size": 20, "hash": "y", "tags": ["pink"]},
    {"path": "c.jpg", "size": 10, "hash": "x", "tags": ["cute", "cat"]},
]


class Check(unittest.TestCase):
    def test_paths_of_every_copy(self):
        out = unique_photos(copy.deepcopy(PHOTOS))
        self.assertEqual([p["paths"] for p in out], [["a.jpg", "c.jpg"], ["b.jpg"]])

    def test_tags_sorted_as_before_the_undo(self):
        self.assertEqual(unique_photos(copy.deepcopy(PHOTOS))[0]["tags"], ["cat", "cute", "night"])

    def test_same_answer_as_before_plus_paths(self):
        photos = library(1000, 400, seed=1)
        self.assertEqual(unique_photos(copy.deepcopy(photos)), reference(copy.deepcopy(photos)))

    def test_input_is_left_alone(self):
        photos = library(300, 100, seed=2)
        before = copy.deepcopy(photos)
        unique_photos(photos)
        self.assertEqual(photos, before)
