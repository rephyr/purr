"""A tiny MCP server: JSON-RPC over stdin/stdout, tools only, no packages needed.

    server = Server("codebase", "what this server is for")

    @server.tool("Short description the model sees.", {"path": {"type": "string", "description": "..."}})
    def outline(path):
        return "text for the model"

    server.run()

Any MCP client can use it (purr, OpenCode, Claude Code...): start `python3 this_file.py` in the
project folder. Tools are read-only unless marked otherwise.
"""

import json
import sys
import traceback

PROTOCOL = "2025-06-18"


class Server:
    def __init__(self, name, instructions="", version="0.1.0"):
        self.name, self.instructions, self.version = name, instructions, version
        self.tools = {}  # name -> (spec, function, when)

    def tool(self, description, params=None, required=(), read_only=True, when=None):
        """Register a function as a tool. params: {name: JSON schema}; required: names that must be given;
        when: a function saying whether to offer it at all (say, only in projects that need it)."""
        def add(fn):
            spec = {"name": fn.__name__, "description": description,
                    "inputSchema": {"type": "object", "properties": params or {}, "required": list(required)},
                    "annotations": {"readOnlyHint": read_only}}
            self.tools[fn.__name__] = (spec, fn, when)
            return fn
        return add

    def handle(self, msg):
        """One request -> its response (None for notifications)."""
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if mid is None:  # notifications/initialized and friends: nothing to answer
            return None
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", PROTOCOL),
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": self.name, "version": self.version}}
            if self.instructions:
                result["instructions"] = self.instructions
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [spec for spec, _, when in self.tools.values() if when is None or when()]}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name not in self.tools:
                return _error(mid, -32602, f"no tool called {name}")
            try:
                text, failed = str(self.tools[name][1](**args)), False
            except TypeError as e:
                text, failed = f"error: wrong arguments for {name}: {e}", True
            except Exception as e:  # the model sees what went wrong instead of a dead server
                text, failed = f"error: {type(e).__name__}: {e}", True
                traceback.print_exc(file=sys.stderr)
            result = {"content": [{"type": "text", "text": text}], "isError": failed}
        else:
            return _error(mid, -32601, f"unknown method {method}")
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def run(self):
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                _send(_error(None, -32700, "not JSON"))
                continue
            reply = self.handle(msg)
            if reply is not None:
                _send(reply)


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()
