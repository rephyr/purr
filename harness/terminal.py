"""Terminal sessions: programs the model keeps talking to (a VM's console, ssh, a REPL, a debugger,
a server it wants to watch), unlike `run`, which waits for a command to finish.

With tmux installed, each session is a tmux session (purr-<name>), so it keeps running after purr
is done: a VM or server the task needs stays up. Without tmux, sessions run in purr's own process
on a pseudo-terminal and end when purr does.
"""

import os
import pty
import re
import select
import shutil
import signal
import subprocess
import threading
import time

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][A-Z0-9]|\x1b[=>]")
# keys the model can press by name (send's keys), as bytes for a pty and as names for tmux
KEYS = {"Enter": ("\r", "Enter"), "Tab": ("\t", "Tab"), "Escape": ("\x1b", "Escape"),
        "Backspace": ("\x7f", "BSpace"), "Up": ("\x1b[A", "Up"), "Down": ("\x1b[B", "Down"),
        "Right": ("\x1b[C", "Right"), "Left": ("\x1b[D", "Left"), "C-c": ("\x03", "C-c"),
        "C-d": ("\x04", "C-d"), "C-z": ("\x1a", "C-z"), "C-a": ("\x01", "C-a"), "C-x": ("\x18", "C-x"),
        "C-l": ("\x0c", "C-l"), "C-]": ("\x1d", "C-]")}
MAX_WAIT = 300


def clean(text):
    """Terminal output as plain text: no colours or cursor moves, no carriage returns."""
    text = ANSI.sub("", text).replace("\r\n", "\n")
    return "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))


class PtySession:
    """A program on a pseudo-terminal in purr's own process (no tmux)."""

    def __init__(self, command, cwd):
        self.buffer, self.lock = [], threading.Lock()
        from .tools import child_env
        env = child_env(cwd, TERM="dumb")  # made before the fork: the child only execs
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # the child: become the program, or end (never a second copy of purr)
            try:
                os.chdir(cwd)
                os.execvpe("bash", ["bash", "-c", command] if command else ["bash", "-i"], env)
            finally:
                os._exit(127)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            try:
                ready, _, _ = select.select([self.fd], [], [], 0.5)
                if ready:
                    data = os.read(self.fd, 65536)
                    if not data:
                        break
                    with self.lock:
                        self.buffer.append(data.decode(errors="replace"))
            except OSError:
                break

    def write(self, text):
        os.write(self.fd, text.encode())

    def press(self, key):
        self.write(KEYS[key][0])

    def screen(self):
        with self.lock:
            return clean("".join(self.buffer))

    def alive(self):
        try:
            return os.waitpid(self.pid, os.WNOHANG) == (0, 0)
        except ChildProcessError:
            return False

    def stop(self):
        try:
            os.kill(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


class TmuxSession:
    """A tmux session: it survives purr, so what's started in it keeps running."""

    def __init__(self, name, command, cwd):
        self.target = f"purr-{name}"
        subprocess.run(["tmux", "kill-session", "-t", self.target], capture_output=True)
        args = ["tmux", "new-session", "-d", "-s", self.target, "-x", "200", "-y", "50", "-c", cwd]
        subprocess.run(args + ([command] if command else []), check=True, capture_output=True)
        subprocess.run(["tmux", "set-option", "-t", self.target, "remain-on-exit", "on"], capture_output=True)
        subprocess.run(["tmux", "set-option", "-t", self.target, "history-limit", "20000"], capture_output=True)

    def write(self, text):
        subprocess.run(["tmux", "send-keys", "-t", self.target, "-l", text], capture_output=True)

    def press(self, key):
        subprocess.run(["tmux", "send-keys", "-t", self.target, KEYS[key][1]], capture_output=True)

    def screen(self):
        res = subprocess.run(["tmux", "capture-pane", "-p", "-t", self.target, "-S", "-2000"],
                             capture_output=True, text=True)
        return clean(res.stdout).rstrip("\n")

    def alive(self):
        res = subprocess.run(["tmux", "display-message", "-p", "-t", self.target, "#{pane_dead}"],
                             capture_output=True, text=True)
        return res.returncode == 0 and res.stdout.strip() != "1"

    def stop(self):
        subprocess.run(["tmux", "kill-session", "-t", self.target], capture_output=True)


class Terminals:
    """The sessions one purr has open, by name."""

    def __init__(self, root):
        self.root = str(root)
        self.sessions = {}
        self.tmux = shutil.which("tmux") is not None

    def _get(self, name):
        if name not in self.sessions:
            have = ", ".join(self.sessions) or "none"
            raise ValueError(f"no terminal session called {name!r} (open: {have}); start one first")
        return self.sessions[name]

    def _settle(self, session, before, wait, wait_for):
        """Wait up to `wait` seconds: until `wait_for` shows up in new output, or (without it) until
        the output stops changing for a moment. Returns how it went, for the model."""
        wait = min(float(wait or 0), MAX_WAIT)
        pattern = re.compile(wait_for) if wait_for else None
        deadline, last, quiet = time.monotonic() + wait, None, 0.0
        while time.monotonic() < deadline:
            screen = session.screen()
            new = screen[len(before):] if screen.startswith(before) else screen
            if pattern and pattern.search(new):
                return f"(saw {wait_for!r})"
            if not pattern:
                quiet = quiet + 0.3 if screen == last else 0.0
                if quiet >= 1.5:
                    return ""
            if not session.alive():
                break
            last = screen
            time.sleep(0.3)
        return f"(waited {wait:g}s; {wait_for!r} didn't show up)" if pattern else ""

    def show(self, name, lines=40, note=""):
        session = self._get(name)
        tail = "\n".join(session.screen().splitlines()[-int(lines):])
        state = "running" if session.alive() else "finished"
        return f"[session {name}, {state}]{' ' + note if note else ''}\n{tail}"

    def start(self, name, command=None, wait=2, wait_for=None, lines=40):
        if name in self.sessions and self.sessions[name].alive():
            return f"error: session {name!r} is already running (send to it, or stop it first)"
        session = TmuxSession(name, command, self.root) if self.tmux else PtySession(command, self.root)
        self.sessions[name] = session
        note = self._settle(session, "", wait, wait_for)
        if not self.tmux:
            note = (note + " " if note else "") + "(no tmux here: this session ends when purr does)"
        return self.show(name, lines, note)

    def send(self, name, text=None, keys=None, wait=2, wait_for=None, lines=40):
        session = self._get(name)
        before = session.screen()
        if text:
            session.write(text)
        for key in keys or ([] if text is None else ["Enter"]):
            if key not in KEYS:
                return f"error: no key called {key!r}; have: {', '.join(KEYS)}"
            session.press(key)
        return self.show(name, lines, self._settle(session, before, wait, wait_for))

    def read(self, name, wait=0, wait_for=None, lines=40):
        session = self._get(name)
        return self.show(name, lines, self._settle(session, session.screen(), wait, wait_for))

    def stop(self, name):
        self._get(name).stop()
        del self.sessions[name]
        return f"stopped session {name}"

    def list(self):
        if not self.sessions:
            return "no terminal sessions"
        return "\n".join(f"{n}: {'running' if s.alive() else 'finished'}" for n, s in self.sessions.items())

    def close_local(self):
        """When purr quits: end the sessions that live in purr's process (tmux ones keep going)."""
        for s in self.sessions.values():
            if isinstance(s, PtySession):
                s.stop()

