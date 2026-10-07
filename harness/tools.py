"""The model's hands: read, list, search, fetch, edit, write, run, todo, task.

Each tool = a schema (what the model sees) + a Python function (what actually happens).
Edits, writes and commands ask you first.
"""

import atexit
import difflib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from . import checks
from .limits import Limits
from .terminal import Terminals


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
    _schema("grep", "Search file contents with a regex (ripgrep). Returns file:line: text. "
            "All lower-case ignores case; use it to find code before reading whole files. "
            'Example: {"pattern": "def total", "glob": "*.py"}', {
        "pattern": {"type": "string"},
        "path": {"type": "string", "description": "File or folder. Default '.'"},
        "glob": {"type": "string", "description": "Only files matching, e.g. '*.py'"},
    }, ["pattern"]),
    _schema("fetch_url", "Download a web page (or any URL) and return it as plain text.", {
        "url": {"type": "string"},
    }, ["url"]),
    _schema("edit_file", "Replace exact text in a file. old_text must match exactly once "
            "(include enough surrounding lines), unless replace_all is true. Read the file first. "
            'Example: {"path": "shop.py", "old_text": "    return a - b", "new_text": "    return a + b"}', {
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
    _schema("terminal", "A terminal session for interactive programs (VM console, ssh, REPL) or a server to "
            "watch; run is for commands that finish. Actions start/send/read/stop/list. wait_for: regex to "
            "wait for, like login:. keys: e.g. Enter, C-c.", {
        "action": {"type": "string", "enum": ["start", "send", "read", "stop", "list"]},
        "name": {"type": "string", "description": "The session's name. Default 'main'."},
        "command": {"type": "string", "description": "start: the program to run (default: a shell)."},
        "text": {"type": "string", "description": "send: what to type."},
        "keys": {"type": "array", "items": {"type": "string"},
                 "description": "send: keys to press after the text (default Enter): Enter, Tab, Up, Down, C-c, C-d, Escape..."},
        "wait": {"type": "number", "description": "Seconds to wait for output. Default 2."},
        "wait_for": {"type": "string", "description": "A regex: wait (up to wait seconds) until it shows up."},
    }, ["action"]),
    _schema("look_at_image", "Look at an image file (png, jpg, gif, webp): it's shown to you right after.", {
        "path": {"type": "string"},
    }, ["path"]),
    _schema("todo", "A short task list for work with several steps. Send the whole list each time; "
            "one item 'doing'.", {
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"},
            "status": {"type": "string", "enum": ["pending", "doing", "done"]},
            "check": {"type": "string", "description": "optional: a shell command that exits 0 only when "
                      "this item holds (purr runs it again before the end)"},
        }, "required": ["text", "status"]}},
    }, ["items"]),
    _schema("task", "Hand a research job to a helper with a fresh, empty memory. It can only read "
            "(files, grep, web) and reports back a summary. Good for exploring lots of files "
            "without filling up this chat. Tell it exactly what to find out.", {
        "prompt": {"type": "string"},
    }, ["prompt"]),
]

# names models know from other harnesses -> purr's tools
TOOL_ALIASES = {"search": "grep", "grep_search": "grep", "ripgrep": "grep", "find_in_files": "grep",
                "read": "read_file", "view": "read_file", "cat": "read_file", "open_file": "read_file",
                "ls": "list_files", "list": "list_files", "glob": "list_files", "list_dir": "list_files",
                "edit": "edit_file", "str_replace": "edit_file", "replace": "edit_file",
                "write": "write_file", "create_file": "write_file",
                "bash": "run", "shell": "run", "exec": "run", "run_command": "run",
                "view_image": "look_at_image", "read_image": "look_at_image", "tmux": "terminal"}
ARG_ALIASES = {"file_path": "path", "filePath": "path", "filename": "path", "file": "path",
               "old_string": "old_text", "oldString": "old_text", "old_str": "old_text",
               "new_string": "new_text", "newString": "new_text", "new_str": "new_text",
               "cmd": "command", "query": "pattern", "regex": "pattern"}
UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")

READ_ONLY = {"read_file", "list_files", "grep", "fetch_url"}
ZERO_TESTS = re.compile(r"\bRan 0 tests\b|NO TESTS RAN|collected 0 items|\bno tests ran\b")
NUMBERED = re.compile(r"^ *\d+\t")  # read_file's line numbers, pasted into an edit
SKIP_DIRS = {"node_modules", "__pycache__", "venv", "dist", "build", "target"}  # besides hidden ones


def files_under(base, glob=None):
    """The files below base, as `rg --files` lists them. ripgrep when it's installed (fast, knows
    .gitignore); plain Python when it isn't (a fresh Mac or CI box), skipping hidden and build folders."""
    if shutil.which("rg"):
        cmd = ["rg", "--files", str(base)] + (["--glob", glob] if glob else [])
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.splitlines()
    base = Path(base)
    if base.is_file():
        return [str(base)]
    negate, glob = (glob or "").startswith("!"), (glob or "").lstrip("!")
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
                             and not (negate and fnmatch.fnmatch(d, glob)))  # "!tests": not that folder either
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if name.startswith(".") or (glob and negate == (fnmatch.fnmatch(name, glob)
                                                          or fnmatch.fnmatch(os.path.relpath(path, base), glob))):
                continue
            out.append(path)
    return out


def search(pattern, base, glob=None, fixed=False, per_file=50, columns=300):
    """`rg -n --smart-case` over base: (exit code, lines). 0 found, 1 nothing, 2 an error (the lines
    then say what). Plain Python when ripgrep isn't installed; a single file gives "line:text"."""
    if shutil.which("rg"):
        cmd = ["rg", "-n", "--smart-case", "--max-columns", str(columns), "--max-count", str(per_file)]
        cmd += (["--glob", glob] if glob else []) + (["--fixed-strings"] if fixed else []) + ["--", pattern, str(base)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return res.returncode, (res.stdout if res.returncode < 2 else res.stderr).strip().splitlines()
    if not Path(base).exists():
        return 2, [f"{base}: No such file or directory"]
    try:  # smart case: an all-lower-case pattern ignores case
        rx = re.compile(re.escape(pattern) if fixed else pattern, re.I if pattern == pattern.lower() else 0)
    except re.error as e:
        return 2, [f"regex parse error: {e}"]
    single, hits = Path(base).is_file(), []
    for f in files_under(base, glob):
        try:
            if os.path.getsize(f) > 2_000_000:
                continue  # a data file or a build: rg wouldn't show it usefully either
            text = Path(f).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable, as rg skips them
        if "\0" in text[:8000]:
            continue  # binary that happens to decode
        lines = text.splitlines()
        found = [(n, line) for n, line in enumerate(lines, 1) if rx.search(line)][:per_file]
        hits += [f"{n}:{line[:columns]}" if single else f"{f}:{n}:{line[:columns]}" for n, line in found]
    return (0 if hits else 1), hits
TOOL_NAMES = {s["function"]["name"] for s in SCHEMAS}  # an MCP tool with one of these names isn't offered
SHELL_CHAINING = set(";&|`$()<>\n")
# commands where the second word matters for "always allow" (git status vs git push)
TWO_WORD = {"git", "npm", "pnpm", "yarn", "uv", "pip", "cargo", "go", "docker", "systemctl", "godot"}


def schemas(read_only=False, read_lines=400, hidden=()):
    """The tool list for the model. read_lines is this model's read_file default; hidden
    tools aren't offered at all."""
    out = [s for s in SCHEMAS if (not read_only or s["function"]["name"] in READ_ONLY)
           and s["function"]["name"] not in hidden]
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


# a cut or pruned tool result is kept whole here, so the model reads parts of it instead of running a
# slow command again (it was told "save the output to a file" after the output was already gone)
SPILL_DIR = Path(tempfile.gettempdir()) / f"purr-out-{os.getpid()}"
SPILL_CAP = 50_000_000  # bytes: the oldest files go first
SPILL_FILES = 300       # and at most this many files (/tmp's inodes run out before its space does)


def clip(s, limit=MAX_OUTPUT, spilled=None):
    if len(s) <= limit:
        return s
    half = limit // 2
    where = (f"the whole output is in {spilled}: look at parts with grep -n or sed -n" if spilled
             else "save the output to a file and look at parts with grep or sed -n")
    return s[:half] + f"\n... [{len(s) - limit} chars cut; {where}] ...\n" + s[-half:]


def edit_window(after, before, size):
    """The numbered lines around the first change, for an edit's result."""
    old, new = before.splitlines(), after.splitlines()
    ops = [op for op in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if op[0] != "equal"]
    if not ops or not new:
        return ""
    first, last = ops[0][3], max(op[4] for op in ops)
    start = max(0, min(first, (first + last) // 2) - size // 4)
    end = min(len(new), start + size)
    lines = "\n".join(f"{n + 1:>5}\t{new[n]}" for n in range(start, end))
    return f"\nnow (lines {start + 1}-{end} of {len(new)}):\n{lines}"


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
    new_lines = new.strip("\n").splitlines()
    first = None  # (model's lead, real lead) of a first line indented on its own
    # how the model's indents map onto the file's real ones, e.g. 6 spaces -> 4 spaces or a tab
    imap = _indent_map(old_lines, real)
    if imap is None and len(old_lines) > 1:
        # only the first line off ("    def f():" for "def f():", gpt-oss): it alone maps its own way
        imap = _indent_map(old_lines[1:], real[1:])
        if imap is None or not new_lines or _lead(new_lines[0]) != _lead(old_lines[0]):
            return None
        first = (_lead(old_lines[0]), _lead(real[0]))
    if not imap:
        imap = {first[0]: first[1]} if first else {"": ""}
    keys = sorted(imap, key=len)
    fixed = []
    for i, l in enumerate(new_lines):
        lead, body = _lead(l), l.lstrip(" \t")
        if not body:
            fixed.append("")
        elif i == 0 and first:
            fixed.append(first[1] + body)
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

def _unnumbered(text):
    """text without read_file's line numbers: "   12\t" prefixes go, and a line that is only a number at
    either end goes (an empty line, copied with its number). Unchanged when it has neither."""
    lines = text.split("\n")
    numbered = sum(1 for line in lines if NUMBERED.match(line))
    bare = [i for i in (0, len(lines) - 1) if lines and re.fullmatch(r" *\d+ *", lines[i])]
    if not numbered and not bare:
        return text
    lines = [NUMBERED.sub("", line, count=1) for line in lines]
    for i in sorted(set(bare), reverse=True):
        if len(lines) > 1:
            del lines[i]
    return "\n".join(lines)


def _indent_map(model_lines, real_lines):
    """{model's indent: the file's} for lines that match ignoring indents; None if they disagree."""
    imap = {}
    for o, r in zip(model_lines, real_lines):
        if o.strip() and imap.setdefault(_lead(o), _lead(r)) != _lead(r):
            return None
    return imap


class Tools:
    def __init__(self, root, view, read_only=False, allow_run=(), limits=None):
        self.root = Path(root).resolve()
        self.view = view
        self.read_only = read_only
        self.no_tools = False  # chat and create mode
        self.repairs = []      # model slips purr fixed by itself (the benchmark counts them)
        self.code_checks = True  # syntax + lint after edits, placeholder guard (checks.py)
        self.warnings = []     # problems those checks pointed out (the benchmark counts them)
        self.rewrite_ok = set()  # (path, content hash) rewrites the model insisted on
        self.read_before_edit = True  # edits only on files read this session
        self.seen = set()        # files read (or written) so far
        self.limits = limits or Limits.for_model({})
        self.step = 0         # tool calls so far: spill files and stale marks say "as of step N"
        self.last_spill = None  # where the last call's whole output went, if it was cut
        self.allow_run = list(allow_run)  # patterns from config.toml that never ask
        self.always = set()      # tools you said "always" to this session
        self.always_run = set()  # command heads you said "always" to ("git status", "python3")
        self.todos_left = 0      # items on the todo list that aren't done
        self.trust_all = False
        self.halt = False        # a plain "no": stop and let the user say what to do next
        self.undo_stack = []     # one {path: text before, or None if it didn't exist} per turn
        self.spawn = None        # set by the agent: runs a helper for the task tool
        self.terminals = Terminals(self.root)  # sessions for interactive programs (terminal tool)
        atexit.register(self.terminals.close_local)  # purr's own (non-tmux) sessions end with it
        self.pending_images = []  # (path, data URL) look_at_image read: the agent shows them next
        self.outside_ok = []     # one-shot runs: folders and files outside the project it may write
        self.deadline = None     # time.monotonic() when a time-limited run ends: run's timeout stops there
        self.edit_gen = 0        # goes up with every edit: re-reading a file after one isn't a repeat
        self.todo_list = []      # the last task list (a compaction keeps its open items)
        self.compiled = {}       # Python file -> its last text that compiled (the safety net's fallback)

    def _path(self, p):
        p = Path(p).expanduser()
        if not p.is_absolute() and not (self.root / p).exists() and str(p).startswith(str(self.root)[1:]):
            # "home/me/project/a.py": an absolute path that lost its first "/"
            self.repairs.append("path missing its leading / -> fixed")
            p = Path("/" + str(p))
        return p if p.is_absolute() else self.root / p

    def _no_such_file(self, path):
        """A missing file, with the project's files whose names come closest, so a guessed path
        ("build_order/README.md") turns into the right one ("README.md") in one step."""
        files = self.t_list_files(".").splitlines()
        name = Path(path).name
        same = [f for f in files if Path(f).name == name]
        close = same or difflib.get_close_matches(str(path), files, n=3, cutoff=0.5)
        if close:
            return f"error: no file {path}. Did you mean {', '.join(close[:3])}?"
        return f"error: no file {path}. The files are: " + ", ".join(files[:20]) + (" …" if len(files) > 20 else "")

    def _outside(self, path):
        """An error for writing outside the project folder, or None. Small models garble long
        absolute paths, and writing would create folders wherever the typo points."""
        p = self._path(path).resolve()
        if p == self.root or self.root in p.parents:
            return None
        # a task that names /app/out.txt or says "scratch files in /tmp" means it
        if any(p == ok or ok in p.parents for ok in self.outside_ok):
            return None
        return (f"error: {path} is outside the project folder ({self.root}). Use paths inside it (like "
                "src/app.py); if you really mean that path, use run.")

    def summary(self, name, args):
        """One short line for the screen."""
        key = {"read_file": "path", "list_files": "path", "grep": "pattern", "edit_file": "path",
               "write_file": "path", "run": "command", "fetch_url": "url", "task": "prompt",
               "outline": "path", "find_symbol": "name", "look_at_image": "path",
               "godot_class": "name", "python_api": "name"}.get(name)
        if name == "terminal":
            what = args.get("command") or args.get("text") or " ".join(args.get("keys") or [])
            return f"terminal {args.get('action', '')} {args.get('name', 'main')} {str(what)[:80]}".strip()
        if name == "godot_class" and args.get("member"):
            return f"{name} {args.get('name', '')}.{args['member']}"
        val = str(args.get(key, "")) if key else ""
        return f"{name} {val[:100]}".strip()

    def call(self, name, raw_args):
        """Run one tool call. Always returns a string for the model."""
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError
        except ValueError:
            return self._failed(name, {}, f"error: arguments were not valid JSON: {raw_args[:300]}")
        # models trained on other harnesses use their names: say grep for search, path for file_path
        wanted = name
        mcp = getattr(self, "mcp", None)
        if mcp and mcp.has(wanted) and not hasattr(self, "t_" + wanted):
            # an MCP tool by exactly this name (search, read, exec...) is what was offered: an alias
            # mustn't send its calls to purr's own tool instead
            return self._mcp_call(wanted, args)
        name = TOOL_ALIASES.get(re.sub(r"[^\w]", "", name), name)
        if name != wanted:
            self.repairs.append(f"tool {wanted} -> {name}")
        # keys with stray whitespace ("\nnew_text", gpt-oss) are the key without it
        for key in [k for k in args if isinstance(k, str) and k != k.strip() and k.strip() not in args]:
            args[key.strip()] = args.pop(key)
            self.repairs.append(f"argument {key.strip()} had spaces around its name -> fixed")
        for alias, real in ARG_ALIASES.items():
            if alias in args and real not in args:
                args[real] = args.pop(alias)
                self.repairs.append(f"argument {alias} -> {real}")
        fn = getattr(self, "t_" + name, None)
        if fn is None and not self.read_only and not self.no_tools:
            command = self._as_command(wanted, args)
            if command:  # objdump, size...: a program on this machine, called as if it were a tool
                self.repairs.append(f"tool {wanted} -> run {command[:60]}")
                return self.call("run", json.dumps({"command": command}))
        if fn is None or (self.read_only and name not in READ_ONLY):
            hidden = set(getattr(self, "hidden_fn", lambda: ())())  # what this model isn't offered
            have = sorted((TOOL_NAMES - hidden) if not self.read_only else READ_ONLY - hidden)
            return self._failed(wanted, args, f"error: there is no tool called {wanted}. The tools: {', '.join(have)}"
                                + ("" if self.read_only else "; programs and shell commands go through run"))
        if self.no_tools:
            return f"error: no tools in this mode ({name} can't run)"
        self.view.activity(name, self.summary(name, args)[len(name):].strip())
        if name != "todo":
            self.view.tool(self.summary(name, args))
        self.step += 1
        self.last_spill = None
        # arguments the tool doesn't have are dropped, not fatal: gpt-oss added replace_whole_file
        # to every edit_file call, and each failed whole ("unexpected keyword argument")
        import inspect
        known = inspect.signature(fn).parameters
        extra = [k for k in args if k not in known]
        if extra and not any(p.kind == p.VAR_KEYWORD for p in known.values()):
            for k in extra:
                args.pop(k)
                self.repairs.append(f"unknown argument {k} for {name} -> dropped")
        try:
            raw = fn(**args)
            if isinstance(raw, str) and len(raw) > self.limits.tool_output:
                self.last_spill = self.spill(name, raw)
            result = clip(raw, self.limits.tool_output, self.last_spill)
        except TypeError as e:
            missing = re.search(r"missing \d+ required positional arguments?: (.+)", str(e))
            result = (f"error: {name} needs {missing.group(1).replace(chr(39), '')}" if missing
                      else f"error: wrong arguments for {name}: {e}")
        except Exception as e:
            result = f"error: {type(e).__name__}: {e}"
        if name != "todo":
            self.view.tool_result(name, args, result)  # the exact call and what the model got back
        return result

    def _as_command(self, name, args):
        """A made-up tool that is really a program on this machine (gpt-oss called objdump and size
        as tools): the shell command it meant, or None. Its arguments become the command line."""
        import shlex
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", name or "") or not shutil.which(name):
            return None
        rest = next((args[k] for k in ("command", "args", "arguments", "cmd", "argv", "options") if k in args), None)
        if isinstance(rest, list):
            rest = " ".join(shlex.quote(str(x)) for x in rest)
        elif rest is None:
            rest = " ".join(shlex.quote(str(v)) for v in args.values() if isinstance(v, (str, int, float)))
        return f"{name} {rest}".strip()

    def spill(self, name, text):
        """Keep a whole tool result in a file of its own (SPILL_DIR); returns its path, or None in
        private mode (nothing kept) or when it can't be written."""
        if getattr(self, "is_private", lambda: False)():
            return None
        try:
            if not SPILL_DIR.exists():  # purr's own: gone when purr exits
                atexit.register(shutil.rmtree, SPILL_DIR, ignore_errors=True)
            SPILL_DIR.mkdir(parents=True, exist_ok=True)
            path = SPILL_DIR / f"{self.step:04d}-{re.sub(r'[^A-Za-z0-9_-]', '', name)[:30] or 'tool'}.txt"
            path.write_text(text)
            files = sorted(SPILL_DIR.iterdir(), key=lambda f: f.stat().st_mtime)
            total = sum(f.stat().st_size for f in files)
            while (total > SPILL_CAP or len(files) > SPILL_FILES) and len(files) > 1:
                total -= files[0].stat().st_size
                files.pop(0).unlink()
            return str(path)
        except OSError:
            return None

    def _mcp_call(self, name, args):
        if self.no_tools:
            return f"error: no tools in this mode ({name} can't run)"
        if not self.mcp.read_only(name):  # it may change things: ask first, like run
            if self.read_only:
                return self._failed(name, args, f"error: there is no tool called {name}")
            ok, reason = self._allowed(name, f"let the MCP tool {name} run? {json.dumps(args)[:200]}")
            if not ok:
                return f"the user said no{': ' + reason if reason else ''}"
        self.view.activity(name, self.summary(name, args)[len(name):].strip())
        self.view.tool(self.summary(name, args))
        result = clip(self.mcp.call(name, args), self.limits.tool_output)
        self.view.tool_result(name, args, result)
        return result

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

    def _stray_quote(self, path, content):
        """A Python file written with one stray quote at its very end (gpt-oss: ...return total\\n" ), which
        doesn't compile while the text without it does: the quote goes. The model couldn't edit a lone \"
        away (it matches everywhere)."""
        tail = content.rstrip()
        if not str(path).endswith(".py") or not tail.endswith(('"', "'")):
            return content
        try:
            compile(content, str(path), "exec")
            return content
        except (SyntaxError, ValueError):
            pass
        fixed = tail[:-1].rstrip() + "\n"
        try:
            compile(fixed, str(path), "exec")
        except (SyntaxError, ValueError):
            return content
        self.repairs.append("write_file content ended with a stray quote -> removed")
        return fixed

    def _compiles(self, p, text):
        """Note text as p's last version that compiles (Python files only)."""
        if p.suffix == ".py" and text is not None:
            try:
                compile(text, str(p), "exec")
                self.compiled[p] = text
            except (SyntaxError, ValueError):
                pass

    def _remember(self, p):
        """Keep a file's old text the first time this turn changes it, for /undo."""
        if p.suffix == ".py" and p not in self.compiled and p.exists():
            self._compiles(p, p.read_text(errors="replace"))
        self.edit_gen += 1
        if not self.undo_stack:
            self.begin_turn()
        changes = self.undo_stack[-1]
        if p not in changes:
            changes[p] = p.read_text() if p.exists() else None

    def session_changes(self):
        """Every file changed this session: {path: its text before the first change, or None if
        it was new}. Undone turns are gone from the stack, so they drop out too."""
        changes = {}
        for turn in self.undo_stack:
            for p, before in turn.items():
                changes.setdefault(p, before)
        return changes

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

    def t_read_file(self, path, offset=1, limit=None, line_start=None, line_end=None, line_range=None):
        if line_range is not None and line_start is None:  # gpt-oss: [10, 40] or "10-40"
            nums = [int(n) for n in re.findall(r"\d+", str(line_range))][:2]
            if nums:
                line_start, line_end = nums[0], (nums[1] if len(nums) > 1 else None)
        if line_start is not None:  # what some models (gpt-oss) call it
            self.repairs.append("read_file line_start/line_end -> offset/limit")
            offset = int(line_start)
            if line_end is not None:
                limit = max(1, int(line_end) - offset + 1)
        p = self._path(path)
        if not p.is_file():
            return self._no_such_file(path)
        lines = p.read_text(errors="replace").splitlines()
        self.seen.add(p.resolve())
        offset = max(1, int(offset))
        limit = self.limits.read_lines if limit is None else int(limit)
        room = self.limits.tool_output - 200  # whole lines only: clip() would cut out the middle
        out, size = [], 0
        for n, line in enumerate(lines[offset - 1: offset - 1 + limit], offset):
            text = f"{n:>5}\t{line[:500]}" + (f"…[+{len(line) - 500} chars]" if len(line) > 500 else "")
            if out and size + len(text) + 1 > room:
                break
            out.append(text)
            size += len(text) + 1
        body = "\n".join(out)
        end = offset + len(out) - 1
        if end < len(lines):
            body += f"\n[lines {offset}-{end} of {len(lines)}; next: offset={end + 1}]"
        return body or "(empty file)"

    def t_list_files(self, path=".", glob=None):
        files = [os.path.relpath(f, self.root) for f in files_under(self._path(path), glob)]
        files.sort()
        most = self.limits.list_files
        if len(files) > most:
            return "\n".join(files[:most]) + f"\n[{len(files) - most} more; narrow it with glob]"
        return "\n".join(files) or "(no files)"

    def t_grep(self, pattern, path=".", glob=None):
        # smart case: "discount" also finds HAPPY_HOUR_DISCOUNT; "Discount" only matches exactly.
        # Without it a model's lower-case search comes back empty and it reads whole files instead.
        code, found = search(pattern, self._path(path), glob)
        if code == 1:
            return f"no matches for {pattern!r}; try a shorter or different word"
        if code > 1:
            return "error: " + "\n".join(found)
        lines = [line.replace(str(self.root) + "/", "", 1) for line in found]
        most = self.limits.grep_matches
        if len(lines) > most:
            return "\n".join(lines[:most]) + f"\n[{len(lines) - most} more matches]"
        return "\n".join(lines)

    def _benchmark_lookup(self, text):
        """In a benchmark run: the refusal for a call that would look up the benchmark itself, or None."""
        if not getattr(self, "bench_guard", False):
            return None
        from harness import benchguard
        name = benchguard.lookup(text)
        if not name:
            return None
        self.repairs.append("looked up the benchmark itself -> refused")
        return benchguard.REFUSAL.format(name=name)

    def t_fetch_url(self, url):
        if getattr(self, "is_private", lambda: False)():
            return "error: private mode: no web (nothing leaves this computer)"
        refused = self._benchmark_lookup(f"https://{url}" if "://" not in url else url)
        if refused:
            return refused
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (purr coding agent)"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            kind = resp.headers.get("Content-Type", "")
            raw = resp.read(2_000_000).decode(resp.headers.get_content_charset() or "utf-8", "replace")
        return html_to_text(raw) if "html" in kind else raw

    def _failed(self, name, args, error):
        """A call that couldn't even start: still show it (and let the benchmark count it)."""
        if not self.no_tools:
            self.view.tool(f"{name} {json.dumps(args)[:80]}")
            self.view.tool_result(name, args, error)
        return error

    def t_edit_file(self, path, old_text=None, new_text=None, replace_all=False, content=None,
                    line_start=None, line_end=None):
        if old_text is None and content is not None and line_start is None:  # it meant "replace the whole file"
            self.repairs.append("edit_file with only content -> write_file")
            return self.t_write_file(path, content)
        if old_text is None and line_start is not None and (new_text is not None or content is not None):
            # gpt-oss edits by line numbers: those lines (as read_file numbers them) are the old text
            p = self._path(path)
            if p.is_file() and (not self.read_before_edit or p.resolve() in self.seen):
                lines = p.read_text().splitlines(keepends=True)
                start, end = int(line_start), int(line_end if line_end is not None else line_start)
                if 1 <= start <= end <= len(lines):
                    old_text = "".join(lines[start - 1:end]).rstrip("\n")
                    new_text = (new_text if new_text is not None else content).rstrip("\n")
                    self.repairs.append("edit_file by line numbers -> those lines' text")
        if old_text is None or new_text is None:
            return ("error: edit_file needs old_text (copied from the file) and new_text. "
                    "To replace the whole file, use write_file(path, content).")
        if self.code_checks and old_text and new_text:
            hole = checks.placeholder(old_text, new_text)
            if hole:
                self.warnings.append(f"{path}: placeholder in edit refused")
                return (f"error: new_text contains the placeholder {hole!r} instead of real code; that would "
                        "delete code. Put the real lines in new_text.")
        p = self._path(path)
        outside = self._outside(path)
        if outside:
            return outside
        if not p.exists():  # a guessed path: the closest real ones, not a bare FileNotFoundError
            return self._no_such_file(path)
        if self.read_before_edit and p.exists() and p.resolve() not in self.seen:
            self.warnings.append(f"{path}: edit before reading refused")
            return (f"error: read {path} first (read_file), so your old_text matches what is really in it. "
                    "Then make the edit.")
        before = p.read_text()
        count = before.count(old_text)
        if count == 0 and "\\u00" in old_text:  # "\u003c" written out instead of "<"
            fixed = [UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), t) for t in (old_text, new_text)]
            if before.count(fixed[0]):
                self.repairs.append("unescaped \\u00.. in edit_file")
                old_text, new_text = fixed
                count = before.count(old_text)
        if count == 0:
            # copied from read_file with its "   12\t" numbers (17 failed edits in purr bench), or with a
            # bare "    12" line for an empty one (gpt-oss)
            plain = _unnumbered(old_text)
            if plain != old_text and before.count(plain) == 1:
                old_text, new_text, count = plain, _unnumbered(new_text), 1
                self.repairs.append("edit_file old_text had read_file's line numbers -> stripped")
        if count == 0:
            loose = _loose_match(before, old_text, new_text)
            if not loose:
                return self._not_found(path, before, old_text)
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
        self._compiles(p, after)
        window = edit_window(after, before, self.limits.edit_window) if self.limits.edit_window else ""
        return (f"edited {path} ({count if replace_all else 1} change)" + window
                + self._check_code(path, before, after))

    def _not_found(self, path, text, old_text):
        """old_text isn't in the file: say where it is instead, or show the closest lines,
        so the model can fix its edit without reading everything again."""
        msg = f"error: old_text not found in {path}."
        elsewhere = []
        for f in self.t_list_files(".").splitlines()[:2000]:
            p = self.root / f
            if f == path or not p.is_file() or p.stat().st_size > 400_000:
                continue
            try:
                body = p.read_text(errors="strict")
            except (OSError, UnicodeDecodeError):
                continue
            if old_text in body:
                elsewhere.append(f"{f} (line {body[:body.index(old_text)].count(chr(10)) + 1})")
        # it's also in another file: say so, but as a maybe. gpt-oss adding an import to crafting.py
        # quoted save.py's, was told "edit that file instead" and edited save.py three times
        there = (f" That exact text is in {', '.join(elsewhere[:3])}: if you meant that file, edit it there."
                 if elsewhere else "")
        lines, want = text.splitlines(), old_text.strip("\n").splitlines()
        head = re.match(r"\s*(?:async\s+)?(def|class|func|fn|function)\s+(\w+)", want[0] if want else "")
        if head and not re.search(rf"\b{head.group(1)}\s+{re.escape(head.group(2))}\b", text):
            # gpt-oss kept editing a method an earlier edit of its own had removed
            there += (f" There is no `{head.group(1)} {head.group(2)}` in {path} now (misspelled, or an earlier "
                      "change removed it; to add it back, put it next to code that is there).")
        if not want or not lines:
            return msg + there + " Read the file again and copy the text exactly."
        size = len(want)
        best, at = 0.0, 0
        for i in range(max(1, len(lines) - size + 1)):
            r = difflib.SequenceMatcher(None, "\n".join(lines[i:i + size]), "\n".join(want)).quick_ratio()
            if r > best:
                best, at = r, i
        if best < 0.5:
            return msg + there + " Read the file again and copy the text exactly."
        shown = "\n".join(f"{n:>5}\t{line}" for n, line in enumerate(lines[at:at + size], at + 1))
        return (msg + there + f" In {path}, the closest lines are {at + 1}-{at + size} (copy them exactly, "
                f"without the line numbers):\n{shown}")

    def t_write_file(self, path, content):
        p = self._path(path)
        outside = self._outside(path)
        if outside:
            return outside
        if self.read_before_edit and p.exists() and p.resolve() not in self.seen:
            self.warnings.append(f"{path}: overwrite before reading refused")
            return f"error: {path} already exists: read it first (read_file) before replacing it."
        before = p.read_text() if p.exists() else ""
        content = self._stray_quote(path, content)
        stop = self._suspicious_rewrite(path, before, content)
        if stop:
            return stop
        self.view.diff(path, before, content)  # a new file shows as a preview of its start
        ok, reason = self._allowed("write_file", f"{'overwrite' if p.exists() else 'create'} {path}?")
        if not ok:
            return self._refused(reason)
        self._remember(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        self._compiles(p, content)
        self.seen.add(p.resolve())  # it knows what it just wrote
        window = edit_window(content, before, self.limits.edit_window) if self.limits.edit_window and before else ""
        return f"wrote {path} ({len(content.splitlines())} lines)" + window + self._check_code(path, before, content)

    # ---- checks on the model's code (harness/checks.py); code_checks = false turns them off ----

    def _check_code(self, path, before, after):
        """After a change: syntax and real-bug lint on the new code, added to the tool result."""
        if not self.code_checks:
            return ""
        problems = checks.code_problems(path, before, after)
        lost = checks.removed_definitions(path, before, after)
        if lost:  # gpt-oss replaced the start of __init__ with another method: the constructor was gone
            self.warnings.append(f"{path}: removed {lost[0]}")
            problems = [f"this change removed {', '.join(lost[:4])} (no longer in the file): if that wasn't "
                        "meant, put it back"] + problems
        weakened = checks.weakened_tests(path, before, after)
        if weakened:
            self.warnings.append(f"{path}: test expectations changed")
            return ("\n⚠ you changed or removed what a test checks:\n" + "\n".join("  - " + x for x in weakened)
                    + "\nIf the code is wrong, fix the code instead. Only change a test when the test itself "
                    "is wrong, and say why." + ("\n⚠ also: " + "; ".join(problems) if problems else ""))
        if not problems:
            return ""
        self.warnings.append(f"{path}: {problems[0]}")
        return ("\n⚠ purr checked the new code and found problems:\n" + "\n".join("  " + x for x in problems)
                + "\nFix them before going on.")

    def _suspicious_rewrite(self, path, before, content):
        """A rewrite that would quietly delete code: a "...rest unchanged" placeholder, or a big
        file shrinking to a fraction. Refused once; sent again unchanged, it goes through."""
        if not self.code_checks or not before:
            return None
        key = (path, hash(content))
        if key in self.rewrite_ok:
            return None
        hole = checks.placeholder(before, content)
        old_n, new_n = len(before.splitlines()), len(content.splitlines())
        tests = re.compile(r"^\s*(import unittest|import pytest|from unittest\b)|unittest\.TestCase\b|^def test_", re.M)
        if hole:
            why = (f"it contains the placeholder {hole!r} instead of the real code, so saving it would "
                   "delete that code")
        elif old_n >= 40 and new_n < old_n * 0.5:
            why = f"{path} would shrink from {old_n} to {new_n} lines"
        elif tests.search(content) and not tests.search(before) and not checks.TEST_FILE.search(str(path)):
            # Ornith-9B, wrapping up lru-cache, wrote its tests over cache.py itself
            why = (f"{path} holds code, not tests, and this would replace it with a test module (did you "
                   "mean a test file, like test_" + Path(path).name + "?)")
        else:
            return None
        self.rewrite_ok.add(key)
        self.warnings.append(f"{path}: rewrite refused once ({why[:60]})")
        return (f"error: not written: {why}. Write the complete file, or use edit_file to change only the "
                "part that needs changing. (If this really is what you want, send the same write again.)")

    def t_run(self, command, timeout=120):
        refused = self._benchmark_lookup(command)
        if refused:
            return refused
        ok, reason = self._run_allowed(command)
        if not ok:
            return self._refused(reason)
        cut = ""
        if self.deadline:
            import time
            room = int(max(30, self.deadline - time.monotonic() - 60))
            if int(timeout) > room:
                timeout = room
                cut = f"(purr: timeout cut to {room}s: that is all the time left)\n"
        output, code = run_shell(command, self.root, timeout)
        if ZERO_TESTS.search(output) or (re.search(r"\btests?\b|unittest|pytest", command)
                                         and "No module named" in output):
            right = checks.test_command(self.root)
            ran = re.sub(r"^\s*cd\s+\S+\s*&&\s*", "", command).strip()
            if right and ran != right:  # a guessed test command that found nothing
                output += f"\n(purr: no tests ran there. This project's tests run with `{right}` from the project folder.)"
        for line in output.splitlines()[-4:]:
            self.view.note(line[:160])
        if code == -1 and output.startswith("timed out"):
            # builds and training runs often need longer than the default: say how, or the model
            # tends to give up on the step or retry it the same way
            output += (f"\n(purr: it was stopped after {timeout}s. If it needs longer, run it again with "
                       "a bigger timeout (run's timeout argument, in seconds), or start it in the background "
                       "with its output in a log (nohup <command> > run.log 2>&1 &) and check run.log.)")
            if self.deadline:  # a time limit: a bigger timeout may not fit at all
                output = output[:-1] + (" With the time you have, first time a small piece of it (fewer "
                                        "epochs, samples or files) and size the full run to fit.)")
        return f"{cut}{output}\n[exit code {code}]"

    def t_terminal(self, action, name="main", command=None, text=None, keys=None, wait=None,
                   wait_for=None, lines=40):
        t = self.terminals
        try:
            if action == "list":
                return t.list()
            if action == "read":
                return t.read(name, wait or 0, wait_for, lines)
            if action == "stop":
                return t.stop(name)
            if action not in ("start", "send"):
                return "error: action is start, send, read, stop or list"
            what = command if action == "start" else (text or " ".join(keys or []))
            refused = self._benchmark_lookup(what or "")
            if refused:
                return refused
            ok, reason = self._allowed("terminal", f"terminal {action} ({name}): {what or 'a shell'}")
            if not ok:
                return self._refused(reason)
            if action == "start":
                return t.start(name, command, 2 if wait is None else wait, wait_for, lines)
            return t.send(name, text, keys, 2 if wait is None else wait, wait_for, lines)
        except (ValueError, OSError, subprocess.SubprocessError) as e:
            return f"error: {e}"

    IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                   ".webp": "image/webp"}

    def t_look_at_image(self, path):
        import base64
        p = self._path(path)
        if not p.is_file():
            return self._no_such_file(path)
        kind = self.IMAGE_TYPES.get(p.suffix.lower())
        if not kind:
            return f"error: {path} isn't an image look_at_image can show (png, jpg, gif, webp)"
        if p.stat().st_size > 8_000_000:
            return f"error: {path} is too big to show ({p.stat().st_size // 1_000_000} MB, the limit is 8)"
        url = f"data:{kind};base64," + base64.b64encode(p.read_bytes()).decode()
        self.pending_images.append((path, url))
        return f"(the image {path} comes right after this)"

    def t_todo(self, items):
        clean = [{"text": str(i.get("text", "")), "status": i.get("status", "pending"),
                  **({"check": i["check"].strip()} if isinstance(i.get("check"), str) and i["check"].strip() else {})}
                 for i in items if isinstance(i, dict)]
        self.view.todos(clean)
        self.todo_list = clean
        self.todos_left = sum(i["status"] != "done" for i in clean)
        if not self.todos_left:
            return ("task list saved: everything is done. If the work is finished, give the user "
                    "your final reply now, without calling any tool.")
        nxt = next((i for i in clean if i["status"] == "doing"), None) or next(
            i for i in clean if i["status"] != "done")
        return f"task list saved; next: {nxt['text'][:80]}"

    def t_task(self, prompt):
        if not self.spawn:
            return "error: helpers aren't available here"
        return self.spawn(prompt)


def child_env(root, **extra):
    """The environment for the model's commands: without purr's own Python venv (`uv run purr`, a venv
    install), so python3 and pip are the machine's or the project's, not purr's. In purr bench,
    `python3 -m unittest discover -s tests` in a task without tests/ ran purr's own 453 tests.
    A venv inside the project folder is the project's own and stays."""
    # no .pyc files: a file rewritten in the same second at the same size runs its old bytecode, and
    # models blamed "stale bytecode" for their own bugs anyway
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **extra}
    if sys.prefix != sys.base_prefix and not Path(sys.prefix).resolve().is_relative_to(Path(root).resolve()):
        bin_dir = os.path.realpath(Path(sys.prefix) / "bin")
        env["PATH"] = os.pathsep.join(d for d in env.get("PATH", "").split(os.pathsep)
                                      if d and os.path.realpath(d) != bin_dir)
        if env.get("VIRTUAL_ENV") and os.path.realpath(env["VIRTUAL_ENV"]) == os.path.realpath(sys.prefix):
            del env["VIRTUAL_ENV"]
    return env


def run_shell(command, root, timeout=120):
    """Run a bash command. Returns (output, exit code); exit code -1 means it timed out.
    Output goes to a temporary file, not a pipe: a server started in the background (`cmd &`)
    keeps a pipe open forever, which used to stall the call until the timeout and then kill the
    server. Now the call returns when bash does, and the server keeps running. On a timeout the
    command's whole process group is stopped."""
    fd, ps_file = tempfile.mkstemp(prefix="purr-ps-")
    os.close(fd)
    os.unlink(ps_file)  # bash writes it only if the command got to its end
    # what a pipe's earlier commands exited with ("make | tail" shows tail's 0). Not after a
    # here-document: one missing its end marker would swallow the extra line.
    script = command if "<<" in command else command + PIPE_TRAILER
    try:
        with tempfile.TemporaryFile() as out:
            proc = subprocess.Popen(["bash", "-c", script], stdout=out, stderr=subprocess.STDOUT, cwd=root,
                                    stdin=subprocess.DEVNULL, start_new_session=True,
                                    env=child_env(root, PURR_PS=ps_file))
            try:
                code = proc.wait(timeout=int(timeout))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, 9)
                except ProcessLookupError:
                    pass
                proc.wait()
                partial = _read_output(out)
                return f"timed out after {timeout}s" + (f"; its output so far:\n{partial[-4000:]}" if partial else ""), -1
            text = _read_output(out)
        return (text + _pipe_note(ps_file, code)).lstrip("\n"), code
    finally:
        if os.path.exists(ps_file):
            os.unlink(ps_file)


# appended to a command: the exit codes of its last pipeline, and its own exit code kept as it was
PIPE_TRAILER = '\n__purr_ps="${PIPESTATUS[*]} $?"; echo "$__purr_ps" > "$PURR_PS"; exit "${__purr_ps##* }"'


def _read_output(out):
    """A command's output as text: bytes that aren't UTF-8 don't lose the rest, and a progress
    bar's \r updates come out as just their last state."""
    out.seek(0)
    text = out.read().decode("utf-8", errors="replace")
    if "\r" in text:
        text = "\n".join(line.rstrip("\r").rsplit("\r", 1)[-1] for line in text.split("\n"))
    return text.strip()


def _pipe_note(ps_file, code):
    """A pipe whose earlier command failed while the last one (tail, grep, tee) worked."""
    try:
        codes = [int(c) for c in Path(ps_file).read_text().split()][:-1]  # the last one is $?
    except (OSError, ValueError):
        return ""
    bad = [c for c in codes[:-1] if c not in (0, 141)]  # 141: stopped by a closed pipe (head), fine
    if code != 0 or not bad or len(codes) < 2:
        return ""
    return (f"\n(purr: an earlier command in the pipe exited {bad[0]}; the exit code shown is only "
            "the last command's)")


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
