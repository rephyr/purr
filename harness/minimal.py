"""Minimal mode (minimal = true in config.toml; benchmarks: --ak minimal=true): purr the way
DeepSeek's own harness got its best Terminal-Bench 2.1 score. DSH Minimal scored 90.6% with
DeepSeek V4.1 Flash, against 85.8% for DSH's full preset with the same model: a one-line system
prompt, one tool (bash, in a shell that stays open between calls), every reply's thinking sent
back, thinking effort high, and no checks, nudges or reminders. A reference point for what purr's
own machinery adds or costs on a strong model; small models keep the full harness.
"""

import os
import signal
import subprocess
import threading
import time
import uuid

SYSTEM = "You are a helpful software engineer assistant."
TIMEOUT = 300       # seconds a command may run (DSH's persistent shell: 300)
MAX_OUTPUT = 16000  # characters of output the model gets, from the start

BASH = {"type": "function", "function": {
    "name": "bash",
    "description": ("Run commands in a bash shell. The shell stays open between calls: the working "
                    "directory, environment variables and background processes carry over. Commands "
                    "get no input (stdin is empty), so use non-interactive flags like -y. A command "
                    f"that runs longer than {TIMEOUT} seconds is stopped and the shell starts over. "
                    "Long output is cut: send it to a file and look at parts with grep -n or sed -n."),
    "parameters": {"type": "object", "properties": {
        "command": {"type": "string", "description": "The bash command to run."}},
        "required": ["command"]}}}


class BashSession:
    """One bash process for the whole run. Each command runs in it with stdin from /dev/null,
    followed by a marker line carrying its exit code, so the output is read up to the marker."""

    def __init__(self, root):
        self.root = root
        self.proc = None

    def _start(self):
        self.proc = subprocess.Popen(
            ["bash", "--noprofile", "--norc"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd=self.root, start_new_session=True,
            env={**os.environ, "PS1": "", "PS2": ""})
        self.buf = bytearray()
        self.lock = threading.Condition()
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        fd = proc.stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            with self.lock:
                if chunk and proc is self.proc:
                    self.buf += chunk
                self.lock.notify_all()
            if not chunk:
                return

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait()
        self._drop()

    def _drop(self):
        if self.proc:
            for pipe in (self.proc.stdin, self.proc.stdout):
                try:
                    pipe.close()
                except OSError:
                    pass
        self.proc = None

    def run(self, command, timeout=TIMEOUT):
        """The command's output for the model, ending in [exit code: N]."""
        # a syntax error (an unclosed quote) would swallow the marker and hang until the timeout
        check = subprocess.run(["bash", "-n"], input=command, capture_output=True, text=True)
        if check.returncode:
            return f"{check.stderr.strip()}\n[exit code: {check.returncode}]"
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        marker = f"__purr_done_{uuid.uuid4().hex}__"
        script = (f"{{ {command}\n}} < /dev/null 2>&1\n"
                  f"__purr_s=$?; printf '\\n{marker} %d\\n' \"$__purr_s\"\n")
        with self.lock:
            self.buf.clear()
        try:
            self.proc.stdin.write(script.encode())
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self.close()
            return "error: the shell had exited; run the command again (a new shell starts)\n[exit code: -1]"
        end = time.monotonic() + timeout
        tail = marker.encode()
        with self.lock:
            while tail not in self.buf and self.proc.poll() is None and time.monotonic() < end:
                self.lock.wait(min(1.0, max(0.0, end - time.monotonic())))
            raw = bytes(self.buf)
        if tail in raw:
            out, _, rest = raw.partition(b"\n" + tail)
            if not _:  # the output didn't end with the newline printf adds before the marker
                out, _, rest = raw.partition(tail)
            code = rest.split()[0].decode() if rest.split() else "?"
            return _result(out, code=code)
        if self.proc.poll() is not None:  # the command ran `exit`, or the shell died
            code = self.proc.returncode
            self._drop()
            return _result(raw, f"(the shell exited; the next command starts a new one in {self.root})", code)
        self.close()
        return _result(raw, f"(timed out after {timeout} seconds: the command was stopped and the shell "
                            f"started over in {self.root}, so its directory and variables are reset. For a "
                            "longer job, start it in the background with its output in a log and check the log.)")


def _result(raw, note="", code=-1):
    return "\n".join(part for part in (_clip(_text(raw)), note, f"[exit code: {code}]") if part)


def _text(raw):
    """Bytes as text: a progress bar's \\r updates come out as their last state."""
    text = raw.decode("utf-8", errors="replace")
    if "\r" in text:
        text = "\n".join(line.rstrip("\r").rsplit("\r", 1)[-1] for line in text.split("\n"))
    return text.strip("\n")


def _clip(text):
    if len(text) <= MAX_OUTPUT:
        return text
    return (text[:MAX_OUTPUT] + f"\n<response clipped: {len(text) - MAX_OUTPUT} more characters. Send the "
            "output to a file and look at the part you need with grep -n or sed -n.>")
