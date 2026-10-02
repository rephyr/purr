"""MCP servers (the [mcp.*] tables in config.toml): their tools are offered next to purr's own.

Each server is a program speaking JSON-RPC over stdin/stdout. purr starts it in the project folder
the first time the tool list is needed, asks what tools it has, and passes the model's calls on.
purr's own servers live in servers/ (codebase, stack); any other MCP server works the same way.

    [mcp.codebase]
    command = ["python3", "{purr}/servers/codebase.py"]   # {purr} = purr's folder, ~ works too
    tools = ["code_map", "outline"]                       # optional: offer only these
    enabled = true
"""

import json
import os
import queue
import subprocess
import threading
from pathlib import Path

PURR_DIR = Path(__file__).resolve().parent.parent
PROTOCOL = "2025-06-18"
START_TIMEOUT = 20  # seconds for a server to say hello
CALL_TIMEOUT = 120


class McpError(Exception):
    pass


class Server:
    def __init__(self, name, command, cwd, allow=None):
        self.name, self.cwd, self.allow = name, cwd, set(allow) if allow else None
        self.command = [os.path.expanduser(str(c).replace("{purr}", str(PURR_DIR))) for c in command]
        self.proc, self.tools, self.instructions = None, [], ""
        self.env = {}  # extra environment (PURR_OFFLINE=1 in private mode)
        self._id = 0
        self._lines = queue.Queue()
        self._lock = threading.Lock()  # helpers may call at the same time: one request at a time

    def start(self):
        self.proc = subprocess.Popen(self.command, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                     env={**os.environ, "PROJECT_ROOT": str(self.cwd), **self.env})
        self._lines = queue.Queue()  # a fresh one: a restarted server's old reader mustn't feed this one
        threading.Thread(target=self._read, args=(self.proc, self._lines), daemon=True).start()
        try:
            hello = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                "clientInfo": {"name": "purr", "version": "0.1"}}, START_TIMEOUT)
            self.instructions = hello.get("instructions", "")
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            tools = self.request("tools/list", {}, START_TIMEOUT).get("tools", [])
        except Exception:
            self.proc.kill()  # a server that fails to start mustn't keep running in the background
            raise
        self.tools = [t for t in tools if self.allow is None or t["name"] in self.allow]

    @staticmethod
    def _read(proc, lines):
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)  # the server ended

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params, timeout=CALL_TIMEOUT):
        with self._lock:
            self._id += 1
            mid = self._id
            try:
                self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
            except (BrokenPipeError, OSError):
                raise McpError(f"the {self.name} server isn't running") from None
            while True:
                try:
                    line = self._lines.get(timeout=timeout)
                except queue.Empty:
                    raise McpError(f"the {self.name} server didn't answer in {timeout}s") from None
                if line is None:
                    raise McpError(f"the {self.name} server stopped")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue  # a stray print from the server
                if msg.get("id") != mid:
                    continue  # its own notifications, or an old answer
                if "error" in msg:
                    raise McpError(msg["error"].get("message", "error"))
                return msg.get("result", {})

    def call(self, tool, args):
        result = self.request("tools/call", {"name": tool, "arguments": args})
        text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        return ("error: " + text) if result.get("isError") and not text.startswith("error") else text

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class Mcp:
    """All the configured servers, started lazily; a server that fails to start is skipped (once)."""

    def __init__(self, config, root, note=None):
        self.root, self.note = Path(root), note or (lambda s: None)
        self.servers = [Server(name, spec["command"], self.root, spec.get("tools"))
                        for name, spec in (config.get("mcp") or {}).items()
                        if spec.get("command") and spec.get("enabled", True)]
        self.started = False
        self.by_tool = {}  # tool name -> (server, its spec)
        self.env = {}

    def offline(self, on):
        """Private mode: restart the servers with PURR_OFFLINE=1, so none of them goes online."""
        want = {"PURR_OFFLINE": "1"} if on else {}
        if want == self.env:
            return
        self.env = want
        self.stop()
        self.started, self.by_tool = False, {}
        for s in self.servers:
            s.env = want

    def _start(self):
        if self.started:
            return
        self.started = True
        for s in self.servers:
            try:
                s.start()
            except (OSError, McpError) as e:
                self.note(f"MCP server {s.name} didn't start: {e}")
                continue
            for t in s.tools:
                self.by_tool.setdefault(t["name"], (s, t))

    def has(self, name):
        self._start()
        return name in self.by_tool

    def read_only(self, name):
        _, spec = self.by_tool[name]
        return bool((spec.get("annotations") or {}).get("readOnlyHint"))

    def schemas(self, read_only=False, taken=()):
        """The tools in the model's format (skipping names purr's own tools already use)."""
        self._start()
        out = []
        for name, (_, spec) in self.by_tool.items():
            if name in taken or (read_only and not self.read_only(name)):
                continue
            params = spec.get("inputSchema") or {"type": "object", "properties": {}}
            out.append({"type": "function", "function": {"name": name, "description": spec.get("description", ""),
                                                         "parameters": params}})
        return out

    def call(self, name, args):
        self._start()
        if name not in self.by_tool:
            return f"error: no MCP tool called {name}"
        server, _ = self.by_tool[name]
        if server.proc is not None and server.proc.poll() is not None:  # it crashed: start it again
            try:
                server.start()
            except Exception:  # noqa: BLE001 - say so instead of failing every call the same way
                return f"error: the MCP server {server.name} stopped and wouldn't start again"
        try:
            return server.call(name, args)
        except McpError as e:
            return f"error: {e}"

    def run_once(self, name, args=None):
        """Call a tool for purr itself (the project overview), "" if there's no such tool or it fails."""
        if not self.has(name):
            return ""
        text = self.call(name, args or {})
        return "" if text.startswith("error") else text

    def stop(self):
        for s in self.servers:
            s.stop()
