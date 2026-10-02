import tempfile
import unittest
from pathlib import Path

from photosync import config, units
from photosync.net.session import Session
from photosync.sync import sync_folder


class FakeTransport:
    def __init__(self, fail_times=0):
        self.calls, self.fail_times = [], fail_times

    def send(self, url, data, timeout):
        self.calls.append(timeout)
        if len(self.calls) <= self.fail_times:
            raise TimeoutError("too slow")
        return 200


def folder():
    d = Path(tempfile.mkdtemp())
    (d / "cat.jpg").write_bytes(b"meow")
    return d


class Check(unittest.TestCase):
    def test_every_attempt_waits_the_full_timeout(self):
        fake, pauses = FakeTransport(fail_times=2), []
        sync_folder(folder(), dict(config.DEFAULTS), fake, sleep=pauses.append)
        self.assertEqual(fake.calls, [30.0, 30.0, 30.0])
        self.assertEqual(pauses, [0.5, 1.0])  # the pause between retries didn't change

    def test_a_user_setting_is_used(self):
        fake = FakeTransport()
        sync_folder(folder(), {**config.DEFAULTS, "upload_timeout_ms": 5000}, fake, sleep=lambda s: None)
        self.assertEqual(fake.calls, [5.0])

    def test_units_still_right(self):
        self.assertEqual(units.ms_to_s(1500), 1.5)
        self.assertEqual(units.kib_to_bytes(2), 2048)
        self.assertEqual(Session(dict(config.DEFAULTS), None).url("a.jpg"), "http://localhost:8080/upload/a.jpg")
