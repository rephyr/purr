"""Finds duplicate photos in a photo library export."""


def unique_photos(photos, min_copies=1):
    """photos: a list of dicts with "path", "size", "hash" and "tags" (a list of strings).
    Two photos are the same picture when both their size and their hash match.

    Returns one dict per picture with at least min_copies copies, in the order each picture was
    first seen, with the first copy's "path", "size" and "hash", "copies" (how many copies there
    were) and "tags" (every tag of every copy, sorted, without repeats). The photos passed in are
    left as they were."""
    by_key = {}  # (size, hash) -> the picture; dicts keep the order they were first seen in
    for photo in photos:
        key = (photo["size"], photo["hash"])
        kept = by_key.get(key)
        if kept is None:
            by_key[key] = {"path": photo["path"], "size": photo["size"], "hash": photo["hash"],
                           "copies": 1, "tags": set(photo["tags"])}
        else:
            kept["copies"] += 1
            kept["tags"].update(photo["tags"])
    return [{**p, "tags": sorted(p["tags"])} for p in by_key.values() if p["copies"] >= min_copies]
