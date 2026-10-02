"""Content hashes, so a renamed photo isn't uploaded twice."""

import hashlib

from .units import kib_to_bytes


def file_hash(path, chunk_kib=512):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(kib_to_bytes(chunk_kib)):
            h.update(chunk)
    return h.hexdigest()[:16]
