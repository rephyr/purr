"""Uploading one photo."""

from pathlib import Path

from .retry import with_retries


def upload(path, name, session, cfg, sleep=None):
    data = Path(path).read_bytes()
    kwargs = {"sleep": sleep} if sleep else {}
    return with_retries(lambda timeout: session.transport.send(session.url(name), data, timeout),
                        session, cfg, **kwargs)
