"""The model's hands: read, list, search, fetch, edit, write, run, todo, task.

Each tool = a schema (what the model sees) + a Python function (what actually happens).
Edits, writes and commands ask you first.
"""

import fnmatch
import json
import os
import re
import subprocess
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from .limits import Limits


MAX_OUTPUT = 12000  # fallback: characters a tool result may send back to the model


def _schema(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


SCHEMAS = [
    _schema("read_file", "Read a text file. Each line comes back as: line number, a tab, then the line. "
            "Use offset/limit for big files.", {
        "path": {"type": "string"},
        "offset": {"type": "integer", "description": "First line, 1-based. Default 1."},
        "limit": {"type": "integer", "description": "How many lines. Default {read_lines}."},
    }, ["path"]),
    _schema("list_files", "List files under a folder (skips .gitignored files). Optional glob like '*.gd'.", {
        "path": {"type": "string", "description": "Folder. Default '.'"},
        "glob": {"type": "string"},
    }, []),
    _schema("grep", "Search file contents with a regex (ripgrep). Returns file:line: text.", {
        "pattern": {"type": "string"},
        "path": {"type": "string", "description": "File or folder. Default '.'"},
        "glob": {"type": "string", "description": "Only files matching, e.g. '*.py'"},
    }, ["pattern"]),
    _schema("fetch_url", "Download a web page (or any URL) and return it as plain text.", {
        "url": {"type": "string"},
    }, ["url"]),
    _schema("edit_file", "Replace exact text in a file. old_text must match exactly once "
            "(include enough surrounding lines), unless replace_all is true.", {
        "path": {"type": "string"},
        "old_text": {"type": "string"},
        "new_text": {"type": "string"},
        "replace_all": {"type": "boolean"},
    }, ["path", "old_text", "new_text"]),
    _schema("write_file", "Create a file or overwrite it completely.", {
        "path": {"type": "string"},
        "content": {"type": "string"},
    }, ["path", "content"]),
    _schema("run", "Run a shell command (bash) in the project folder. Returns output and exit code.", {
        "command": {"type": "string"},
        "timeout": {"type": "integer", "description": "Seconds. Default 120."},
    }, ["command"]),
    _schema("todo", "Keep a short task list for work with several steps. Send the WHOLE list every "
            "time; the user sees it. Mark one item 'doing' while you work on it.", {
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"},
            "status": {"type": "string", "enum": ["pending", "doing", "done"]},
        }, "required": ["text", "status"]}},
    }, ["items"]),
    _schema("task", "Hand a research job to a helper with a fresh, empty memory. It can only read "
            "(files, grep, web) and reports back a summary. Good for exploring lots of files "
            "without filling up this chat. Tell it exactly what to find out.", {
        "prompt": {"type": "string"},
    }, ["prompt"]),
]

READ_ONLY = {"read_file", "list_files", "grep", "fetch_url"}
SHELL_CHAINING = set(";&|`$()<>\n")
# commands where the second word matters for "always allow" (git status vs git push)
TWO_WORD = {"git", "npm", "pnpm", "yarn", "uv", "pip", "cargo", "go", "docker", "systemctl", "godot"}


def schemas(read_only=False, read_lines=400):
    """The tool list for the model. read_lines is this model's read_file default."""
    out = [s for s in SCHEMAS if not read_only or s["function"]["name"] in READ_ONLY]
    text = json.dumps(out).replace("{read_lines}", str(read_lines))
    return json.loads(text)


def command_head(command):
    """'git status --short' -> 'git status', 'python3 x.py' -> 'python3'."""
    words = re.findall(r"[^\s;&|<>()]+", command)
    if not words:
        return ""
    if words[0] in TWO_WORD and len(words) > 1:
        # "git -C x push": can't tell what it does, so "always" only covers this exact command
        return " ".join(words[:2]) if not words[1].startswith("-") else command.strip()
    return words[0]


def clip(s, limit=MAX_OUTPUT):
    if len(s) <= limit:
        return s
    half = limit // 2
    return s[:half] + f"\n... [{len(s) - limit} characters cut] ...\n" + s[-half:]


def _lead(line):
    return line[:len(line) - len(line.lstrip(" \t"))]


def _loose_match(text, old, new):
    """Small models often get indentation slightly wrong. Find old_text's lines ignoring
    leading whitespace; if they match exactly one place, return (the real old text, new text
    re-indented the same way the real lines are). Otherwise None."""
    old_lines = old.strip("\n").splitlines()
    if not old_lines:
        return None
    lines = text.splitlines(keepends=True)
    want = [l.strip() for l in old_lines]
    hits = [i for i in range(len(lines) - len(want) + 1)
            if [l.strip() for l in lines[i:i + len(want)]] == want]
    if len(hits) != 1:
        return None
    real = lines[hits[0]:hits[0] + len(want)]
    # how the model's indents map onto the file's real ones, e.g. 6 spaces -> 4 spaces or a tab
    imap = {}
    for o, r in zip(old_lines, real):
        if o.strip():
            if imap.setdefault(_lead(o), _lead(r)) != _lead(r):
                return None
    keys = sorted(imap, key=len)
    fixed = []
    for l in new.strip("\n").splitlines():
        lead, body = _lead(l), l.lstrip(" \t")
        if not body:
            fixed.append("")
        elif lead in imap:
            fixed.append(imap[lead] + body)
        else:
            # nearest smaller known indent, then keep the extra
            base = next((k for k in reversed(keys) if len(k) <= len(lead)), keys[0])
            extra = lead[len(base):] if len(lead) > len(base) else ""
            fixed.append(imap[base] + extra + body)
    real_text = "".join(real)
    ending = "\n" if real_text.endswith("\n") else ""
    return real_text, "\n".join(fixed) + ending

class Tools:
    def __init__(self, root, view, read_only=False, allow_run=(), limits=None):
        self.root = Path(root).resolve()
        self.view = view
        self.read_only = read_only
        self.limits = limits or Limits.for_model({})
        self.allow_run = list(allow_run)  # patterns from config.toml that never ask
        self.always = set()      # tools you said "always" to this session
        self.always_run = set()  # command heads you said "always" to ("git status", "python3")
        self.trust_all = False
        self.halt = False        # a plain "no": stop and let the user say what to do next
        self.undo_stack = []     # one {path: text before, or None if it didn't exist} per turn
        self.spawn = None        # set by the agent: runs a helper for the task tool

    def _path(self, p):
        p = Path(p).expanduser()
        return p if p.is_absolute() else self.root / p

    def summary(self, name, args):
        """One short line for the screen."""
        key = {"read_file": "path", "list_files": "path", "grep": "pattern", "edit_file": "path",
               "write_file": "path", "run": "command", "fetch_url": "url", "task": "prompt"}.get(name)
        val = str(args.get(key, "")) if key else ""
        return f"{name} {val[:100]}".strip()

    def call(self, name, raw_args):
        """Run one tool call. Always returns a string for the model."""
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError
        except ValueError:
            return f"error: arguments were not valid JSON: {raw_args[:300]}"
        fn = getattr(self, "t_" + name, None)
        if fn is None or (self.read_only and name not in READ_ONLY):
            return f"error: there is no tool called {name}"
        if name != "todo":
            self.view.tool(self.summary(name, args))
        try:
            return clip(fn(**args), self.limits.tool_output)
        except TypeError as e:
            return f"error: wrong arguments for {name}: {e}"
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"

    def _allowed(self, name, question):
        if self.trust_all or name in self.always:
            return True, ""
        ans, reason = self.view.ask(question)
        if ans == "a":
            self.always.add(name)
        return ans in ("y", "a"), reason

    def _run_allowed(self, command):
        head = command_head(command)
        if self.trust_all or head in self.always_run:
            return True, ""
        simple = not (set(command) & SHELL_CHAINING)
        if simple and any(fnmatch.fnmatch(command.strip(), pat) for pat in self.allow_run):
            return True, ""
        ans, reason = self.view.ask(f"run it?   (always = every '{head}' command)")
        if ans == "a":
            self.always_run.add(head)
        return ans in ("y", "a"), reason

    def _refused(self, reason):
        if reason:
            return f"the user said no: {reason}"
        self.halt = True
        return "the user said no. Stop here; they will tell you what to do next."

    # ---- undo ----

    def begin_turn(self):
        self.undo_stack.append({})

    def _remember(self, p):
        """Keep a file's old text the first time this turn changes it, for /undo."""
        if not self.undo_stack:
            self.begin_turn()
        changes = self.undo_stack[-1]
        if p not in changes:
            changes[p] = p.read_text() if p.exists() else None

    def undo(self):
        """Put back the files the last turn changed. Returns the paths."""
        while self.undo_stack and not self.undo_stack[-1]:
            self.undo_stack.pop()
        if not self.undo_stack:
            return []
        changes = self.undo_stack.pop()
        for p, before in changes.items():
            if before is None:
                p.unlink(missing_ok=True)
            else:
                p.write_text(before)
        return [os.path.relpath(p, self.root) for p in changes]

    # ---- tools ----

    def t_read_file(self, path, offset=1, limit=None):
        p = self._path(path)
        lines = p.read_text(errors="replace").splitlines()
        offset = max(1, int(offset))
        limit = self.limits.read_lines if limit is None else int(limit)
        chunk = lines[offset - 1: offset - 1 + limit]
        body = "\n".join(f"{n:>5}\t{line[:500]}" for n, line in enumerate(chunk, offset))
        end = offset + len(chunk) - 1
        if end < len(lines):
            body += f"\n[lines {offset}-{end} of {len(lines)}; use offset to read more]"
        return body or "(empty file)"

    def t_list_files(self, path=".", glob=None):
        cmd = ["rg", "--files", str(self._path(path))]
        if glob:
            cmd[2:2] = ["--glob", glob]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        files = [os.path.relpath(f, self.root) for f in res.stdout.splitlines()]
        files.sort()
        most = self.limits.list_files
        if len(files) > most:
            return "\n".join(files[:most]) + f"\n[{len(files) - most} more; narrow it with glob]"
        return "\n".join(files) or "(no files)"

    def t_grep(self, pattern, path=".", glob=None):
        cmd = ["rg", "-n", "--max-columns", "300", "--max-count", "50", pattern, str(self._path(path))]
        if glob:
            cmd[1:1] = ["--glob", glob]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=self.root)
        if res.returncode == 1:
            return "no matches"
        if res.returncode > 1:
            return "error: " + res.stderr.strip()
        lines = [line.replace(str(self.root) + "/", "", 1) for line in res.stdout.splitlines()]
        most = self.limits.grep_matches
        if len(lines) > most:
            return "\n".join(lines[:most]) + f"\n[{len(lines) - most} more matches]"
        return "\n".join(lines)

    def t_fetch_url(self, url):
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (purr coding agent)"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            kind = resp.headers.get("Content-Type", "")
            raw = resp.read(2_000_000).decode(resp.headers.get_content_charset() or "utf-8", "replace")
        return html_to_text(raw) if "html" in kind else raw

    def t_edit_file(self, path, old_text, new_text, replace_all=False):
        p = self._path(path)
        before = p.read_text()
        count = before.count(old_text)
        if count == 0:
            loose = _loose_match(before, old_text, new_text)
            if not loose:
                return "error: old_text not found. Read the file again and copy the text exactly."
            old_text, new_text, count = *loose, 1
        if count > 1 and not replace_all:
            return f"error: old_text matches {count} times. Add more surrounding lines so it is unique."
        after = before.replace(old_text, new_text) if replace_all else before.replace(old_text, new_text, 1)
        self.view.diff(path, before, after)
        ok, reason = self._allowed("edit_file", f"edit {path}?")
        if not ok:
            return self._refused(reason)
        self._remember(p)
        p.write_text(after)
        return f"edited {path} ({count if replace_all else 1} change)"

    def t_write_file(self, path, content):
        p = self._path(path)
        before = p.read_text() if p.exists() else ""
        if p.exists():
            self.view.diff(path, before, content)
        else:
            self.view.note(f"new file, {len(content.splitlines())} lines")
        ok, reason = self._allowed("write_file", f"{'overwrite' if p.exists() else 'create'} {path}?")
        if not ok:
            return self._refused(reason)
        self._remember(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"wrote {path} ({len(content.splitlines())} lines)"

    def t_run(self, command, timeout=120):
        self.view.note(f"$ {command}")
        ok, reason = self._run_allowed(command)
        if not ok:
            return self._refused(reason)
        output, code = run_shell(command, self.root, timeout)
        for line in output.splitlines()[-4:]:
            self.view.note(line[:160])
        return f"{output}\n[exit code {code}]"

    def t_todo(self, items):
        clean = [{"text": str(i.get("text", "")), "status": i.get("status", "pending")}
                 for i in items if isinstance(i, dict)]
        self.view.todos(clean)
        left = sum(i["status"] != "done" for i in clean)
        return f"task list saved ({left} not done)"

    def t_task(self, prompt):
        if not self.spawn:
            return "error: helpers aren't available here"
        return self.spawn(prompt)


def run_shell(command, root, timeout=120):
    """Run a bash command. Returns (output, exit code); exit code -1 means it timed out."""
    try:
        res = subprocess.run(["bash", "-c", command], capture_output=True, text=True,
                             timeout=int(timeout), cwd=root, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s", -1
    return (res.stdout + res.stderr).strip(), res.returncode


class _TextOnly(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "section", "article"}

    def __init__(self):
        super().__init__()
        self.parts, self.skipping = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def html_to_text(raw):
    parser = _TextOnly()
    parser.feed(raw)
    text = "".join(parser.parts)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
