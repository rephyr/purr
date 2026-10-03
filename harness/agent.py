"""The heart of purr: send the chat, run the tools the model asks for, repeat.

Also: compacting long chats, saving/resuming sessions, undo, helpers (sub-agents),
@file attachments and !shell commands.
"""

import datetime
import difflib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import ui
from .api import ApiError, Stopped, stream_chat
from .limits import LOCAL, Limits
from .free import FreeRouter, rest_for
from .mcp import Mcp
from . import agents as agent_files, checks, minimal
from .prompts import (  # noqa: F401 - the words purr says; some only re-exported for others
    SYSTEM, ASK_TOOLS, LEARN, LEARN_NUDGE, LEARN_SHORTEN, PAIR,
    PAIR_HAND_BACK, CHAT, CREATE, PLAN, TICKET_WORK, TIME_INTRO,
    TIME_NOTES, CUT_NUDGE, EMPTY_NUDGE, SERVICES, FINAL_CHECK_LIGHT, FINAL_CHECK,
    ONE_SHOT_CHECK, EVIDENCE_PASS, SCRATCH_NOTE, ONE_SHOT_SHORT_CHECK, NOBODY, REFINE,
    HELPER, REVIEW, REVIEW_NOTE, COMPACT, TASK_LINE, ASK_LINE,
    ONE_SHOT_LINE, IDENTITY_TAIL, APPROVE_LINE, FOLDER_LINE, STEP_BACK, CHECKPOINT, CUT_TAIL, CUT_ACT,
)
from .tools import READ_ONLY, TOOL_ALIASES, TOOL_NAMES, Tools, clip, run_shell, schemas


LEARN_TODO_LINES = 4  # a longer TODO(you) comment has usually written out the answer

TODO_YOU = "TODO(you)"


PAIR_SNAPSHOT_FILES = 3000  # files remembered to spot the user's own edits (text, < 200 kB each)
PAIR_DIFF_CHARS = 6000      # the most of the user's changes shown to the model at once


# mode -> (which tools, what it's for). The TUI's chip and /mode use this.
MODES = {
    "code": ("all", "does the work: reads, edits, runs"),
    "ask": ("read", "looks at the project and explains, never changes anything"),
    "learn": ("all", "you learn by doing: purr writes the boring parts and leaves the key lines to you"),
    "pair": ("all", "pair programming: you take turns, one small step each"),
    "plan": ("all", "a big model splits the task into tickets, then a small one works through them"),
    "chat": ("none", "just talking, no tools"),
    "create": ("none", "brainstorming and writing, no tools, a bit more random"),
}


REASONING_KEEP = 2  # past replies whose thinking goes back to providers that want it (echo_reasoning)
IMAGES_KEEP = 3     # images (look_at_image) sent again with every request: only the latest few


TEST_TIMEOUT = 180  # seconds for a run of the project's tests (the final check, plan mode)
REMIND_EVERY = 12  # model calls between checkpoints (CHECKPOINT; reminders = false turns them off)
STEP_BACK_EDITS = 8   # changes to the same file before purr asks for a step back (step_back = false: never)
STEP_BACK_TWEAKS = 6  # versions of the same command with only its numbers changed, likewise
STEP_BACK_MAX = 3     # step backs a turn


ONE_SHOT_STEPS = 150  # one-shot runs with a time limit: nobody can say "keep going", so a fixed cap
ONE_SHOT_STEPS_FREE = 200  # without a time limit (at least this, or max_steps if that's higher)
CHECK_STEPS = 10      # steps left for the final check once the cap is hit

# one-shot runs on a bare machine (benchmarks): what's there and what isn't, in one line, so the
# model doesn't spend five steps finding out gcc is missing or pip has no network
PROBE = r"""
miss=""
for c in gcc g++ make cmake rustc cargo go java node npm git curl wget pip3 uv; do
  command -v "$c" >/dev/null 2>&1 || miss="$miss $c"
done
py=$(python3 -c 'import sys; print("python %d.%d" % sys.version_info[:2])' 2>/dev/null || echo "no python3")
pkgs=$(python3 - <<'EOF' 2>/dev/null
import importlib.util as u
names = ["numpy", "pandas", "scipy", "torch", "sklearn", "PIL", "cv2", "requests", "pytest"]
print(" ".join(n for n in names if u.find_spec(n) is None))
EOF
)
net=$(python3 -c 'import socket; socket.create_connection(("pypi.org", 443), 3); print("network ok")' 2>/dev/null || echo "no network")
echo "$py; $net; missing commands:${miss:- none}; missing python packages: ${pkgs:-none}"
"""
PROJECT_FILES = ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml", "CMakeLists.txt")

ABS_PATH = re.compile(r"(?<![\w.~/])/(?:[\w.+-]+/)*[\w.+-]+")
SYSTEM_DIRS = {"bin", "boot", "dev", "etc", "home", "lib", "lib64", "opt", "proc", "root", "run", "sbin",
               "srv", "sys", "usr", "var"}  # named on their own, they don't open the whole folder
INTERACTIVE = re.compile(r"\b(ssh|qemu|vm|repl|interactive|tmux|telnet|gdb)\b", re.I)

# a final reply that hands a choice back to someone who isn't there
HEDGE = re.compile(r"say so|tell me if|if you'?d (rather|prefer|like)|if you (want|prefer|intended)|"
                   r"say the word|shall i|want me to|judge?ment call", re.I)

REFINE_MODES = ("auto", "on", "off")


# PURR_STATE moves saved chats and history elsewhere (tests use it to stay away from your real ones)
STATE_DIR = Path(os.environ.get("PURR_STATE") or Path.home() / ".local/state/purr")
LOG_DIR = STATE_DIR / "sessions"
GLOBAL_NOTES = Path.home() / ".config/purr/AGENTS.md"
NOTES_FILES = ["AGENTS.md"]
NOTES_MAX = 20000
RETRY_WAITS = [5, 15, 30]  # seconds between tries when an API server hiccups (a minute in all)
RETRY_WAITS_LOCAL = [2]    # Ollama on this machine: one more try; it's up or it isn't
CUT_MAX = 5  # one-shot runs: cut-off replies nudged on before the turn may end (a chat: 2)
CUT_TAIL_CHARS = 1200  # of a cut-off reply's end, quoted back so it can carry on from there
REPEAT_NUDGE = 2  # same tool call this many times: tell the model to stop repeating


def _notes(path, label):
    if not path.is_file():
        return ""
    notes = path.read_text(errors="replace")
    if len(notes) > NOTES_MAX:
        notes = notes[:NOTES_MAX] + "\n[notes cut short]"
    return f"\n\n{label}:\n{notes}"


MCP_HINTS = [  # one short line each: the prompt goes with every request
    ({"outline", "find_symbol"}, "- {names}: outline a file, then read just the lines you need."),
]
OVERVIEW_CHARS = 2000      # the overview in the system prompt, for big-context models
OVERVIEW_CHARS_SMALL = 1200  # and for 64k-or-less ones (about 300 tokens)


def _mcp_lines(names):
    names, lines = list(names), []
    for group, text in MCP_HINTS:
        have = [n for n in names if n in group]
        if have:
            lines.append(text.format(names=", ".join(have)))
            names = [n for n in names if n not in group]
    names = [n for n in names if n != "project_overview"]  # its answer is already in the prompt
    if names:
        lines.append(f"- also: {', '.join(names)} (see their descriptions)")
    return "\n".join(lines)


def system_prompt(root, model_id, provider, hidden=(), mode="code", mcp_tools=(), overview="", one_shot=False):
    """hidden: tools this model isn't offered, so the prompt doesn't mention them either.
    mode: code (all tools), ask (look only), chat or create (no tools, their own prompt).
    mcp_tools: names of the MCP servers' tools; overview: the project overview to start from."""
    date = datetime.date.today().isoformat()
    if mode in ("chat", "create"):  # no project, no tools: a short prompt leaves room to talk
        return (CHAT if mode == "chat" else CREATE).format(model=model_id, provider=provider, date=date)
    look = ", ".join(t for t in ("read_file", "list_files", "grep", "fetch_url") if t not in hidden)
    text = SYSTEM.format(model=model_id, provider=provider, root=root, look=look,
                         task="" if "task" in hidden else TASK_LINE, date=date)
    if mode == "ask":
        start, end = text.index("Tools\n"), text.index("\n\nHow to work")
        text = text[:start] + ASK_TOOLS.format(look=look) + text[end:]
    elif mode == "learn":
        text = text[:text.index("How to work")] + LEARN
    elif mode == "pair":
        text = text[:text.index("How to work")] + PAIR
    extra = _mcp_lines(mcp_tools)
    if extra:
        cut = text.index("\n\nHow to work")
        text = text[:cut] + "\n" + extra + text[cut:]
    if one_shot:
        text = text.replace(ASK_LINE, ONE_SHOT_LINE).replace(APPROVE_LINE, "")
        text = text.replace(IDENTITY_TAIL.format(model=model_id, provider=provider), "")
        text = text.replace(FOLDER_LINE, "(use the exact absolute path when the task gives one)")
        purr_dir = Path(__file__).resolve().parent.parent
        if Path.home() not in purr_dir.parents:  # installed somewhere like /opt/purr (a container)
            text = text.replace("- System: Linux", f"- System: Linux ({purr_dir} is purr itself, not the task)")
    if overview:
        text += ("\n\nProject overview (purr made it from the files; when they disagree, the files win):\n"
                 + overview)
    text += _notes(GLOBAL_NOTES, "The user's notes for every project")
    for name in NOTES_FILES:
        text += _notes(Path(root) / name, f"Project notes ({name})")
    return text


def plain_history(messages):
    """The chat without tool calls, for chat and create mode: APIs refuse tool messages when
    no tools are offered, so earlier tool use becomes plain text."""
    out, names = [], {}
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = "; ".join(f"{c['function']['name']}({c['function']['arguments'][:200]})" for c in m["tool_calls"])
            names.update({c["id"]: c["function"]["name"] for c in m["tool_calls"]})
            out.append({"role": "assistant", "content": f"{m.get('content') or ''}\n[used tools: {calls}]".strip()})
        elif m.get("role") == "tool":
            out.append({"role": "user", "content": f"(result of {names.get(m.get('tool_call_id'), 'a tool')}: "
                                                   f"{clip(m.get('content') or '', 1500)})"})
        else:
            out.append({k: m[k] for k in ("role", "content") if k in m})
    return out


TEXT_CALL = re.compile(r"<function=([\w-]+)>(.*?)</function>", re.S)
TEXT_PARAM = re.compile(r"<parameter=([\w-]+)>\n?(.*?)\n?</parameter>", re.S)
PARAM_TYPES = {s["function"]["name"]: {k: v.get("type") for k, v in s["function"]["parameters"]["properties"].items()}
               for s in schemas()}
MENTION = re.compile(r"(?<!\S)@(\S+)")


def calls_from_text(text):
    """Qwen models sometimes write tool calls as text instead of real calls:
    <function=read_file><parameter=path>x.py</parameter></function>. Turn those into real ones."""
    calls = []
    for n, (name, body) in enumerate(TEXT_CALL.findall(text)):
        types = PARAM_TYPES.get(name, {})
        args = {}
        for key, value in TEXT_PARAM.findall(body):
            if types.get(key) == "integer":
                value = int(value.strip()) if value.strip().lstrip("-").isdigit() else value
            elif types.get(key) == "boolean":
                value = value.strip().lower() == "true"
            elif types.get(key) == "array":
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            args[key] = value
        calls.append({"id": f"text_call_{n}", "name": name, "args": json.dumps(args)})
    cleaned = re.sub(r"</?tool_call>", "", TEXT_CALL.sub("", text)).strip()
    return calls, cleaned


LOOKS = ("read_file", "grep", "list_files")  # their result changes when a file does
ROUTINE_TOOLS = (*LOOKS, "outline", "find_symbol", "todo")  # steps routine_effort may think less after


def _new_stats():
    """A turn's numbers, every key there from the start (purr bench and the window read them)."""
    return {"start": time.monotonic(), "out": 0, "gen": 0.0, "calls": 0, "model_s": 0.0, "in": 0,
            "cached": 0, "providers": {}}


def _cached(usage):
    """Prompt tokens a provider served from its cache (DeepSeek's and OpenAI's ways of saying it)."""
    hit = (usage or {}).get("prompt_cache_hit_tokens")
    if hit is None:
        hit = ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens")
    return hit or 0


def _ends(text, head, tail):
    """A long text as its start and end (where a request keeps its paths and its limits)."""
    if len(text) <= head + tail:
        return text
    return text[:head] + " … " + text[-tail:]


def _stable(raw):
    """A tool call's arguments, normalised so the same call matches itself."""
    try:
        return json.dumps(json.loads(raw or "{}"), sort_keys=True)
    except ValueError:
        return raw or ""


def _args(raw):
    """A tool call's arguments as a dict ({} when the model wrote broken JSON)."""
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        return {}
    return args if isinstance(args, dict) else {}


def _looks_failed(result):
    """Whether a tool result reads like a failure (so a repeat is worth stopping)."""
    text = (result or "").lstrip()
    if text.startswith("error:"):
        return True
    m = re.search(r"\[exit code (-?\d+)\]", result or "")
    return bool(m and m.group(1) != "0")


TICKET_SPLIT = re.compile(r"^##\s+(.+?)\s*$", re.M)
FILE_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.M)


def parse_tickets(text):
    """The planner's reply -> [(title, body)]. Only "## " starts a ticket; a leading "# Plan"
    or intro before the first one is dropped, and "###" subheadings (Files, Done when, ...) stay
    inside the ticket they belong to. If it wrote no "## " headings, the whole reply is one ticket."""
    text = text or ""
    heads = list(TICKET_SPLIT.finditer(text))
    tickets = []
    for i, m in enumerate(heads):
        title = m.group(1).strip().strip("*#:").strip()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        if title:
            tickets.append((title, text[m.end():end].strip()))
    if not tickets:
        stripped = text.strip()
        if stripped:
            lines = stripped.splitlines()
            title = lines[0].lstrip("#*- ").strip() or "ticket"
            tickets.append((title, "\n".join(lines[1:]).strip() or stripped))
    return tickets


def ticket_body(text):
    """A ticket file without its "# title" line (the title is shown on its own)."""
    return FILE_TITLE.sub("", text or "", count=1).strip()


FAILED_TEST = re.compile(r"^(?:FAILED|ERROR) (\S+)|^(?:FAIL|ERROR): (.+?)\s*$", re.M)


def failed_tests(output):
    """The failing test names in pytest or unittest output (empty when it names none)."""
    return {a or b for a, b in FAILED_TEST.findall(output or "")}


def ticket_title(text, fallback="ticket"):
    """The "# title" line of a ticket file (for the progress line and the record)."""
    m = FILE_TITLE.search(text or "")
    return m.group(1).strip() if m else fallback


def slug(title):
    """A ticket title -> a short, safe file name."""
    name = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return name[:40].rstrip("-") or "ticket"


def one_line(text, limit=600):
    """A request squeezed onto one line, for the executor's small context."""
    return " ".join((text or "").split())[:limit]


def ensure_purr_gitignore(root):
    """.purr/.gitignore with "*", so tickets are never committed in any project."""
    purr = Path(root) / ".purr"
    purr.mkdir(parents=True, exist_ok=True)
    ignore = purr / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")


def archive_tickets(root):
    """Move the current .purr/tickets/*.md to .purr/tickets/old/<timestamp>/ (not deleted).
    Returns that folder, or None when there was no earlier plan."""
    folder = Path(root) / ".purr/tickets"
    old = list(folder.glob("*.md"))
    if not old:
        return None
    dest = folder / "old" / datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for p in old:
        p.replace(dest / p.name)
    return dest


def write_tickets(root, tickets):
    """Write a plan to .purr/tickets/NN-slug.md. An earlier plan is archived (not deleted).
    Returns the paths, in order."""
    folder = Path(root) / ".purr/tickets"
    folder.mkdir(parents=True, exist_ok=True)
    ensure_purr_gitignore(root)
    archive_tickets(root)
    paths = []
    for i, (title, body) in enumerate(tickets, 1):
        path = folder / f"{i:02d}-{slug(title)}.md"
        path.write_text(f"# {title}\n\n{body}\n")
        paths.append(path)
    return paths


def read_tickets(root):
    """The tickets on disk, sorted by file name, read fresh: [(path, text), ...]."""
    folder = Path(root) / ".purr/tickets"
    if not folder.is_dir():
        return []
    return [(p, p.read_text(errors="replace")) for p in sorted(folder.glob("*.md"))]


_OPENCODE_KEYS = {}


KEYS_FILE = Path.home() / ".config/purr/keys.toml"  # `purr --key groq` writes here (only you can read it)


def saved_key(env_name):
    """A key saved with `purr --key`, by its environment variable's name."""
    try:
        import tomllib
        return tomllib.loads(KEYS_FILE.read_text()).get(env_name)
    except (OSError, ValueError):
        return None


def provider_key(provider):
    """The provider's key: its environment variable, then `purr --key`, then `opencode auth login`.
    None if it needs one and there's none."""
    env = provider.get("api_key_env")
    if not env:
        return None
    return os.environ.get(env) or saved_key(env) or (
        opencode_key(provider["opencode_auth"]) if provider.get("opencode_auth") else None)


def opencode_key(integration):
    """A key you saved with `opencode auth login`, so it doesn't have to live in ~/.zshrc too."""
    if integration in _OPENCODE_KEYS:
        return _OPENCODE_KEYS[integration]
    try:
        out = subprocess.run(["opencode", "auth", "export"], capture_output=True, text=True, timeout=20).stdout
        creds = json.loads(out)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    for c in creds:
        if c.get("integrationID") == integration and c.get("active", True):
            _OPENCODE_KEYS[integration] = (c.get("value") or {}).get("key")
            return _OPENCODE_KEYS[integration]
    return None


def is_peak(provider):
    now = datetime.datetime.now(datetime.timezone.utc)
    if now.weekday() >= 5:
        return False
    return any(a <= now.hour < b for a, b in provider.get("peak_hours_utc", []))


def list_sessions(folder=None):
    """Saved chats, newest first: [{"path", "title", "model", "folder", "when", "turns"}]."""
    found = []
    for p in sorted(LOG_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if folder and data.get("folder") != str(folder):
            continue
        users = [m for m in data.get("messages", []) if m.get("role") == "user"]
        if not users:
            continue
        found.append({"path": p, "title": data.get("title") or (users[0].get("content") or "")[:70],
                      "model": data.get("model", "?"), "folder": data.get("folder", ""),
                      "when": datetime.datetime.fromtimestamp(p.stat().st_mtime), "turns": len(users)})
    return found


class SubView:
    """A helper's view: only its tool lines show up (dimmed, under the task line)."""

    def __init__(self, view):
        self.view = view

    def tool(self, line):
        self.view.note(f"↳ {line}")

    def note(self, s, kind="dim"):
        if kind in ("error", "warn"):
            self.view.note(f"↳ {s}", kind)

    def ask(self, question, allow_always=True):
        return "n", ""

    def plan_review(self, tickets, folder):
        return "run"  # a helper never plans, but a view needs this to exist

    def activity(self, what, detail=""):
        if what != "thinking":
            self.view.activity(what, detail)

    def tool_result(self, name, args, result): pass

    def thinking(self, s): pass
    def text(self, s): pass
    def end_reply(self): pass
    def diff(self, path, before, after): pass
    def status(self, s): pass
    def todos(self, items): pass


class Agent:
    def __init__(self, config, root, model_name, view, helper=False, parent=None):
        self.config = config
        self.root = Path(root).resolve()
        self.view = view
        self.helper = helper
        self.parent = parent
        perms = config.get("permissions", {})
        self.tools = Tools(self.root, view, read_only=helper, allow_run=perms.get("allow_run", []))
        # MCP servers ([mcp.*] in config.toml): started on first use, shared with helpers
        self.mcp = parent.mcp if parent else Mcp(config, self.root, note=lambda s: view.note(s, "warn"))
        self.tools.mcp = self.mcp
        self.tools.is_private = lambda: self.private
        self.agents = {}  # your own agents (harness/agents.py), each a mode: reload_agents()
        self._overview = None
        self.tools.spawn = None if helper else self._helper
        self.stop_flag = False  # the TUI sets this to stop an answer (plain mode uses ctrl+c)
        self.tools.code_checks = config.get("code_checks", True)
        self.tools.read_before_edit = config.get("read_before_edit", True)
        self._hide_terminal = None  # one-shot runs on a small context: decided once from the task
        self.mode = "code"       # code, ask, learn, pair, plan, chat or create (MODES)
        self.mode_model = {}     # mode -> the model it had last (switch_mode)
        self.router = None       # /model free: picks free models and moves on when one is maxed out
        self.time_limit = None   # seconds for a turn (purr --time-limit): reminders at half and four fifths
        self.one_shot = False    # nobody answers questions (purr -p, benchmarks): set_one_shot
        self._echo_all = False   # True once a provider refused trimmed thinking (_for_provider)
        self.shell = None        # minimal mode's bash session, opened by its first command
        # /refine: "auto" rewrites a short first message into a clear task (you approve it),
        # "on" every message, "off" none. purr bench measured +2 solved hard tasks from vague asks.
        # Unset, it follows the model (auto for local models, off for API ones); /refine or
        # refine = "..." in config.toml pins it.
        self.refine_pinned = config.get("refine") in REFINE_MODES
        self.refine_mode = config["refine"] if self.refine_pinned else "auto"
        self.set_model(model_name)
        self.reload_agents()
        self.new()

    def stopping(self):
        return self.stop_flag or bool(self.parent and self.parent.stopping())

    @property
    def minimal(self):
        """minimal = true (harness/minimal.py): one-line prompt, one bash tool, no checks."""
        return bool(self.config.get("minimal")) and not self.helper

    # ---- /private: local models only, no web, nothing saved ----

    @property
    def private(self):
        # kept in the config dict, which helpers, the planner and ticket workers share
        return bool(self.config.get("_private"))

    def local_models(self):
        return [n for n, m in self.config["models"].items() if m["provider"] in LOCAL and not m.get("router")]

    def set_private(self, on):
        """Turn private mode on or off. Either way the chat starts fresh: what was said in private
        is gone, and nothing from before carries into it. Returns a line about it."""
        if on:
            local = self.local_models()
            if not local:
                raise KeyError("private mode needs a local model (an Ollama one in config.toml)")
            self.config["_private"] = True
            want = self.config.get("private_model")
            pick = (want if want in local else self.model_name if self.model_name in local
                    else self.config.get("default_model") if self.config.get("default_model") in local
                    else (self.config.get("plan") or {}).get("executor") if (self.config.get("plan") or {}).get("executor") in local
                    else local[0])
            self.router = None
            self.mode_model = {}
            self.set_model(pick)
        else:
            self.config.pop("_private", None)
        self.mcp.offline(on)  # the Godot docs lookup stops fetching from GitHub
        self.new()
        return pick if on else None

    @property
    def hidden_tools(self):
        """Tools this model isn't offered: the model's own limits, plus the web in private mode."""
        # terminal = true/false on a model decides; else one-shot runs on a small context decide
        terminal = self.model.get("terminal")
        hide_terminal = terminal is False or (terminal is None and bool(getattr(self, "_hide_terminal", None)))
        agent = self.agents.get(self.mode)
        not_its = tuple(sorted(TOOL_NAMES - agent["tools"])) if agent and isinstance(agent["tools"], set) else ()
        return (tuple(self.limits.hidden_tools) + (("fetch_url",) if self.private else ())
                + (() if self.model.get("vision") else ("look_at_image",))  # only models that can see
                + (("terminal",) if hide_terminal else ()) + not_its)  # one of your agents: only its tools

    @property
    def chosen_model(self):
        """What you picked: "free" while the free router picks for you, else the model itself."""
        return self.router_name if self.router else self.model_name

    def set_model(self, name):
        models = self.config["models"]
        if name not in models:
            raise KeyError(f"no model called {name!r}. Have: {', '.join(models)}")
        if self.private and (models[name].get("router") or models[name]["provider"] not in LOCAL):
            raise KeyError(f"private mode: {name} runs at {models[name]['provider']}, so your chat would leave "
                           "this computer. Only local models now (/private again to leave)")
        if models[name].get("router"):  # [models.free]: let the free router choose
            router = self.router or FreeRouter(self.config)
            pick = router.pick(self.mode, need=self.context_used() if getattr(self, "messages", None) else 0)
            if not pick:
                raise KeyError(f"every free model is resting until about {router.next_free_at(self.mode)} "
                               "(/free shows them)")
            self.router, self.router_name = router, name
            return self._use_model(pick)
        self.router = None
        return self._use_model(name)

    def _use_model(self, name):
        model = self.config["models"][name]
        provider = self.config["providers"][model["provider"]]
        key = provider_key(provider)
        if provider.get("api_key_env") and not key:
            raise KeyError(f"{name} needs a key: `purr --key {model['provider']}`, or the "
                           f"{provider['api_key_env']} environment variable"
                           + (f", or `opencode auth login {provider['opencode_auth']}`"
                              if provider.get("opencode_auth") else ""))
        self.model_name, self.model, self.provider, self.key = name, model, provider, key
        self.limits = Limits.for_model(model)
        if not getattr(self, "refine_pinned", True):
            self.refine_mode = "auto" if self.limits.helpers == "full" else "off"
        if getattr(self, "tools", None):
            self.tools.limits = self.limits  # 32k and 1M models need different caps
        self._refresh_system()  # the system prompt names the model, so it changes with it

    # ---- your own agents (harness/agents.py): modes of their own ----

    def reload_agents(self):
        """Read the agent files again (yours, the project's, other tools')."""
        if self.helper:
            return
        self.agents = agent_files.load(self.root, TOOL_NAMES, self.config.get("models", {}))

    def modes(self):
        """{mode: what it does}: purr's own, then your agents."""
        return {**{m: what for m, (_, what) in MODES.items()},
                **{n: a["description"] for n, a in self.agents.items()}}

    def cycle_modes(self):
        """The modes shift+tab goes through: purr's own and your agents (other tools' agents are a
        /agent <name> away, so a big collection doesn't flood the cycle)."""
        return list(MODES) + [n for n, a in self.agents.items() if a["origin"] in ("purr", "project")]

    def mode_level(self, mode=None):
        """"all", "read" or "none": what a mode may do with tools."""
        mode = mode or self.mode
        agent = self.agents.get(mode)
        if agent:
            return "none" if agent["tools"] == "none" else "read" if agent_files.looks_only(agent) else "all"
        return MODES[mode][0]

    def looks_only(self):
        """Nothing may be changed: a helper, ask mode, or one of your look-only agents."""
        return self.helper or self.mode == "ask" or self.mode_level() == "read"

    @property
    def coding(self):
        """Doing the work, so the checks that come with it apply: code mode, or an agent of yours
        that may change things."""
        return self.mode == "code" or (self.mode in self.agents and self.mode_level() == "all")

    def set_mode(self, mode):
        """code: every tool. ask: look but never change. chat / create: no tools at all. One of your
        agents: the tools its file allows."""
        if mode not in MODES and mode not in self.agents:
            raise KeyError(f"no mode or agent called {mode!r}. Have: {', '.join(self.modes())}")
        self.mode = mode
        self.tools.read_only = self.looks_only()  # enforced, not just asked for
        self.tools.no_tools = self.mode_level() == "none"
        if mode == "pair" and not self.helper:
            self.pair_snapshot()  # from now on, file changes between turns are the user's
        self._refresh_system()

    def switch_mode(self, mode):
        """set_mode for your own switches (shift+tab, /mode, /plan): also moves to the model that
        mode is best on (mode_models in config.toml). A model you picked by hand in a mode wins
        for the rest of the session. Returns a line about the model change, or None."""
        if mode not in MODES and mode not in self.agents:
            raise KeyError(f"no mode or agent called {mode!r}. Have: {', '.join(self.modes())}")
        self.mode_model[self.mode] = self.chosen_model  # going back to this mode brings it back
        self.set_mode(mode)
        want = (self.mode_model.get(mode) or self.config.get("mode_models", {}).get(mode)
                or (self.agents.get(mode) or {}).get("model"))
        if self.router and mode not in self.mode_model:
            want = self.router_name  # the free router stays on: it picks the best free model for this mode
        if self.private and want and want not in self.local_models():
            want = None  # private: a mode's API model stays out; keep the local one
        if not want or (want == self.chosen_model and not self.router):
            return None
        before = self.model_name
        try:
            self.set_model(want)
        except KeyError as e:  # no key, or a name that isn't in [models]: keep the current one
            return f"kept {self.model_name} ({e.args[0]})"
        if self.model_name == before:
            return None
        return f"model: {self.router_name} → {self.model_name}" if self.router else f"model: {want}"

    def should_refine(self, text):
        """Whether this message should go through refine first (see refine_mode)."""
        if self.mode != "code" or self.refine_mode == "off" or text.startswith(("/", "!")):
            return False
        if self.refine_mode == "on":
            return True
        words = len(text.split())
        first = not any(m.get("role") == "user" for m in self.messages)
        # auto: a short first message is a new task said briefly; follow-ups need the chat to make
        # sense (refine doesn't see it) and one-word replies ("yes", "continue") aren't tasks
        return first and 3 <= words <= 15

    def refine(self, text):
        """Your request, rewritten by the same model into a clear task (Task / Where / Steps /
        Done when) using the project's file list. The chat itself doesn't change."""
        self.view.activity("refining", "making your message clearer")
        files = self.tools.t_list_files(".").splitlines()
        listing = "\n".join(files[:80]) + (f"\n… {len(files) - 80} more files" if len(files) > 80 else "")
        notes = _notes(Path(self.root) / "AGENTS.md", "Project notes (AGENTS.md)")[:3000]
        prompt = REFINE.format(files=listing, notes=notes + "\n" if notes else "", request=text)
        reply = self._call([{"role": "user", "content": prompt}], tools=False, quiet=True)
        self._count(reply["usage"])
        return reply["text"].strip()

    # ---- plan mode: a big model writes tickets, a small one does them one at a time (MODES) ----

    def plan_turn(self, text):
        """Split the request into tickets (a big model, .purr/tickets/), let the user approve or
        edit them, then hand each ticket to the executor (a small local model) one at a time. Each
        ticket runs in a fresh agent, so the small model's context holds only the ticket in front
        of it, not the whole task."""
        self.turn_stats = _new_stats()
        self.title = self.title or " ".join(text.split())[:70]
        plan = self.config.get("plan", {})
        planner_name = plan.get("planner") or self.model_name
        executor_name = plan.get("executor") or self.model_name
        self.view.note(f"planning with {planner_name}, then {executor_name} takes the tickets", "info")
        self.messages.append({"role": "user", "content": text})
        tickets = self._make_tickets(text, planner_name)
        if self.stopping():
            self.view.note("stopped while planning; no tickets written", "warn")
            self._plan_done("stopped while planning", "■")
            return
        if not tickets:
            self.view.note("the planner came back with no tickets, so there is nothing to do", "error")
            self.messages.append({"role": "assistant", "content": "(purr: the planner wrote no tickets)"})
            self._plan_done("0 done, 0 failed/not run", "✗")
            return
        write_tickets(self.root, tickets)
        (Path(self.root) / ".purr/tickets/request.txt").write_text(text + "\n")
        listing = "\n\n".join(f"## {title}\n{body}" for title, body in tickets)
        self.messages.append({"role": "assistant",
                              "content": f"Plan ({len(tickets)} tickets):\n\n{listing}"})
        self.view.note(f"wrote {len(tickets)} tickets to .purr/tickets/", "info")
        if not self._review_plan():
            self.view.note("the tickets are ready; /plan run starts them when you are", "info")
            self._plan_done(f"0 done, {len(tickets)} not run", "■")
            return
        self.run_tickets(1, text, executor_name)

    def _make_tickets(self, text, planner_name):
        """Ask the planner (a big model, read-only so it can look around) for the ticket list."""
        try:
            planner = Agent(self.config, self.root, planner_name, self.view, parent=self)
        except KeyError as e:  # no key / no such model: plan with what we have rather than fail
            self.view.note(f"can't use {planner_name} for planning ({e.args[0]}), "
                           f"using {self.model_name}", "warn")
            planner = Agent(self.config, self.root, self.model_name, self.view, parent=self)
        planner.set_mode("ask")
        planner.title = f"planning: {' '.join(text.split())[:60]}"
        planner.turn(PLAN.format(request=text))
        answers = [m.get("content") for m in planner.messages
                   if m.get("role") == "assistant" and m.get("content")]
        return parse_tickets(answers[-1] if answers else "")

    def _review_plan(self):
        """Show the tickets on disk and wait: run, cancel, or let the user edit them first.
        Returns True to run, False to stop (the files stay either way)."""
        while True:
            tickets = [{"path": str(p), "title": ticket_title(text, p.stem), "body": ticket_body(text)}
                       for p, text in read_tickets(self.root)]
            if not tickets:
                self.view.note("no tickets to run", "error")
                return False
            choice = self.view.plan_review(tickets, str(Path(self.root) / ".purr/tickets"))
            if choice == "run":
                return True
            if choice == "cancel":
                self.view.note("kept the tickets; nothing was run", "info")
                return False
            # "edit": the user changes the files, then we read them again

    def run_plan(self, start=1):
        """`/plan run [N]`: run the tickets already in .purr/tickets/ from ticket N, without
        planning again. The files are read fresh, so any edits the user made count."""
        self.stop_flag = False  # an Esc on an earlier answer mustn't stop this before it starts
        self.turn_stats = _new_stats()
        self.title = self.title or "plan"
        self.messages.append({"role": "user", "content": f"(purr: run the plan tickets from {start})"})
        self.run_tickets(start, self._saved_request())

    def run_tickets(self, start=1, request="", executor_name=None):
        """Run the tickets in .purr/tickets/ from `start` (1-based), each in a fresh agent. Reads
        every ticket file again right before it runs, so the user's edits count. The project's
        tests are run after each ticket; the first failure stops the plan and is reported. Keeps a
        record in the parent chat and saves it."""
        executor_name = executor_name or self.config.get("plan", {}).get("executor") or self.model_name
        if not getattr(self, "turn_stats", None):
            self.turn_stats = _new_stats()
        tickets = read_tickets(self.root)
        total = len(tickets)
        if not tickets:
            self.view.note("no tickets in .purr/tickets/; use /plan <task> to write some", "error")
            return
        if not 1 <= start <= total:
            self.view.note(f"there is no ticket {start} (there are {total})", "error")
            return
        request = one_line(request or self._saved_request())
        titles = [ticket_title(text, p.stem) for p, text in tickets]
        attempted = total - (start - 1)
        self.tools.begin_turn()  # one undo frame for the whole plan
        cmd = checks.test_command(self.root)
        if cmd and "pytest" in cmd:
            cmd = re.sub(r"\s-x\b", "", cmd)  # run every test, so old failures don't hide new ones
        baseline = self._test_baseline(cmd)
        plan_stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
        done, end = 0, None  # end: why the plan stopped early ("stopped", "failed", "error")
        for idx in range(start - 1, total):
            if self.stopping():
                self.view.note("stopped before the next ticket", "warn")
                end = "stopped"
                break
            path = tickets[idx][0]
            text = path.read_text(errors="replace")  # re-read: the user may have edited it
            title = ticket_title(text, path.stem)
            self.view.note(f"♡ ticket {idx + 1}/{total}: {title}", "info")
            try:
                worker = Agent(self.config, self.root, executor_name, self.view, parent=self)
            except KeyError as e:
                self.view.note(f"can't hand out the tickets: {e.args[0]}", "error")
                end = "error"
                break
            worker.tools.trust_all = self.tools.trust_all
            worker.tools.always = self.tools.always            # shared: "always" carries between tickets
            worker.tools.always_run = self.tools.always_run
            worker.title = f"ticket {idx + 1}/{total}: {title}"
            worker.log_path = LOG_DIR / f"plan_{plan_stamp}_ticket-{idx + 1:02d}.json"
            worker.turn(TICKET_WORK.format(i=idx + 1, total=total, request=request,
                                           titles=self._ticket_lines(titles, idx), ticket=text))
            self._merge_undo(worker)
            if self.stopping():  # stopped half way: not checked, not done
                ok, line, end = False, f"■ ticket {idx + 1}/{total}: {title} (stopped, not checked)", "stopped"
            else:
                ok, line, baseline = self._ticket_result(idx + 1, total, title, cmd, baseline)
                end = None if ok else "failed"
            self.messages.append({"role": "assistant", "content": line})
            self.save_log()
            if not ok:
                break
            done += 1
        left = attempted - done
        if end == "stopped":
            self._plan_done(f"{done} done, {left} stopped/not run", "■")
        elif end == "failed":
            self._plan_done(f"{done} done, 1 failed, {left - 1} not run", "✗")
        elif end == "error":
            self._plan_done(f"{done} done, {left} not run", "✗")
        else:
            self._plan_done(f"{done} done", "✓")

    def _ticket_lines(self, titles, idx):
        """The ticket list for the executor: ✓ for the ones already done, the current one next."""
        return "\n".join(f"{'✓' if n < idx else '○'} {n + 1}. {title}"
                         for n, title in enumerate(titles))

    def _merge_undo(self, worker):
        """Put a ticket's file changes on the plan's undo frame, so /undo after a plan puts back
        everything the plan changed (all tickets as one undo)."""
        if not worker.tools.undo_stack:
            return
        changes = worker.tools.undo_stack.pop()
        for p, before in changes.items():
            self.tools.undo_stack[-1].setdefault(p, before)  # keep the earliest text for /undo

    def _test_baseline(self, cmd):
        """Run the tests once before the first ticket, so a ticket is only blamed for failures it
        added, not for ones the project already had. None when there's nothing to run."""
        if not cmd:
            return None
        output, code = self._run_tests(cmd, ask=False)
        baseline = {"code": code, "failed": failed_tests(output)}
        if code != 0:
            known = f" ({len(baseline['failed'])} failing)" if baseline["failed"] else ""
            self.view.note(f"the tests already fail before the plan{known}; tickets are only "
                           "blamed for new failures", "warn")
        return baseline

    def _ticket_result(self, i, total, title, cmd, baseline):
        """Run the project's tests after a ticket and compare them with the baseline (the run
        before this ticket). Returns (ok, one-line record for the chat, the new baseline)."""
        name = f"ticket {i}/{total}: {title}"
        if not cmd:
            return True, f"✓ {name} (not checked: no tests found)", None
        output, code = self._run_tests(cmd, ask=False)
        now = {"code": code, "failed": failed_tests(output)}
        if code == 0:
            return True, f"✓ {name} (tests pass)", now
        was_failing = baseline and baseline["code"] not in (0, -1)
        if was_failing and code != -1:
            if not (now["failed"] and baseline["failed"]):
                # can't tell which tests fail, so can't tell if this ticket broke one
                return True, f"✓ {name} (not checked: the tests already failed before the plan)", now
            new = sorted(now["failed"] - baseline["failed"])
            if not new:
                return True, f"✓ {name} (no new test failures; {len(now['failed'])} failed before)", now
            why = "broke " + ", ".join(new[:5]) + (f" and {len(new) - 5} more" if len(new) > 5 else "")
        else:
            why = "tests time out" if code == -1 else "tests fail"
        tail = "\n".join(output.strip().splitlines()[-15:])
        self.view.note(f"{name} left the tests failing ({cmd}):", "error")
        for line in tail.splitlines():
            self.view.note(line[:200], "error")
        self.messages.append({"role": "assistant", "content": f"(purr ran `{cmd}`:\n{tail}\n)"})
        if why == "tests fail":
            why += ": " + (tail.splitlines()[-1] if tail else "no output")
        return False, f"✗ {name} ({why})", baseline

    def _saved_request(self):
        path = Path(self.root) / ".purr/tickets/request.txt"
        return path.read_text(errors="replace") if path.is_file() else ""

    def _plan_done(self, summary, mark="✓"):
        """mark: ✓ every ticket tried is done, ✗ one failed, ■ stopped or cancelled."""
        line = f"{mark} plan · {summary}"
        self.turn_stats["summary"] = line
        self.view.note(line, "stats")
        self.save_log()

    def project_overview(self):
        """The codebase server's project overview, made once per session ("" without one)."""
        if self._overview is None:
            on = self.config.get("overview_in_prompt", True) and not self.helper
            self._overview = self.mcp.run_once("project_overview") if on else ""
        cap = OVERVIEW_CHARS_SMALL if self.model.get("context", 0) <= 65_536 else OVERVIEW_CHARS
        if len(self._overview) <= cap:
            return self._overview
        cut = self._overview.rfind("\n", 0, cap)
        return self._overview[:cut if cut > 0 else cap] + "\n…"  # whole lines where it can

    @property
    def mcp_taken(self):
        """Names an MCP tool may not use: purr's own tools, and project_overview once its answer is
        in the system prompt (no need to send it twice)."""
        return TOOL_NAMES | ({"project_overview"} if self.project_overview() else set())

    def _system(self):
        if self.minimal:
            return minimal.SYSTEM
        level = self.mode_level()
        tools = level != "none"
        names = [s["function"]["name"] for s in self.mcp.schemas(read_only=self.looks_only(),
                                                                 taken=self.mcp_taken)] if tools else []
        agent = self.agents.get(self.mode)
        # one of your agents: purr's prompt for what it may do (code, ask or chat), then its own role
        base = {"all": "code", "read": "ask", "none": "chat"}[level] if agent else self.mode
        text = system_prompt(self.root, self.model["id"], self.model["provider"], self.hidden_tools,
                             base, mcp_tools=names, overview=self.project_overview() if tools else "",
                             one_shot=self.one_shot)
        if agent:
            text += agent_files.role(agent)
        return text + HELPER if self.helper else text

    def new(self):
        self.messages = [{"role": "system", "content": self._system()}]
        self.session_cost = 0.0
        self.session_out = 0
        self.session_in = 0      # prompt tokens sent, and how many of them were cached
        self.session_cached = 0
        self.last_usage = None
        self.title = ""
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
        self.log_path = None if self.helper or self.private else LOG_DIR / f"{stamp}.json"  # private: never saved

    # ---- sessions ----

    def save_log(self):
        """The whole chat as JSON: for /resume, and to see exactly what the model sent and got back."""
        if not self.log_path or len(self.messages) < 2:
            return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(json.dumps(
            {"model": self.chosen_model, "mode": self.mode, "folder": str(self.root), "title": self.title,
             "cost": self.session_cost, "out": self.session_out, "in": self.session_in,
             "cached": self.session_cached, "messages": self.messages,
             **({"cut_off_reply": self.cut_off} if getattr(self, "cut_off", None) else {})},
            indent=1, ensure_ascii=False))

    def load(self, path):
        """Carry on a saved chat."""
        data = json.loads(Path(path).read_text())
        if data.get("model") in self.config["models"]:
            try:
                self.set_model(data["model"])
            except KeyError:
                pass  # e.g. no API key right now: keep the current model
        self.messages = data["messages"]
        if data.get("mode") in MODES or data.get("mode") in self.agents:
            self.set_mode(data["mode"])
        self._refresh_system()
        self.title = data.get("title", "")
        self.session_cost = data.get("cost", 0.0)
        self.session_out = data.get("out", 0)
        self.session_in, self.session_cached = data.get("in", 0), data.get("cached", 0)
        self.last_usage = None
        self.log_path = Path(path)

    # ---- one model call ----

    def _body(self, messages, tools):
        msgs = messages or self.messages
        if tools and self.mode_level() == "none":
            tools, msgs = False, plain_history(msgs)
        msgs = self._for_provider(msgs)
        body = {
            "model": self.model["id"],
            "messages": msgs,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools and self.minimal:
            body["tools"] = [minimal.BASH]
        elif tools:
            read_only = self.looks_only()
            body["tools"] = schemas(read_only=read_only, read_lines=self.limits.read_lines,
                                    hidden=self.hidden_tools) + self.mcp.schemas(read_only, taken=self.mcp_taken)
        body.update(self.provider.get("body", {}))  # e.g. which OpenRouter hosts may answer
        body.update(self.model.get("body", {}))
        if self.mode == "create" and messages is None:  # a little more surprising
            body["temperature"] = min(1.2, (body.get("temperature") or 0.8) + 0.3)
        agent = self.agents.get(self.mode)
        if agent and agent["temperature"] is not None and messages is None:
            body["temperature"] = agent["temperature"]
        # DSH's default effort is high: a DeepSeek API setting, not sent to Ollama (its models take other values)
        effort = self.config.get("effort") or ("high" if self.minimal and self.model.get("provider") != "ollama" else None)
        routine = self.config.get("routine_effort")
        if routine and tools and messages is None and self._routine_step():
            # output (mostly thinking) is about half of an agent's cost; after only reading and
            # searching, the next step rarely needs deep thought (off unless routine_effort is set)
            effort = routine
        if effort:
            if self.model.get("provider") == "openrouter" or "openrouter.ai" in self.provider.get("base_url", ""):
                body["reasoning"] = {**(body.get("reasoning") or {}), "effort": effort}
            else:
                body["reasoning_effort"] = effort
        return body

    def _routine_step(self):
        """The last step only looked around (read, grep, list, outline): every tool call in the last
        reply was one of those, and nothing came after their results (no check, no note)."""
        i = len(self.messages) - 1
        while i > 0 and self.messages[i].get("role") == "tool":
            i -= 1
        if i == len(self.messages) - 1 or self.messages[i].get("role") != "assistant":
            return False
        calls = [c["function"]["name"] for c in self.messages[i].get("tool_calls") or []]
        return bool(calls) and all(name in ROUTINE_TOOLS for name in calls)

    STANDARD = {"role", "content", "tool_calls", "tool_call_id", "name"}

    def _with_images(self, msgs):
        """Messages carrying images (look_at_image) as text + image parts, for a model that can see;
        older images (beyond the latest few) and models that can't see get a line of text instead."""
        if not any(m.get("images") for m in msgs):
            return msgs
        withs = [i for i, m in enumerate(msgs) if m.get("images")]
        recent = set(withs[-IMAGES_KEEP:])
        out = []
        for i, m in enumerate(msgs):
            if not m.get("images"):
                out.append(m)
                continue
            m = {k: v for k, v in m.items() if k != "images"}
            if self.model.get("vision") and i in recent:
                m["content"] = [{"type": "text", "text": m["content"]}] + [
                    {"type": "image_url", "image_url": {"url": url}} for url in msgs[i]["images"]]
            else:
                m["content"] += " (not shown again here)"
            out.append(m)
        return out

    def set_one_shot(self, on=True):
        """One-shot runs: the prompt says nobody will answer, so settle unclear points yourself."""
        self.one_shot = on
        self._refresh_system()

    def _refresh_system(self):
        """Rebuild the system prompt (the model, the mode or one-shot changed). Before the chat
        exists (set_model runs before new()), there's nothing to rebuild."""
        if getattr(self, "messages", None):
            self.messages[0] = {"role": "system", "content": self._system()}

    def _time_note(self):
        """With a time limit (purr --time-limit, benchmarks): say how much is left at half time and
        at four fifths, once each. A small model will happily explore until the clock runs out."""
        if not self.time_limit or self.helper:
            return
        gone = time.monotonic() - self._started
        for share, text in TIME_NOTES:
            if gone >= share * self.time_limit and share not in self._time_said:
                self._time_said.add(share)
                left = max(1, round((self.time_limit - gone) / 60))
                text = text.format(left=left)
                if share == TIME_NOTES[-1][0] and self.one_shot and not self._checked:
                    text = text[:-2] + "." + ONE_SHOT_SHORT_CHECK
                self.messages.append({"role": "user", "content": text})
                self.view.note(f"⏱ about {left} min left", "warn")

    def _for_provider(self, msgs):
        """The chat with only the fields this provider understands: after the free router switches
        mid-chat, the history holds another provider's thinking (reasoning / reasoning_content),
        and strict APIs refuse fields they don't know."""
        msgs = self._with_images(msgs)
        echo = self.provider.get("echo_reasoning")
        keep = self.STANDARD | ({echo} if echo else set())
        # the thinking of older replies goes too: only the last few keep theirs. A long turn
        # otherwise sends every step's thinking back (170k characters on one Terminal-Bench task,
        # against 4k of replies), filling the context ~40x faster. A provider that refuses this
        # (see _call) gets all of it again.
        recent = None  # None: every reply keeps its thinking
        # keep_reasoning = "all" (and minimal mode, like DSH): every step's thinking goes back, so
        # the model keeps its own earlier plans; with a 1M context and a cache that's cheap
        if echo and not self._echo_all and not (self.minimal or self.config.get("keep_reasoning") == "all"):
            withs = [i for i, m in enumerate(msgs) if m.get(echo)]
            if len(withs) > REASONING_KEEP:
                recent = set(withs[-REASONING_KEEP:])
        if recent is None and all(k in keep for m in msgs for k in m):
            return msgs
        return [{k: v for k, v in m.items() if k in keep and (recent is None or k != echo or i in recent)}
                for i, m in enumerate(msgs)]

    def _next_free(self, err):
        """The free router's model just got maxed out: rest it and move to the next one.
        True when there is one to try (the call goes again), False to give up."""
        router = self.router or (self.parent.router if self.parent else None)
        rest = rest_for(err) if router else None
        if not rest and router and getattr(err, "status", None) in (400, 422) and self._refused < 2:
            # providers take slightly different settings: one refusing purr's request doesn't mean
            # the next will (but a request every model refuses shouldn't empty the whole list)
            self._refused += 1
            rest = (3600, "refused purr's request", "model")
        if not rest:
            return False
        router.rest(self.model_name, *rest)
        pick = router.pick(self.mode, need=self.context_used())
        if not pick:
            self.view.note(f"{self.model_name} is {rest[1]}, and every other free model is resting too "
                           f"(back about {router.next_free_at(self.mode)}; /free shows them)", "warn")
            return False
        self.view.note(f"{self.model_name} is {rest[1]}: switching to {pick}", "warn")
        self._use_model(pick)
        return True

    def _call(self, messages=None, tools=True, quiet=False):
        on_text = (lambda s: None) if quiet else self.view.text
        on_think = (lambda s: None) if quiet else self.view.thinking
        attempt = 0
        self._refused = 0  # 400s that made the free router switch, this call
        while True:
            body = self._body(messages, tools)  # again after a switch: another model, id and hosts
            try:
                return stream_chat(self.provider["base_url"], self.key, body, on_text, on_think, self.stopping)
            except ApiError as e:
                if not self._echo_all and getattr(e, "status", None) in (400, 422) and "reason" in str(e).lower():
                    self._echo_all = True  # it wants every reply's thinking back after all
                    self.tools.repairs.append("provider wanted all the thinking back -> sent it all")
                    continue
                if self._next_free(e):
                    continue
                waits = RETRY_WAITS_LOCAL if self.model.get("provider") in LOCAL else RETRY_WAITS
                if not e.retry or attempt == len(waits):
                    raise
                wait = waits[attempt]
                attempt += 1
                self.view.note(f"the model server had a problem, trying again in {wait}s", "warn")
                for _ in range(wait * 10):
                    if self.stopping():
                        raise Stopped from None
                    time.sleep(0.1)
            finally:
                if not quiet:
                    self.view.end_reply()

    def _price(self, usage):
        price = self.model.get("price")
        if not price or not usage:
            return 0.0
        if isinstance(usage.get("cost"), (int, float)):
            return usage["cost"]  # OpenRouter says what the call really cost
        prompt = usage.get("prompt_tokens", 0)
        hit = _cached(usage)
        miss = usage.get("prompt_cache_miss_tokens", prompt - hit)
        out = usage.get("completion_tokens", 0)
        times = 2 if is_peak(self.provider) else 1
        return times * (hit * price["hit"] + miss * price["miss"] + out * price["out"]) / 1e6

    def _count(self, usage):
        """Add one call's usage to the totals. Returns its price."""
        if not usage:
            return 0.0
        self.last_usage = usage
        cost = self._price(usage)
        self.session_cost += cost
        self.session_out += usage.get("completion_tokens", 0)
        self.session_in += usage.get("prompt_tokens", 0)
        self.session_cached += _cached(usage)
        if self.parent:
            self.parent.session_cost += cost
        return cost

    def context_used(self):
        used = 0
        if self.last_usage:
            used = self.last_usage.get("prompt_tokens", 0)
            if getattr(self, "provider", None) and self.provider.get("echo_reasoning"):
                # its thinking goes back with the reply; for the others the reply's thinking is
                # dropped, and its text is in the guess below (counting every thinking token as
                # context made a 64k model prune and compact far too early)
                used += self.last_usage.get("completion_tokens", 0)
        # Ollama only counts the tokens it didn't have cached, so also guess (~4 characters a token)
        sent = self._for_provider(self.messages) if getattr(self, "provider", None) else self.messages
        text = json.dumps(sent)
        images = text.count('"image_url"')
        text = re.sub(r'data:image/[^"]+', "", text)  # an image costs ~1k tokens, not its base64
        guess = len(text) // 4 + 1500 + 1000 * images  # what's sent: old thinking is trimmed off
        return max(used, guess)

    # ---- one turn: your message -> as many model calls + tools as it takes ----

    def turn(self, text):
        self.stop_flag = False
        self.cut_off = None
        self.failed = None
        if self.mode == "plan" and not self.helper:
            return self.plan_turn(text)
        if self.minimal:
            return self.minimal_turn(text)
        if not self.title:
            self.title = " ".join(text.split())[:70]
        self._begin_turn(text)
        self.messages.append({"role": "user", "content": self._turn_content(text)})
        turn_cost = 0.0
        steps = retries = 0
        todo_only = 0  # steps in a row that did nothing but update the task list
        empty = 0      # empty answers nudged this turn
        cut = 0        # replies cut off at the output limit, nudged this turn
        cap = self._step_cap()
        try:
            while True:
                steps += 1
                if steps > cap:
                    if self.one_shot:  # nobody to ask: check the work once, then stop
                        if not self._final_check():
                            self.view.note(f"stopped after {steps - 1} steps (the step limit)", "warn")
                            break
                        cap = steps + CHECK_STEPS - 1  # this step and a few more for the check
                    else:
                        ans, _ = self.view.ask(f"{steps - 1} steps so far. keep going?", allow_always=False)
                        if ans != "y":
                            break
                        steps = 1
                self._make_room()
                try:
                    if (self._helper_on("reminders") and self.coding and not self.helper
                            and steps > 1 and (steps - 1) % REMIND_EVERY == 0):
                        # small models lose the goal on long tasks: say it again now and then, and
                        # have them look at whether the approach is getting anywhere
                        self.messages.append({"role": "user", "content": CHECKPOINT.format(
                            steps=steps - 1, request=_ends(text, 500, 300))})
                    self._time_note()
                    self.view.activity("thinking")
                    reply = self._call()
                except ApiError as e:
                    self.view.note(f"api error: {e}", "error")
                    self.failed = str(e)  # the turn ended on the model server, not on the task
                    break
                turn_cost += self._record(reply)
                self.save_log()  # after every step: a run that's killed (a time limit, a crash) keeps its chat and cost

                if not reply["tool_calls"] and "<function=" in reply["text"]:
                    reply["tool_calls"], reply["text"] = calls_from_text(reply["text"])
                    self.tools.repairs += ["tool call written as text -> real call"] * len(reply["tool_calls"])

                if self._pair_handing_back and reply["tool_calls"]:
                    # the step is done: whatever it wanted next becomes a suggestion, not an action
                    held = "; ".join(self.tools.summary(c["name"], _args(c["args"])) for c in reply["tool_calls"])
                    reply["text"] = (reply["text"] or "").strip() or f"Next I'd do: {held}"
                    reply["tool_calls"] = []
                    self.view.note(f"⇄ held back for your go: {held}")

                self.messages.append(self._assistant_message(reply))

                if reply["finish"] == "length":
                    self.view.note("(the reply hit the output limit and was cut off)", "error")
                    if not reply["tool_calls"] and cut < (CUT_MAX if self.one_shot else 2):
                        # a cut-off reply isn't "done": it ran out of room mid-thought (seen writing
                        # a whole file into its answer on Terminal-Bench). Have it carry on, in files,
                        # from where it was (its thinking isn't kept), and after twice: act first
                        cut += 1
                        self.tools.repairs.append("reply cut off at the output limit -> told to carry on in files")
                        said = (reply.get("reasoning") or "") + (reply["text"] or "")
                        tail = " ".join(said[-CUT_TAIL_CHARS:].split())
                        nudge = CUT_NUDGE if cut <= 2 else CUT_ACT.format(n=cut)
                        self.messages.append({"role": "user", "content": nudge + (CUT_TAIL.format(tail=tail) if tail else "")})
                        continue
                if not reply["tool_calls"]:
                    # a completely empty answer isn't "done": the model stalled (seen right after
                    # it ran checks that showed bugs). Nudge it on, at most twice a turn.
                    if not (reply["text"] or "").strip() and reply["finish"] != "length" and empty < 2:
                        empty += 1
                        self.tools.repairs.append("empty reply -> nudged to continue")
                        self.view.note("the model went quiet, nudging it on")
                        self.messages.append({"role": "user", "content": EMPTY_NUDGE})
                        continue
                    # local models sometimes write a tool call as plain text, so it never runs
                    if "tool_call>" in (reply["text"] or "") and retries < 2:
                        retries += 1
                        self.view.note("tool call came out as text, asking it to try again")
                        self.messages.append({"role": "user", "content":
                            "(purr: your tool call came out as plain text, so it did not run. "
                            "Call the tool again.)"})
                        continue
                    if self._one_more_look(reply["text"]):
                        continue
                    break
                stopped = self._run_tools(reply["tool_calls"])
                if self.tools.pending_images:  # look_at_image: show the model what it asked to see
                    shown = self.tools.pending_images
                    self.tools.pending_images = []
                    self.messages.append({"role": "user", "images": [url for _, url in shown], "content":
                        "(purr: the image" + ("s" if len(shown) > 1 else "") + " you asked to look at: "
                        + ", ".join(path for path, _ in shown) + ")"})
                if stopped:
                    self.view.note("waiting for you", "info")
                    break
                if self.mode == "pair" and not self.helper and any(
                        name in ("edit_file", "write_file") and not _looks_failed(result)
                        for name, _, result in self._executed):
                    self._pair_handing_back = True
                    self.messages.append({"role": "user", "content": PAIR_HAND_BACK})
                    continue
                nudge = self._check_repeats()
                if nudge and nudge.startswith("stop"):
                    what = "failing step" if nudge == "stop failed" else "step and getting the same result"
                    self.view.note(f"it kept repeating the same {what}, so purr stopped the turn", "error")
                    break
                if nudge:
                    self.messages.append({"role": "user", "content": nudge})
                else:
                    back = self._check_churn()
                    if back:
                        self.view.note("it keeps reworking the same thing: purr asked for a step back", "warn")
                        self.messages.append({"role": "user", "content": back})
                # small models can get stuck ticking their task list forever instead of
                # stopping (it never ends the turn, since a tool call always asks for more)
                if all(c["name"] == "todo" for c in reply["tool_calls"]):
                    todo_only += 1
                    if reply["text"] and not self.tools.todos_left:
                        if self._one_more_look(reply["text"]):
                            continue
                        break  # it answered and everything is done: that's the end
                    if todo_only >= 3:
                        self.view.note("it only kept updating its task list, so purr ended the turn")
                        break
                else:
                    todo_only = 0
        except Stopped as e:
            self.view.note("stopped", "warn")
            self.cut_off = e.partial  # the reply it was writing: only in the log, not the chat
        self._after_turn(turn_cost)

    def minimal_turn(self, text):
        """Minimal mode's loop, DSH's: ask the model, run its bash calls, and again, until it answers
        without one (or the step limit). No reminders, nudges, time notes or final checks."""
        if not self.title:
            self.title = " ".join(text.split())[:70]
        self._started = time.monotonic()
        self.turn_stats = _new_stats()
        self.messages.append({"role": "user", "content": text})
        turn_cost = 0.0
        cap = self._step_cap()
        try:
            for _ in range(cap):
                self.view.activity("thinking")
                try:
                    reply = self._call()
                except ApiError as e:
                    self.view.note(f"api error: {e}", "error")
                    self.failed = str(e)
                    break
                turn_cost += self._record(reply)
                self.messages.append(self._assistant_message(reply))
                self.save_log()
                if not reply["tool_calls"]:
                    break
                calls, done = reply["tool_calls"], 0
                try:
                    for c in calls:
                        if self.stopping():
                            raise Stopped
                        self.messages.append({"role": "tool", "tool_call_id": c["id"],
                                              "content": self._minimal_bash(c["name"], c["args"])})
                        done += 1
                finally:  # every tool call needs an answer, or the next request is refused
                    for c in calls[done:]:
                        self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": "cancelled"})
            else:
                self.view.note(f"stopped after {cap} steps (the step limit)", "warn")
        except Stopped as e:
            self.view.note("stopped", "warn")
            self.cut_off = getattr(e, "partial", None)
        self._after_turn(turn_cost)

    def _minimal_bash(self, name, raw_args):
        """One bash call in minimal mode: its output, ending in [exit code: N]."""
        command = _args(raw_args).get("command")
        if not isinstance(command, str) or not command.strip():
            return f"error: {name} needs a command (the only tool is bash, with one argument: command)"
        self.view.tool(f"run {command[:100]}")
        if self.shell is None:
            self.shell = minimal.BashSession(self.root)
        result = self.shell.run(command)
        for line in result.splitlines()[-4:]:
            self.view.note(line[:160])
        return result

    def _begin_turn(self, text):
        """Everything a turn starts from, in one place: the clock, the request, and every flag the
        checks and nudges below use (each runs at most once a turn)."""
        self._started = time.monotonic()
        self._time_said = set()
        self.tools.deadline = self._started + self.time_limit if self.time_limit and not self.helper else None
        self._request = text  # kept word for word through a compaction
        if self.one_shot and not self.helper:
            self._one_shot_setup(text)
        self.tools.begin_turn()
        self.turn_stats = _new_stats()
        self._repeats = {}  # (tool, args) -> how often it's been called, and the last result
        self._churn, self._churn_seen, self._step_backs = {}, {}, 0  # _check_churn
        self._learn_nudged = False  # learn mode: asked to leave a TODO(you) (_turn_content sets it)
        self._pair_handing_back = False
        self._learn_shortened = False
        self._checked = False  # the final check runs at most once a turn
        self._acted = False    # ran a command, a terminal or an MCP tool that changes things
        self._background = False  # started something that should keep running (a terminal, cmd &)
        self._hedged = False   # one-shot: told once that nobody will answer its question
        self._evidence = False  # one-shot: the second look (EVIDENCE_PASS) at most once
        self._compact_again_at = 0  # after a failed compaction: the chat length to try again at
        self._reviewed = False  # one-shot: the second reader (REVIEW) at most once

    def _turn_content(self, text):
        """Your message as the model gets it: @files attached, your own edits (pair mode), the
        TODO(you)s still open (learn mode), the time it has (with a time limit)."""
        content = self.expand(text)
        if self.mode == "pair" and not self.helper:
            yours = self.pair_changes()
            if yours:
                content += f"\n\n(purr: the user changed these files since your last turn:)\n{yours}"
        open_todos = self.learn_todos() if self.mode == "learn" and not self.helper else []
        if open_todos:  # small models lose track of what they left for the user: say it every time
            content += f"\n\n(purr: {TODO_YOU} still in the code: {', '.join(open_todos)})"
        self._learn_nudged = bool(open_todos)  # pieces already out there: no need to leave new ones
        if self.time_limit and not self.helper:
            content += "\n\n" + TIME_INTRO.format(minutes=max(1, round(self.time_limit / 60)))
            if len(self.messages) == 1:
                content += self._probe()
        return content

    def _make_room(self):
        """Before a call: trim old tool output when the context fills up, and compact when that's
        not enough."""
        ctx = self.model.get("context", 0)
        if not ctx or len(self.messages) <= 4:
            return
        used = self.context_used()
        if used > ctx * self.limits.prune_at:
            self._prune_old_tools()
            used = self.context_used()
        if used > ctx * self.limits.compact_at and len(self.messages) >= self._compact_again_at:
            try:
                done = self.compact(auto=True)
            except ApiError as e:
                # the summary call failed (a full context, a server hiccup): free what
                # can be freed without a model and carry on, rather than end the run
                self.view.note(f"compacting failed ({e}), trimming old output instead", "warn")
                done = None
            if not done:
                # nothing was freed: trim old output, and don't try again until the chat
                # has grown (every try is a long call that would fail the same way)
                self._prune_old_tools(keep=1)
                self._compact_again_at = len(self.messages) + 10

    def _record(self, reply):
        """One model call into the session's and the turn's numbers. Returns its price."""
        cost = self._count(reply["usage"])
        usage = reply["usage"] or {}
        s = self.turn_stats
        s["out"] += usage.get("completion_tokens", 0)
        s["calls"] += 1
        s["in"] += usage.get("prompt_tokens") or 0
        s["cached"] += _cached(usage)
        if reply.get("provider"):
            s["providers"][reply["provider"]] = s["providers"].get(reply["provider"], 0) + 1
        s["model_s"] += reply.get("call_seconds", 0.0)
        # Ollama sends a tool call as one piece at the very end, and a short answer is over
        # in a blink: then there's no writing time to measure, so count the whole call
        gen = reply.get("gen_seconds", 0.0)
        s["gen"] += gen if gen >= 0.3 else reply.get("call_seconds", gen)
        return cost

    def _assistant_message(self, reply):
        """The model's reply as a chat message (with its thinking, for providers that want it back)."""
        # content may only be null next to tool calls: Ollama refuses an empty reply's null
        msg = {"role": "assistant", "content": reply["text"] or (None if reply["tool_calls"] else "")}
        if reply["tool_calls"]:
            msg["tool_calls"] = [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                for c in reply["tool_calls"]]
        echo = self.provider.get("echo_reasoning")
        if echo:
            msg[echo] = reply["reasoning"]
        return msg

    def _one_more_look(self, reply_text):
        """The model wants to stop: every check that may send it back once, in order. True when one
        did (the turn goes on). One place, so a new check can't end up in only one of the stops."""
        return (self._final_check(reply_text) or self._evidence_pass() or self._review()
                or self._learn_check() or self._hedge(reply_text))

    def _after_turn(self, turn_cost):
        """The turn is over: save, show the numbers, and what's next in pair and learn mode."""
        self.save_log()
        self._status(turn_cost)
        changed = len(self.tools.undo_stack[-1]) if self.tools.undo_stack else 0
        if changed and not self.helper and (self.root / ".git").exists():
            self.view.note(f"✎ {changed} file{'s' * (changed != 1)} changed · /pr makes a pull request")
        if self.mode == "pair" and not self.helper:
            self.pair_snapshot()  # whatever changes from here on is the user's
            if self._pair_handing_back:
                self.view.note("⇄ your move: say go, steer it, or edit the code yourself and tell it", "info")
        if self.mode == "learn" and not self.helper:
            todos = self.learn_todos()
            if todos:
                self.view.note(f"✿ your turn: {', '.join(todos)} · say done when you're ready, or ask for a hint", "info")
        self.turn_stats["summary"] = self._turn_summary()
        self.view.note(self.turn_stats["summary"], "stats")

    def _final_check(self, reply=""):
        """The model wants to stop after changing files: ask it once to go through the request
        point by point first. Small models often fix the first thing, see the old tests pass and
        say "done" with half the job left (purr bench showed it). Returns True when it asked."""
        changed = self.tools.undo_stack[-1] if self.tools.undo_stack else {}
        # a one-shot run that only used commands (a model trained, a VM set up) has work to check
        # too; in a chat, "run the tests" doesn't need a second look
        acted = self._acted and self.one_shot
        if (self._checked or not (changed or acted) or self.helper or not self.coding
                or not self.config.get("final_check", True)):
            return False
        self._checked = True
        self.view.note("♡ checking the request once more before finishing")
        services = " " + SERVICES if self._background else ""
        if self.one_shot:
            left = self._minutes_left()
            check = ONE_SHOT_CHECK.format(time=f" (about {left} minutes left)" if left else "", services=services,
                                          nobody=NOBODY if self._hedges(reply) else "", scratch=self._new_files_note())
            self._hedged = self._hedged or self._hedges(reply)
        else:
            check = (FINAL_CHECK if self._helper_on("edge_cases") else FINAL_CHECK_LIGHT).format(services=services)
        self.messages.append({"role": "user", "content": self._test_report() + check})
        return True

    def _new_files_note(self):
        """The files this run created that still exist, for the check: leftovers break real tests."""
        new = [p for p, before in self.tools.session_changes().items() if before is None and p.exists()]
        if not new:
            return ""
        names = [os.path.relpath(p, self.root) if self.root in p.parents else str(p) for p in new]
        return SCRATCH_NOTE.format(files=", ".join(sorted(names)[:15]) + (" …" if len(names) > 15 else ""))

    def _change_diff(self, budget):
        """The files this run changed, as a unified diff, at most `budget` characters."""
        parts = []
        for path, before in self.tools.session_changes().items():
            try:
                after = path.read_text(errors="replace") if path.exists() else ""
            except OSError:
                continue
            name = os.path.relpath(path, self.root) if self.root in path.parents else str(path)
            diff = "".join(difflib.unified_diff((before or "").splitlines(True), after.splitlines(True),
                                                f"a/{name}", f"b/{name}", n=2))
            parts.append(diff[:max(2000, budget // 4)])
        return "".join(parts)[:budget]

    def _review(self):
        """Once, before a one-shot run finishes: a call that sees only the request and the diff.
        Returns True when it found something to look at (the turn goes on)."""
        if (not self.one_shot or self.helper or not self.coding or getattr(self, "_reviewed", True)
                or not self.config.get("review", True)):
            return False
        self._reviewed = True
        budget = min(30_000, self.limits.context)  # characters: well inside even a 32k model
        diff = self._change_diff(budget)
        if not diff.strip():
            return False
        self.view.note("♡ a second reader compares the request with the changes")
        try:
            reply = self._call([{"role": "user", "content": REVIEW.format(
                request=_ends(getattr(self, "_request", ""), 3000, 1000), diff=diff)}], tools=False, quiet=True)
        except ApiError:
            return False
        self._count(reply.get("usage"))
        findings = (reply.get("text") or "").strip()
        if not findings or "ALL MET" in findings[:200]:
            return False
        self.tools.repairs.append("second reader found unmet requirements -> sent back to check")
        self.messages.append({"role": "user", "content": REVIEW_NOTE.format(findings=findings[:3000])})
        return True

    def _evidence_pass(self):
        """After the check, a one-shot run that wants to stop with more than half its time left gets
        one more look (once). Returns True when it asked."""
        if not (self.one_shot and self._checked and self.time_limit) or self._evidence or self.helper:
            return False
        left = self._minutes_left()
        total = max(1, round(self.time_limit / 60))
        if not left or left * 2 < total:
            return False
        self._evidence = True
        self.view.note("♡ plenty of time left: one more look, with evidence for every requirement")
        self.messages.append({"role": "user", "content": EVIDENCE_PASS.format(left=left, total=total)})
        return True

    def _one_shot_setup(self, text):
        """What a task's text settles for a one-shot run: the paths it names outside the project
        folder may be written (and /tmp for scratch), and a small model gets no terminal tool
        unless the task is about something interactive (it spent steps on it for nothing)."""
        named = {Path(m) for m in ABS_PATH.findall(text)}
        self.tools.outside_ok = [Path("/tmp").resolve()] + sorted(
            p for p in named if len(p.parts) > 2 or (len(p.parts) == 2 and p.parts[1] not in SYSTEM_DIRS))
        if self._hide_terminal is None:
            self._hide_terminal = self.limits.context <= 65_536 and not INTERACTIVE.search(text)

    def _hedges(self, reply):
        """One-shot: the final reply hands a choice back ("say so and I'll rerun") to nobody."""
        return bool(self.one_shot and reply and HEDGE.search(reply[-300:]))

    def _hedge(self, reply):
        """The final check already ran and it still ends with a question: say once that nobody
        will answer. Returns True when it said so."""
        if self._hedged or not self._hedges(reply) or self.helper:
            return False
        self._hedged = True
        self.tools.repairs.append("one-shot reply asked a question -> told nobody will answer")
        self.messages.append({"role": "user", "content": "(purr:" + NOBODY + ")"})
        return True

    def _minutes_left(self):
        if not self.time_limit or self.helper:
            return None
        return max(1, round((self.time_limit - (time.monotonic() - self._started)) / 60))

    def _step_cap(self):
        """Steps before purr asks to keep going. One-shot runs can't ask, so they get at least a
        fixed cap, and never less than max_steps: a benchmark's own limit (500 in the fair runs)
        wins. Capping those at 150 cut 17 of 54 DeepSWE tasks off before they could commit."""
        steps = self.config.get("max_steps", 40)
        if self.one_shot and not self.helper:
            return max(steps, ONE_SHOT_STEPS if self.time_limit else ONE_SHOT_STEPS_FREE)
        return steps

    def _probe(self):
        """One-shot runs on a machine with no project files (benchmarks): one line on what's
        installed, mostly what's missing. "" when not wanted or it fails."""
        if not self.one_shot or self.mode != "code" or any((self.root / f).exists() for f in PROJECT_FILES):
            return ""
        purr_dir = str(Path(__file__).resolve().parent.parent)
        path = ":".join(d for d in os.environ.get("PATH", "").split(":")
                        if d and not d.startswith(purr_dir) and "/opt/purr" not in d)
        try:
            res = subprocess.run(["bash", "-c", PROBE], capture_output=True, text=True, timeout=8,
                                 cwd=self.root, env={**os.environ, "PATH": path}, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return ""
        line = res.stdout.strip().splitlines()[-1:] if res.returncode == 0 else []
        return f"\n(purr: this machine: {line[0][:300]})" if line else ""

    def _learn_check(self):
        """Learn mode: small models happily write the whole thing themselves. When a turn changed
        files and left no TODO(you) anywhere, ask once to hand the interesting part back."""
        changed = self.tools.undo_stack[-1] if self.tools.undo_stack else {}
        if self.mode != "learn" or self.helper or not changed:
            return False
        long = [(where, n) for where, n in self._todo_lengths(changed) if n > LEARN_TODO_LINES]
        if long and not self._learn_shortened:
            self._learn_shortened = True
            self.tools.repairs.append("learn mode: TODO(you) too long -> asked to shorten it")
            self.view.note("✿ asking it to keep the hint small, so you get to work it out")
            self.messages.append({"role": "user", "content": LEARN_SHORTEN.format(where=long[0][0], n=long[0][1])})
            return True
        if self._learn_nudged or self.learn_todos():
            return False
        self._learn_nudged = True
        self.tools.repairs.append("learn mode: wrote everything -> asked to leave a TODO(you)")
        self.view.note("✿ asking it to leave the interesting part for you")
        self.messages.append({"role": "user", "content": LEARN_NUDGE})
        return True

    def pair_snapshot(self):
        """Pair mode: remember the project's text files, so the next message can show the model
        what the user changed in their own editor meanwhile."""
        try:
            res = subprocess.run(["rg", "--files"], capture_output=True, text=True, timeout=10, cwd=self.root)
        except (OSError, subprocess.SubprocessError):
            self._pair_files = None
            return
        files = {}
        for rel in res.stdout.splitlines()[:PAIR_SNAPSHOT_FILES]:
            path = self.root / rel
            try:
                if path.stat().st_size < 200_000:
                    files[rel] = path.read_text()
            except (OSError, UnicodeDecodeError):
                pass
        self._pair_files = files

    def pair_changes(self):
        """What changed since pair_snapshot(), as a unified diff ("" if nothing or no snapshot yet)."""
        before = getattr(self, "_pair_files", None)
        if before is None:
            self.pair_snapshot()  # first pair message: nothing to compare with yet
            return ""
        old_files = before
        self.pair_snapshot()
        now = self._pair_files or {}
        out, more = [], []
        for rel in sorted(set(old_files) | set(now)):
            a, b = old_files.get(rel), now.get(rel)
            if a == b:
                continue
            diff = "".join(difflib.unified_diff((a or "").splitlines(True), (b or "").splitlines(True),
                                                f"a/{rel}", f"b/{rel}", n=2))
            if not diff.endswith("\n"):
                diff += "\n"
            if sum(map(len, out)) + len(diff) > PAIR_DIFF_CHARS:
                more.append(rel)
            else:
                out.append(diff if a is not None and b is not None
                           else f"{'new' if a is None else 'deleted'} file {rel}\n" + (diff if b else ""))
        if not out and not more:
            return ""
        text = "```diff\n" + "".join(out) + "```"
        if more:
            text += f"\nAlso changed (read them yourself): {', '.join(more)}"
        return text

    def _todo_lengths(self, paths):
        """[("file:line", comment lines)] for each TODO(you) in these files: the TODO line and the
        comment lines right under it."""
        out = []
        for p in paths:
            try:
                lines = Path(p).read_text().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(lines):
                if TODO_YOU not in line:
                    continue
                mark = line.strip()[:2] if line.strip()[:2] in ("//", "--") else line.strip()[:1]
                n = 1
                while i + n < len(lines) and lines[i + n].strip().startswith(mark) and mark:
                    n += 1
                out.append((f"{os.path.relpath(p, self.root)}:{i + 1}", n))
        return out

    def learn_todos(self):
        """Where the TODO(you) pieces are: ["inventory.gd:12", ...]."""
        try:
            res = subprocess.run(["rg", "-n", "--fixed-strings", "--max-count", "20", TODO_YOU, "."],
                                 capture_output=True, text=True, timeout=10, cwd=self.root)
        except (OSError, subprocess.SubprocessError):
            return []
        return [":".join(line.removeprefix("./").split(":", 2)[:2]) for line in res.stdout.splitlines()][:20]

    def _helper_on(self, key):
        """A training-wheels helper (reminders, edge_cases): config.toml decides if it's set there,
        else the model's size does (on for local models, off for API ones)."""
        return self.config[key] if key in self.config else self.limits.helpers == "full"

    def _run_tests(self, cmd, ask=True):
        """Run the project's tests: (output, exit code), or None when you said no. Plan mode doesn't
        ask (ask=False): you approved the plan, its test runs between tickets included."""
        self.view.activity("run", cmd)
        if ask:
            self.view.tool(f"run {cmd}")
            ok, _ = self.tools._run_allowed(cmd)
            if not ok:
                return None
        return run_shell(cmd, self.root, timeout=TEST_TIMEOUT)

    def _test_report(self):
        """For the final check: purr runs the project's tests itself, so the model sees the real
        result instead of trusting its own "tests pass". "" when there's nothing to run."""
        cmd = checks.test_command(self.root) if self.config.get("final_check_tests", True) else None
        if not cmd:
            return ""
        ran = self._run_tests(cmd)
        if ran is None:
            return ""
        output, code = ran
        self.view.tool_result("run", {"command": cmd}, f"{output}\n[exit code {code}]")
        tail = "\n".join(output.strip().splitlines()[-40:])
        verdict = "they pass" if code == 0 else (
            "THEY FAIL: if the failure comes from your changes or is part of the request, fix it "
            "first; if it is in code the request has nothing to do with, leave it and mention it "
            "in your answer")
        result = f"exit code {code}, {verdict}"
        if code == -1:  # stopped, not failed: something hangs, or the suite is just slow
            result = f"timed out after {TEST_TIMEOUT}s: check whether a test hangs (run one file at a time)"
        return f"(purr, not the user, ran the tests itself: `{cmd}` → {result})\n```\n{tail}\n```\n"

    def on_my_gpu(self):
        """Speed only means something for local models: an API's depends on its load, routing, ..."""
        return self.model["provider"] in LOCAL

    def tok_per_s(self):
        """Output speed this turn: tokens written / seconds spent writing them (local models only)."""
        s = getattr(self, "turn_stats", None)
        if not self.on_my_gpu() or not s or not s["out"] or s["gen"] < 0.2:
            return None
        return s["out"] / s["gen"]

    def _turn_summary(self):
        s = self.turn_stats
        parts = [ui.duration(time.monotonic() - s["start"])]
        rate = self.tok_per_s()
        if rate:
            parts.append(f"{rate:.1f} tok/s" if rate < 10 else f"{rate:.0f} tok/s")
        if s["out"]:
            parts.append(f"{ui.short(s['out'])} tokens")
        return "✓ " + " · ".join(parts)

    def _run_tools(self, calls):
        """Runs the tool calls. Returns True when you said a plain no (the turn ends)."""
        done = 0
        self.tools.halt = False
        self._executed = []  # (name, args, result) for the repeat check
        early = self._look_in_parallel(calls)  # several reads/searches at once: all at the same time
        try:
            for c in calls:
                if self.stopping():
                    raise Stopped
                if self.tools.halt:
                    result = "skipped: the user stopped to give new instructions"
                else:
                    result = early[c["id"]] if c["id"] in early else self.tools.call(c["name"], c["args"])
                    self._executed.append((c["name"], c["args"], result))
                    self._note_action(c["name"], c["args"])
                self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                done += 1
            return self.tools.halt
        except (KeyboardInterrupt, Stopped):
            # every tool call needs an answer, or the next request is rejected
            for c in calls[done:]:
                self.messages.append({"role": "tool", "tool_call_id": c["id"],
                                      "content": "cancelled: the user stopped it"})
            raise

    def _look_in_parallel(self, calls):
        """When a reply asks for several read-only tools (read_file, grep, list_files, fetch_url),
        run them together: they change nothing, and fetches or big greps add up one by one.
        Returns {call id: result}; {} when it doesn't apply (the loop then runs them in order)."""
        def plain(name):
            return TOOL_ALIASES.get(re.sub(r"[^\w]", "", name), name)
        if len(calls) < 2 or self.stopping() or not all(plain(c["name"]) in READ_ONLY for c in calls):
            return {}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(calls))) as pool:
            results = list(pool.map(lambda c: self.tools.call(c["name"], c["args"]), calls))
        return {c["id"]: r for c, r in zip(calls, results)}

    def _note_action(self, name, args):
        """Remember a turn that did things other than edit files, for the final check."""
        mcp = getattr(self.tools, "mcp", None)
        if name in ("run", "terminal") or (name not in TOOL_NAMES and mcp and not mcp.read_only(name)):
            self._acted = True
        command = str(_args(args).get("command") or "").strip()
        if name == "terminal" or command.endswith("&") or command.startswith(("nohup ", "setsid ")):
            self._background = True

    def _check_repeats(self):
        """A small model can get stuck making the same call and learning nothing.
        Count identical calls, nudge once, then stop the turn before it eats the
        whole context. Returns a message to send, "stop failed", "stop same", or None."""
        worst, worst_name = None, ""
        for name, args, result in getattr(self, "_executed", []):
            if name == "todo":  # updating the task list over and over is normal
                continue
            # reading a file again after an edit is new information, not a loop
            key = (name, _stable(args), self.tools.edit_gen if name in LOOKS else 0)
            failed = _looks_failed(result)
            rec = self._repeats.get(key)
            if rec and rec["result"] == result:
                # the same call came back byte-for-byte the same: it is going nowhere
                rec["n"] += 1
                rec["failed"] = rec["failed"] or failed
            else:
                rec = {"n": 1, "result": result, "failed": failed, "warned": False}
                self._repeats[key] = rec
            if worst is None or rec["n"] > worst["n"]:
                worst, worst_name = rec, name
        if not worst:
            return None
        # a failing call gets repeat_limit tries; one that works (re-reading a file) gets two more
        if worst["n"] >= self.limits.repeat_limit + (0 if worst["failed"] else 2):
            return "stop failed" if worst["failed"] else "stop same"
        if worst["n"] >= REPEAT_NUDGE and not worst["warned"]:
            worst["warned"] = True
            what = "keeps failing" if worst["failed"] else "returns the same thing"
            tail = ("try a different approach; nobody will answer a question" if self.one_shot else
                    "try a different approach or different arguments, or stop and tell the user what is wrong")
            return (f"(purr: you have now called `{worst_name}` with the same arguments "
                    f"{worst['n']} times and it {what}. Don't repeat it. Read the result, {tail}.)")
        return None

    def _check_churn(self):
        """Small models don't only repeat a call: they rework the same file, or rerun the same
        command with other numbers, for dozens of steps without rethinking the approach they
        picked first (Ornith-9B: one script rewritten 44 times). Past a threshold, ask for a step
        back: what's known, why it fails, a different approach. Returns the message or None."""
        if not self._helper_on("step_back") or self._step_backs >= STEP_BACK_MAX:
            return None
        for name, args, result in getattr(self, "_executed", []):
            a = _args(args)
            if name in ("edit_file", "write_file") and a.get("path") and not _looks_failed(result):
                key, limit = ("edit", str(a["path"])), STEP_BACK_EDITS
            elif name == "run" and isinstance(a.get("command"), str):
                command = a["command"].strip()
                shape = re.sub(r"\d+(?:\.\d+)?", "#", command)
                seen = self._churn_seen.setdefault(shape, set())
                if shape == command or command in seen:
                    continue  # no numbers, or the very same command (a test run again): not a tweak
                seen.add(command)
                key, limit = ("run", shape), STEP_BACK_TWEAKS
            else:
                continue
            self._churn[key] = self._churn.get(key, 0) + 1
            if self._churn[key] >= limit:
                self._step_backs += 1
                self._churn, self._churn_seen = {}, {}  # the next approach gets its own tries
                what = f"`{Path(key[1]).name}`" if key[0] == "edit" else "the numbers in the same command"
                return STEP_BACK.format(what=what, n=limit)
        return None

    def _prune_old_tools(self, keep=None):
        """Free room cheaply by eliding old tool output, keeping the newest results whole.
        Cheaper than a full compaction: no model call, and it keeps the messages."""
        keep = self.limits.keep_recent_tools if keep is None else keep
        # the stub names the call it came from, so the model knows what to redo if it needs it
        calls = {c["id"]: c["function"] for m in self.messages if m.get("role") == "assistant"
                 for c in m.get("tool_calls") or []}
        tools = [m for m in self.messages if m.get("role") == "tool"]
        # only real output counts toward the ones kept: ten "task list saved" don't protect anything
        cut, big = 0, 0
        for i in range(len(tools) - 1, -1, -1):
            content = tools[i].get("content") or ""
            if (len(content) > 400 and not content.startswith("[old output of")
                    and calls.get(tools[i].get("tool_call_id"), {}).get("name") != "todo"):
                big += 1
                if big > keep:
                    cut = i + 1
                    break
        older = tools[:cut]
        freed = 0
        for m in older:
            content = m.get("content") or ""
            if len(content) <= 400 or content.startswith("[old output of"):
                continue
            fn = calls.get(m.get("tool_call_id"), {})
            call = f"{fn.get('name', 'a tool')}({(fn.get('arguments') or '')[:160]})"
            m["content"] = (f"[old output of {call} removed to save room "
                            f"({len(content)} characters); run it again if you need it]")
            freed += len(content) - len(m["content"])
        if freed:
            self.last_usage = None  # the old count is stale; use the fresh size guess
            self._repeats = {}  # a call whose output was just removed may well be needed again
            self.view.note(f"trimmed old tool output to save room ({ui.short(freed)} characters)", "info")
        return freed

    def _status(self, turn_cost):
        if not self.last_usage:
            return
        used = self.context_used()
        ctx = self.model.get("context", 0)
        parts = [f"{self.model_name}", f"context {ui.short(used)}/{ui.short(ctx)}"]
        if self.model.get("price"):
            parts.append(f"${turn_cost:.4f} (session ${self.session_cost:.3f})")
            if is_peak(self.provider):
                parts.append("peak price")
        self.view.status("   ".join(parts))

    # ---- compacting ----

    def transcript(self, messages):
        """The chat as plain text for the summary. Long tool results are cut short
        (harder on a small context, which can't afford a long transcript)."""
        cut = min(4000, max(800, self.limits.tool_output // 6))  # 32k: ~1.1k chars, 1M: 4k
        out = []
        for m in messages:
            role, content = m.get("role"), m.get("content") or ""
            if role == "user":
                out.append(f"USER: {content}")
            elif role == "assistant":
                calls = [f"{c['function']['name']}({c['function']['arguments'][:300]})"
                         for c in m.get("tool_calls") or []]
                out.append("ASSISTANT: " + content + (f"\n[called: {'; '.join(calls)}]" if calls else ""))
            elif role == "tool":
                out.append("TOOL RESULT: " + (content if len(content) < cut else content[:cut] + " [...]"))
        text = "\n\n".join(out)
        # the whole summary request must fit too: about half the context (4 chars a token),
        # keeping the start (what the user asked for) and the most recent work
        budget = self.limits.context * 2
        if len(text) > budget:
            head = budget // 4
            text = (text[:head] + f"\n\n[... {len(text) - budget} characters of the middle left out ...]\n\n"
                    + text[-(budget - head):])
        return text

    def compact(self, auto=False):
        """Swap the chat for a summary of it. Returns (tokens before, tokens after) or None."""
        if len(self.messages) < 3:
            self.view.note("nothing to compact yet", "info")
            return None
        before = self.context_used()
        self.view.note("compacting the chat (summarising it to make room)…", "info")
        self.view.activity("compacting")
        prompt = COMPACT.format(transcript=self.transcript(self.messages[1:]))
        reply = self._call([{"role": "user", "content": prompt}], tools=False, quiet=True)
        self._count(reply["usage"])
        summary = reply["text"].strip()
        if not summary:
            self.view.note("compacting failed: the model wrote nothing", "error")
            return None
        head = "(purr: the chat got long, so it was replaced by this summary.)\n\n" + summary
        if auto:
            # in the middle of a turn: carry straight on. The request goes along word for word: a
            # summary loses the exact paths, names and limits that the result is checked against
            request = getattr(self, "_request", "")
            if request:
                n = 3000 if self.limits.context > 65536 else 2000
                head += "\n\nWhat the user asked for, word for word:\n" + _ends(request, n * 2 // 3, n // 3)
            todos = [t["text"] for t in self.tools.todo_list if t["status"] != "done"]
            if todos:
                head += "\n\nStill open on your task list: " + "; ".join(todos)
            self.messages = [self.messages[0], {"role": "user", "content": head +
                             "\n\nCarry on with the work from where you left off."}]
        else:
            self.messages = [self.messages[0], {"role": "user", "content": head},
                             {"role": "assistant", "content": "Got it, I have the summary. What next?"}]
        self.last_usage = None
        after = self.context_used()
        self.view.note(f"compacted: {ui.short(before)} → {ui.short(after)} tokens", "info")
        self.save_log()
        return before, after

    # ---- helpers (the task tool) ----

    def _helper(self, prompt):
        helper = Agent(self.config, self.root, self.model_name, SubView(self.view), helper=True, parent=self)
        helper.turn(prompt)
        answers = [m.get("content") for m in helper.messages if m.get("role") == "assistant" and m.get("content")]
        return answers[-1] if answers else "the helper didn't report anything"

    # ---- things you type ----

    def expand(self, text):
        """@path in a message attaches that file's text for the model."""
        files = []
        for raw in MENTION.findall(text):
            name = raw.rstrip(".,;:!?)")
            p = (self.root / name).resolve()
            if p.is_file() and name not in [f for f, _ in files]:
                try:
                    body = p.read_text(errors="replace")
                except OSError:
                    continue
                if len(body) > self.limits.attach_max:
                    body = body[:self.limits.attach_max] + "\n[file cut short; use read_file for the rest]"
                files.append((name, body))
        if not files:
            return text
        return text + "".join(f'\n\n<file path="{name}">\n{body}\n</file>' for name, body in files)

    def shell(self, command):
        """!command: you run it yourself; the model sees the output next time."""
        output, code = run_shell(command, self.root)
        self.messages.append({"role": "user", "content":
                              f"(I ran this myself: `{command}`, exit code {code})\n```\n"
                              f"{clip(output, self.limits.tool_output)}\n```"})
        self.save_log()
        return output, code

    def undo(self):
        return self.tools.undo()
