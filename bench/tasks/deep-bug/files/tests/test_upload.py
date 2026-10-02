import tempfile
import unittest
from pathlib import Path

from photosync import config
from photosync.sync import sync_folder


class FakeTransport:
    def __init__(self, fail_times=0):
        self.calls, self.fail_times = [], fail_times

    def send(self, url, data, timeout):
        self.calls.append(timeout)
        if len(self.calls) <= self.fail_times:
            raise TimeoutError("too slow")
        return 200


class UploadTest(unittest.TestCase):
    def folder(self):
        d = Path(tempfile.mkdtemp())
        (d / "cat.jpg").write_bytes(b"meow")
        return d

    def test_uploads_with_the_configured_timeout(self):
        fake = FakeTransport()
        sent = sync_folder(self.folder(), dict(config.DEFAULTS), fake, sleep=lambda s: None)
        self.assertEqual(sent, 1)
        self.assertEqual(fake.calls, [30.0])
