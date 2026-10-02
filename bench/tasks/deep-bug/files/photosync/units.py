"""Small unit helpers. Settings keep times in milliseconds and sizes in KiB."""


def ms_to_s(ms):
    return ms / 1000


def kib_to_bytes(kib):
    return int(kib * 1024)


def human_size(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
