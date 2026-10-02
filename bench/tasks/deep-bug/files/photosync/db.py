"""Remembers what was already uploaded (a JSON file next to the photos)."""

import json
from pathlib import Path

DB_NAME = ".photosync.json"


class SeenDB:
    def __init__(self, folder):
        self.path = Path(folder) / DB_NAME
        self.seen = json.loads(self.path.read_text()) if self.path.exists() else {}

    def known(self, digest):
        return digest in self.seen

    def remember(self, digest, name):
        self.seen[digest] = name

    def save(self):
        self.path.write_text(json.dumps(self.seen, indent=1, sort_keys=True))
