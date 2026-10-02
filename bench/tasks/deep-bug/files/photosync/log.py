"""A tiny logger that keeps the last lines in memory (tests read them)."""

import time

LINES = []


def log(msg, *, level="info"):
    line = f"{time.strftime('%H:%M:%S')} {level:5} {msg}"
    LINES.append(line)
    del LINES[:-500]
    return line


def warn(msg):
    return log(msg, level="warn")
