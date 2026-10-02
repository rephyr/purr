"""Settings: the defaults below, overridden by settings.toml."""

import tomllib
from pathlib import Path

DEFAULTS = {
    "server": "http://localhost:8080",
    "upload_timeout_ms": 30_000,  # per attempt
    "retries": 3,
    "backoff_ms": 500,            # doubles after every failed attempt
    "chunk_kib": 512,
    "thumbnail_px": 320,
    "skip_hidden": True,
    "extensions": [".jpg", ".jpeg", ".png", ".heic"],
}


def load(path="settings.toml"):
    cfg = dict(DEFAULTS)
    p = Path(path)
    if p.exists():
        cfg.update(tomllib.loads(p.read_text()))
    return cfg
