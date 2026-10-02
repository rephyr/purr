"""photosync's command line."""

from .config import load
from .sync import sync_folder


def main(argv):
    if not argv:
        print("usage: python3 -m photosync <folder>")
        return 2
    n = sync_folder(argv[0], load())
    print(f"uploaded {n} photo(s)")
    return 0
