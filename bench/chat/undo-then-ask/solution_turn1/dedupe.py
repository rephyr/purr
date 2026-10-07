"""Finds duplicate photos in a photo library export."""


def unique_photos(photos):
    """photos: a list of dicts with "path", "size", "hash" and "tags" (a list of strings).
    Two photos are the same picture when both their size and their hash match.

    Returns one dict per picture, in the order each picture was first seen, with the first
    copy's "path", "size" and "hash", "copies" (how many copies there were) and "tags" (every tag
    of every copy, in the order first seen, without repeats). The photos passed in are left as
    they were."""
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
        else:
            result.append({"path": photo["path"], "size": photo["size"], "hash": photo["hash"],
                           "copies": 1, "tags": list(dict.fromkeys(photo["tags"]))})
    return result
