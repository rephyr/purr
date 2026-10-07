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


class StopBetweenPiecesTest(unittest.TestCase):
    """A stop while the model pauses mid-reply cuts the connection, and http.client reads that as
    the end of the stream: the half-written reply came back as a finished one, with no 'stopped'."""

    def serve(self, headers, piece):
        import socket
        import threading
        import time
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        self.addCleanup(server.close)

        def answer():
            conn, _ = server.accept()
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n" + headers + b"\r\n")
            for word in (b"Here is my half-written ", b"answer about "):
                conn.sendall(piece(b'data: {"choices":[{"delta":{"content":"%s"}}]}\n\n' % word))
                time.sleep(0.05)
            time.sleep(5)  # the model pauses (a slow model, Ollama holding back a tool call)
            conn.close()
        threading.Thread(target=answer, daemon=True).start()
        return f"http://127.0.0.1:{server.getsockname()[1]}/v1"

    def check(self, url):
        import threading
        import time
        stop = threading.Event()
        threading.Timer(0.6, stop.set).start()
        started = time.monotonic()
        with self.assertRaises(api.Stopped) as caught:
            api.stream_chat(url, "", {"stream": True}, lambda _: None, lambda _: None, stop.is_set)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(caught.exception.partial["text"], "Here is my half-written answer about ")

    def test_chunked(self):
        self.check(self.serve(b"Transfer-Encoding: chunked\r\n",
                              lambda b: b"%x\r\n" % len(b) + b + b"\r\n"))

    def test_close_delimited(self):
        self.check(self.serve(b"Connection: close\r\n", lambda b: b))


class CtrlCWhileConnectingTest(unittest.TestCase):
    def test_a_socket_that_connects_after_ctrl_c_is_cut(self):
        # Ctrl-C during connect/TLS cut an empty list: the worker then sent the request
        # and streamed the reply over the prompt after "stopped"
        import _thread
        import socket
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        asked, written = [], []

        class Sse(BaseHTTPRequestHandler):
            def do_POST(self):
                asked.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"choices": [{"delta": {"content": "old reply"}}]}\n\ndata: [DONE]\n\n')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Sse)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        real = socket.create_connection

        def slow(*args, **kwargs):
            time.sleep(1)  # a slow DNS/TLS handshake to a far-away API
            return real(*args, **kwargs)
        threading.Timer(0.3, _thread.interrupt_main).start()
        with mock.patch("socket.create_connection", slow):
            with self.assertRaises(KeyboardInterrupt):
                api.stream_chat(f"http://127.0.0.1:{server.server_address[1]}/v1", "", {}, written.append, print)
            time.sleep(1.5)  # the worker's connect has finished by now
        self.assertEqual(written, [])
        self.assertEqual(asked, [])


class StopWhileConnectingTest(unittest.TestCase):
    def test_a_stop_during_connect_returns_at_once_and_sends_nothing(self):
        # Esc / the bench's time limit during a hung connect waited up to 10 s for it
        import socket
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        asked, written = [], []

        class Sse(BaseHTTPRequestHandler):
            def do_POST(self):
                asked.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"choices": [{"delta": {"content": "old reply"}}]}\n\ndata: [DONE]\n\n')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Sse)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        real = socket.create_connection

        def slow(*args, **kwargs):
            time.sleep(3)  # a connect that hangs (unanswered SYN, stuck TLS handshake)
            return real(*args, **kwargs)
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        with mock.patch("socket.create_connection", slow):
            started = time.monotonic()
            with self.assertRaises(api.Stopped):
                api.stream_chat(f"http://127.0.0.1:{server.server_address[1]}/v1", "", {},
                                written.append, print, stop.is_set)
            self.assertLess(time.monotonic() - started, 2)
            time.sleep(3.5)  # the worker's connect has finished by now
        self.assertEqual(written, [])
        self.assertEqual(asked, [])
