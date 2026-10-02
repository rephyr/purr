"""Which files in a folder are photos we should look at."""

from pathlib import Path


def photo_files(folder, cfg):
    exts = {e.lower() for e in cfg["extensions"]}
    for p in sorted(Path(folder).rglob("*")):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        if cfg["skip_hidden"] and any(part.startswith(".") for part in p.parts):
            continue
        yield p


def relative_name(path, folder):
    return str(Path(path).relative_to(folder)).replace("\\", "/")
