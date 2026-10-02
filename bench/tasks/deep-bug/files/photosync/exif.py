"""Reads the date a photo was taken, falling back to the file's time."""

import datetime
import os


def taken_at(path):
    # real EXIF parsing lives on the server; here the file time is good enough
    ts = os.path.getmtime(path)
    return datetime.datetime.fromtimestamp(ts)


def album_for(path):
    when = taken_at(path)
    return f"{when.year}/{when.month:02d}"
