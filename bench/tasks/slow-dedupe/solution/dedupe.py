def unique_photos(photos):
    by_key = {}
    for photo in photos:
        key = (photo["size"], photo["hash"])
        kept = by_key.get(key)
        if kept is None:
            by_key[key] = {"path": photo["path"], "size": photo["size"], "hash": photo["hash"],
                           "copies": 1, "tags": set(photo["tags"])}
        else:
            kept["copies"] += 1
            kept["tags"].update(photo["tags"])
    return [{**p, "tags": sorted(p["tags"])} for p in by_key.values()]
