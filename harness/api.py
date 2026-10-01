"""Talks to any OpenAI-compatible chat API (Ollama, DeepSeek, ...) with streaming."""

import json
import time
import urllib.error
import urllib.request


class ApiError(Exception):
    def __init__(self, message, retry=False):
        super().__init__(message)
        self.retry = retry  # worth trying again (server busy or restarting)


class Stopped(Exception):
    """You asked the answer to stop."""


def stream_chat(base_url, api_key, body, on_text, on_reasoning, should_stop=lambda: False):
    """POST /chat/completions with stream=True. Calls on_text / on_reasoning as pieces arrive.

    Returns {"text", "reasoning", "tool_calls", "usage", "finish", "gen_seconds", "call_seconds"}
    once the reply is complete. gen_seconds runs from the first piece to the last, so it leaves
    out the time the model spent reading the prompt: output tokens / gen_seconds is the speed
    you feel. call_seconds is the whole call.
    """
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
        resp = urllib.request.urlopen(req, timeout=600)
    except urllib.error.HTTPError as e:
        raise ApiError(f"{e.code}: {e.read().decode(errors='replace')[:800]}",
                       retry=e.code == 429 or e.code >= 500) from None
    except urllib.error.URLError as e:
        raise ApiError(f"can't reach {base_url}: {e.reason}", retry=True) from None

    text, reasoning = [], []
    calls = {}  # index -> {"id", "name", "args"}; tool calls arrive in pieces
    usage = None
    finish = None
    first = last = None
    with resp:
        for raw in resp:
            if should_stop():
                raise Stopped
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if chunk.get("error"):
                raise ApiError(str(chunk["error"]))
            if chunk.get("usage"):
                usage = chunk["usage"]
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
    }
