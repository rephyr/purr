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
            with mock.patch.object(api, "_open", side_effect=err):
                with self.assertRaises(ApiError) as caught:
                    api.stream_chat("http://x", "", {}, print, print)
            self.assertTrue(caught.exception.retry, type(err).__name__)


class StopBeforeTheFirstPieceTest(unittest.TestCase):
    def test_a_stop_cuts_a_server_that_sends_nothing(self):
        # a local model reading a 40k-token prompt sent nothing for minutes; stop had to wait
        import socket
        import threading
        import time
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        held = []
        threading.Thread(target=lambda: held.append(server.accept()), daemon=True).start()
        stop = threading.Event()
        threading.Timer(0.5, stop.set).start()
        started = time.monotonic()
        with self.assertRaises(api.Stopped):
            api.stream_chat(f"http://127.0.0.1:{server.getsockname()[1]}/v1", "", {}, print, print, stop.is_set)
        self.assertLess(time.monotonic() - started, 5)
        server.close()
