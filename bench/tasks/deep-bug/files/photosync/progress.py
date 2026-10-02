"""Upload progress for the terminal."""

import time


class Progress:
    def __init__(self, total_bytes):
        self.total, self.done, self.start = total_bytes, 0, time.monotonic()

    def add(self, n):
        self.done += n

    def speed_kib_s(self):
        seconds = max(time.monotonic() - self.start, 1e-6)
        return self.done / 1024 / seconds

    def line(self):
        pct = 100 * self.done / self.total if self.total else 100
        elapsed_ms = (time.monotonic() - self.start) * 1000
        return f"{pct:5.1f}%  {self.speed_kib_s():.0f} KiB/s  {elapsed_ms / 1000:.1f}s"
