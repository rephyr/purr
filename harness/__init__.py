import subprocess
from pathlib import Path

VERSION = "0.4.0"  # bump for every release: benchmark results are filed under it


def full_version():
    """"0.3.1+abc1234": the version and the commit, with "-dirty" if purr has uncommitted changes.
    Benchmark results carry it, so each one points at exact code."""
    root = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return VERSION
    return f"{VERSION}+{commit}{'-dirty' if dirty else ''}" if commit else VERSION
