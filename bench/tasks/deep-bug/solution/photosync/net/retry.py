"""Retries with a growing pause between attempts."""

import time

from ..log import warn
from ..units import ms_to_s


def with_retries(send, session, cfg, sleep=time.sleep):
    """Call send(timeout) up to cfg["retries"] times. Returns its result or raises the last error."""
    pause = ms_to_s(cfg["backoff_ms"])
    last = None
    for attempt in range(1, cfg["retries"] + 1):
        try:
            return send(session.timeout)
        except (TimeoutError, OSError) as e:
            last = e
            warn(f"attempt {attempt} failed after {session.timeout:.2f}s: {e}")
            if attempt < cfg["retries"]:
                sleep(pause)
                pause *= 2
    raise last
