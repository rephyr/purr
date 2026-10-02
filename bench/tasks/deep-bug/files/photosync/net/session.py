"""One connection's worth of settings: where to send things and how long to wait."""

from ..units import ms_to_s


class Session:
    def __init__(self, cfg, transport):
        self.server = cfg["server"].rstrip("/")
        self.timeout = ms_to_s(cfg["upload_timeout_ms"])  # seconds
        self.transport = transport

    def url(self, name):
        return f"{self.server}/upload/{name}"
