"""Talks to any OpenAI-compatible chat API (Ollama, DeepSeek, ...) with streaming."""

import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.request


class ApiError(Exception):
    def __init__(self, message, retry=False, status=None):
        super().__init__(message)
        self.retry = retry  # worth trying again (server busy or restarting)
        self.status = status  # the HTTP code (or the error's own code in a stream), if there was one


class Stopped(Exception):
    """You asked the answer to stop. .partial holds what the model had written by then."""

    def __init__(self, partial=None):
        super().__init__("stopped")
        self.partial = partial


class _Sockets:
    """A call's sockets, so another thread can cut them off.

    connect (DNS, TCP, TLS) can still be running when the cut comes, so a socket that
    connects after it is shut at once; otherwise the call went on after a stop or Ctrl-C
    and its reply streamed in afterwards.
    """

    def __init__(self):
        self.socks = []
        self.cut = False

    def add(self, sock):
        self.socks.append(sock)
        if self.cut:  # read after the append: either this or cut_all sees the socket
            _shut(sock)

    def cut_all(self):
        self.cut = True
        for sock in list(self.socks):
            _shut(sock)


def _shut(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _tracked(base, sockets):
    """An HTTP(S) connection class that hands its socket to `sockets` once connected."""
    class Tracked(base):
        def connect(self):
            super().connect()
            sockets.add(self.sock)
    return Tracked


class _Http(urllib.request.HTTPHandler):
    def __init__(self, sockets):
        super().__init__()
        self.sockets = sockets

    def http_open(self, req):
        return self.do_open(_tracked(http.client.HTTPConnection, self.sockets), req)


class _Https(urllib.request.HTTPSHandler):
    def __init__(self, sockets):
        super().__init__()
        self.sockets = sockets

    def https_open(self, req):
        return self.do_open(_tracked(http.client.HTTPSConnection, self.sockets), req, context=self._context)


def _open(req, sockets):
    """urlopen, keeping the request's socket in `sockets`, so another thread can cut it off."""
    return urllib.request.build_opener(_Http(sockets), _Https(sockets)).open(req, timeout=600)


def stream_chat(base_url, api_key, body, on_text, on_reasoning, should_stop=lambda: False):
    """POST /chat/completions with stream=True. Calls on_text / on_reasoning as pieces arrive.

    Returns {"text", "reasoning", "tool_calls", "usage", "finish", "gen_seconds", "call_seconds"}
    once the reply is complete. gen_seconds runs from the first piece to the last, so it leaves
    out the time the model spent reading the prompt: output tokens / gen_seconds is the speed
    you feel. call_seconds is the whole call.

    A stop works even before the first piece: the call runs in a thread, and stopping cuts its
    connection (a local model reading a 40k-token prompt sent nothing for minutes, so stop and
    the bench's time limit waited; cutting it also makes Ollama drop the work).
    """
    sockets, out = _Sockets(), {}

    def work():
        try:
            out["reply"] = _stream_chat(base_url, api_key, body, on_text, on_reasoning, should_stop, sockets)
        except BaseException as e:  # noqa: BLE001 - handed to the caller as is
            out["error"] = e
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            worker.join(0.2)
            if worker.is_alive() and should_stop():
                sockets.cut_all()
                # still connecting (no socket yet): a hung connect can take long to give up, and a
                # socket that connects after the cut is shut at once, so there's nothing to wait for
                worker.join(10 if sockets.socks else 0.5)
                break
    except BaseException:  # Ctrl-C: don't leave the model working for nobody
        sockets.cut_all()
        raise
    if "reply" in out:
        return out["reply"]
    if isinstance(out.get("error"), BaseException):
        raise out["error"]
    raise Stopped(None)


def _stream_chat(base_url, api_key, body, on_text, on_reasoning, should_stop, sockets):
    started = time.monotonic()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        resp = _open(req, sockets)
    except urllib.error.HTTPError as e:
        raise ApiError(f"{e.code}: {e.read().decode(errors='replace')[:800]}",
                       retry=e.code == 429 or e.code >= 500, status=e.code) from None
    except urllib.error.URLError as e:
        if should_stop():
            raise Stopped(None) from None
        raise ApiError(f"can't reach {base_url}: {e.reason}", retry=True) from None
    except (http.client.HTTPException, ConnectionError, socket.timeout) as e:
        if should_stop():  # cut off by a stop while the model read the prompt
            raise Stopped(None) from None
        # the server hung up or went quiet before answering (RemoteDisconnected, a reset, a
        # timeout): urllib doesn't wrap these, so without this a run would end in a traceback
        raise ApiError(f"can't reach {base_url}: {type(e).__name__}: {e}", retry=True) from None

    if should_stop():
        raise Stopped(None)
    try:
        with resp:
            return _read_stream(resp, started, on_text, on_reasoning, should_stop)
    except (http.client.IncompleteRead, OSError, json.JSONDecodeError) as e:
        if should_stop():  # cut off by a stop: not a broken connection
            raise Stopped(None) from None
        # the connection broke mid-reply (seen on long benchmark runs): the call can go again
        raise ApiError(f"the reply broke off: {type(e).__name__}: {e}", retry=True) from None


def _read_stream(resp, started, on_text, on_reasoning, should_stop):
    text, reasoning = [], []
    calls = {}  # index -> {"id", "name", "args"}; tool calls arrive in pieces
    usage = None
    finish = None
    first = last = None
    served_by = None
    done = False

    def stopped():
        return Stopped({"text": "".join(text), "reasoning": "".join(reasoning), "provider": served_by,
                        "tool_calls": [{"name": c["name"], "args": c["args"]} for c in calls.values()]})
    for raw in resp:
        if should_stop():
            raise stopped()
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            done = True
            break
        chunk = json.loads(data)
        if chunk.get("error"):
            err = chunk["error"]
            code = err.get("code") if isinstance(err, dict) else None
            code = code if isinstance(code, int) else None
            # an error in the middle of a stream is mostly the provider dropping it: try again,
            # unless it's about the request itself
            raise ApiError(str(err), retry=code is None or code == 429 or code >= 500, status=code)
        if chunk.get("usage"):
            usage = chunk["usage"]
        served_by = chunk.get("provider") or served_by  # OpenRouter says who answered
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta:
                last = time.monotonic()
                first = first or last
            # DeepSeek calls it reasoning_content, Ollama calls it reasoning
            r = delta.get("reasoning_content") or delta.get("reasoning")
            if r:
                reasoning.append(r)
                on_reasoning(r)
            if delta.get("content"):
                text.append(delta["content"])
                on_text(delta["content"])
            for tc in delta.get("tool_calls") or []:
                i = tc.get("index", len(calls))
                slot = calls.setdefault(i, {"id": "", "name": "", "args": ""})
                slot["id"] = tc.get("id") or slot["id"]
                fn = tc.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["args"] += fn.get("arguments") or ""
            finish = choice.get("finish_reason") or finish
    if not done and finish is None and should_stop():
        # a stop cuts the connection while we wait for the next piece, and http.client takes that
        # as the end of the stream: without this the half-written reply came back as a finished one
        raise stopped()

    tool_calls = []
    for n, i in enumerate(sorted(calls)):
        c = calls[i]
        tool_calls.append({"id": c["id"] or f"call_{n}", "name": c["name"], "args": c["args"]})
    return {
        "text": "".join(text),
        "reasoning": "".join(reasoning),
        "tool_calls": tool_calls,
        "usage": usage,
        "finish": finish,
        "gen_seconds": (last - first) if first else 0.0,
        "call_seconds": time.monotonic() - started,
        "provider": served_by,
    }
