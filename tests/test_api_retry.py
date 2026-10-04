"""api.stream_chat: a connection dropped before the answer is a retryable ApiError, not a crash.
No model is called.
"""

import http.client
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import api  # noqa: E402
from harness.api import ApiError  # noqa: E402


class DroppedConnectionTest(unittest.TestCase):
    def test_errors_before_the_answer_can_be_retried(self):
        for err in (http.client.RemoteDisconnected("closed"), ConnectionResetError("reset"),
                    TimeoutError("timed out")):
            with mock.patch.object(api.urllib.request, "urlopen", side_effect=err):
                with self.assertRaises(ApiError) as caught:
                    api.stream_chat("http://x", "", {}, print, print)
            self.assertTrue(caught.exception.retry, type(err).__name__)
