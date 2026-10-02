"""Syncing a whole folder: find photos, skip known ones, upload the rest."""

from . import config
from .db import SeenDB
from .hashing import file_hash
from .log import log
from .net.session import Session
from .net.transport import Transport
from .net.upload import upload
from .paths import photo_files, relative_name


def sync_folder(folder, cfg=None, transport=None, sleep=None):
    cfg = cfg or config.load()
    session = Session(cfg, transport or Transport())
    db = SeenDB(folder)
    sent = 0
    for path in photo_files(folder, cfg):
        digest = file_hash(path, cfg["chunk_kib"])
        if db.known(digest):
            continue
        name = relative_name(path, folder)
        upload(path, name, session, cfg, sleep=sleep)
        db.remember(digest, name)
        sent += 1
        log(f"uploaded {name}")
    db.save()
    return sent
